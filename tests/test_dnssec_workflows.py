"""Execute DNSSEC file transitions with Ansible; only systemd is a contract double."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

ROOT=Path(__file__).resolve().parents[1]
pytestmark=pytest.mark.skipif(os.name!='posix' or not shutil.which('ansible-playbook') or
                             (hasattr(os,'geteuid') and os.geteuid()!=0),reason='Requires Linux root and Ansible; no target server is contacted')


class DnssecRuntimeSandbox:
    """Rebase managed files and adapt systemd calls, leaving role logic intact."""

    def __init__(self,folder):
        self.folder=folder
        self.tree=folder/'target'
        self.role=folder/'roles/bind9'
        shutil.copytree(ROOT/'roles/bind9',self.role,ignore=shutil.ignore_patterns('__pycache__'))
        self.tree.joinpath('etc').mkdir(parents=True)
        self.tree.joinpath('usr/local/sbin').mkdir(parents=True)
        self.tree.joinpath('etc/rndc.key').write_text('public laboratory key fixture\n')
        self.state=folder/'units.json'
        self.state.write_text(json.dumps({'units':{'named':{'active':True,'enabled':True},
            'named-setup-rndc.service':{'active':True,'enabled':False}},'operations':[]}))
        binary=folder/'python-test'
        binary.symlink_to(sys.executable)
        double=ROOT/'tests/fixtures/systemd_contract.py'
        self.argv=[str(binary),str(double),str(self.state)]
        for path in list((self.role/'tasks').rglob('*.yml'))+list((self.role/'handlers').glob('*.yml')):
            text=path.read_text()
            for prefix in ('/etc/','/usr/local/sbin/'):
                text=text.replace(prefix,str(self.tree)+prefix)
            tasks=yaml.safe_load(text)
            self.adapt(tasks)
            path.write_text(yaml.safe_dump(tasks,sort_keys=False))
        main=yaml.safe_load((self.role/'tasks/tasks.d/deploy/main.yml').read_text())
        deployment=next(t for t in main if 'rescue' in t)
        selected={'tasks.d/named_runtime.yml',
                  'tasks.d/dnssec_disable.yml','tasks.d/dnssec_install.yml','tasks.d/dnssec_activate.yml'}
        block=[t for t in deployment['block'] if t.get('ansible.builtin.import_tasks', '').replace('{{ role_path }}/tasks/', '') in selected]
        block.append({'name':'Inject a post-configuration failure','ansible.builtin.fail':{'msg':'Controlled runtime rollback test'},'when':'test_rollback'})
        names={'Inspect managed renewal units before DNS restoration','Pause private DNSSEC renewal during restoration',
               'Stop named before restoring files and journals','Restore managed runtime files from before the failed deployment',
               'Restart restored BIND configuration','Resume previously active private DNSSEC renewal'}
        rescue=[t for t in deployment['rescue'] if t['name'] in names]
        rescue.append({'ansible.builtin.set_fact':{'test_runtime_restored':True}})
        snapshot=next(t for t in main if t.get('ansible.builtin.import_tasks')=='{{ role_path }}/tasks/tasks.d/runtime_snapshot.yml')
        program=[{'ansible.builtin.import_tasks':'tasks.d/dnssec_settings.yml'}, snapshot,
                 {'name':'Exercise actual runtime transaction','block':block,'rescue':rescue}]
        (self.role/'tasks/test_runtime.yml').write_text(yaml.safe_dump(program,sort_keys=False))
        config=folder/'ansible.cfg'
        config.write_text('[defaults]\nretry_files_enabled=False\nhost_key_checking=False\n')
        self.environment=dict(os.environ,ANSIBLE_CONFIG=str(config),ANSIBLE_ROLES_PATH=str(folder/'roles'))
        self.interpreter=binary

    def adapt(self,value):
        """Substitute unit transport only; retain when/loop/vars from real YAML."""
        if isinstance(value,list):
            for item in value:
                self.adapt(item)
        elif isinstance(value,dict):
            if 'ansible.builtin.systemd' in value:
                request=value.pop('ansible.builtin.systemd')
                value.setdefault('vars',{})['test_unit_request']=request
                value['ansible.builtin.command']={'argv':self.argv+['{{ test_unit_request | to_json }}']}
                value['register']='test_unit_response'
                value['changed_when']='(test_unit_response.stdout | from_json).changed | bool'
            elif 'ansible.builtin.command' in value:
                argv=value['ansible.builtin.command'].get('argv',[])
                if argv and argv[0]=='systemctl' and argv[1] in ('is-active','is-enabled'):
                    value['ansible.builtin.command']['argv']=self.argv+argv[1:]
            for child in list(value.values()):
                self.adapt(child)

    def assets(self):
        """Compare real contents and permissions, excluding only the stable RNDC fixture."""
        return {p.relative_to(self.tree).as_posix():(p.read_bytes(),p.stat().st_mode&0o777)
                for p in self.tree.rglob('*') if p.is_file() and p.name!='rndc.key'}

    def run(self,mode='__default__',rollback=False):
        play=[{'hosts':'localhost','connection':'local','gather_facts':False,
               'vars':{'ansible_python_interpreter':str(self.interpreter),'bind_dnssec_active':mode,
                       'test_rollback':rollback,'bind_rollback':{'rc':0}},
               'tasks':[{'include_role':{'name':'bind9','tasks_from':'test_runtime.yml'}},
                        {'assert':{'that':['test_runtime_restored | default(false) == test_rollback']}}]}]
        if mode == '__default__':
            play[0]['vars'].pop('bind_dnssec_active')
        path=self.folder/'play.yml'
        path.write_text(yaml.safe_dump(play))
        result=subprocess.run(['ansible-playbook','-i','localhost,',str(path)],env=self.environment,
                              stdout=subprocess.PIPE,stderr=subprocess.STDOUT,universal_newlines=True,timeout=180)
        assert result.returncode==0,result.stdout
        return json.loads(self.state.read_text())


class TestDnssecRuntime:
    def test_unsafe_snapshot_aborts_before_any_service_change(self,tmp_path):
        sandbox=DnssecRuntimeSandbox(tmp_path)
        original_state=sandbox.state.read_bytes()
        outside=tmp_path/'unmanaged.conf'
        outside.write_text('Unmanaged configuration must remain untouched.\n')
        directory=sandbox.tree/'etc/systemd/system/named.service.d'
        directory.mkdir(parents=True,exist_ok=True)
        managed=directory/'bind9-runtime.conf'
        managed.symlink_to(outside)
        # Refusing an unsafe path must not enter the service-changing rescue block.
        with pytest.raises(AssertionError,match='Refuse unsafe managed runtime paths'):
            sandbox.run(False)
        assert sandbox.state.read_bytes()==original_state
        assert managed.is_symlink()
        assert outside.read_text()=='Unmanaged configuration must remain untouched.\n'

    def test_disabling_retires_the_legacy_combined_dropin(self,tmp_path):
        sandbox=DnssecRuntimeSandbox(tmp_path)
        sandbox.run('auto')
        directory=sandbox.tree/'etc/systemd/system/named.service.d'
        (directory/'bind9-runtime.conf').unlink()
        (directory/'bind9-z-dnssec.conf').rename(directory/'bind9-dnssec.conf')
        sandbox.run(False)
        assert set(sandbox.assets())=={'etc/systemd/system/named.service.d/bind9-runtime.conf'}

    @pytest.mark.parametrize('mode',[False,True,'auto','__default__'])
    def test_fresh_modes_and_file_idempotence(self,tmp_path,mode):
        sandbox=DnssecRuntimeSandbox(tmp_path)
        state=sandbox.run(mode)
        if mode == '__default__':
            assert state['units']['named-private-dnssec.timer'] == {'active':True,'enabled':True}
        files=sandbox.assets()
        paths=set(files)
        assert 'etc/systemd/system/named.service.d/bind9-runtime.conf' in paths
        dnssec={p for p in paths if 'dnssec' in p}
        assert bool(dnssec) is (mode is not False)
        assert 'etc/systemd/system/named.service.d/bind9-dnssec.conf' not in paths
        sandbox.run(mode)
        assert sandbox.assets()==files

    def test_enabled_to_disabled_retires_hooks_units_and_helper(self,tmp_path):
        sandbox=DnssecRuntimeSandbox(tmp_path)
        sandbox.run('auto')
        state=sandbox.run(False)
        assert not any('dnssec' in p for p in sandbox.assets())
        timer=state['units']['named-private-dnssec.timer']
        assert not timer['active'] and not timer['enabled']

    @pytest.mark.parametrize('before,after',[(False,'auto'),('auto',False)])
    def test_failed_mode_transition_restores_exact_files_and_timer_state(self,tmp_path,before,after):
        sandbox=DnssecRuntimeSandbox(tmp_path)
        old=sandbox.run(before)
        # Match the production timer's enabled/active pre-deployment state.
        if before is not False:
            old['units']['named-private-dnssec.timer']={'active':True,'enabled':True}
            sandbox.state.write_text(json.dumps(old))
        files=copy.deepcopy(sandbox.assets())
        restored=sandbox.run(after,rollback=True)
        assert sandbox.assets()==files
        timer=restored['units'].get('named-private-dnssec.timer',{'active':False,'enabled':False})
        assert timer['active'] is (before is not False)
        assert timer['enabled'] is (before is not False)
