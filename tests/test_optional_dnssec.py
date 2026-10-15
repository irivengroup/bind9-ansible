"""Protect the tri-state setting, default-off rendering and lifecycle gates."""
from pathlib import Path

from jinja2 import Environment, StrictUndefined
import pytest
import yaml

from route_task_expressions import TaskExpressions


class TestOptionalDnssec:
    """Use production task expressions instead of Python truthiness guesses."""

    ROOT=Path(__file__).resolve().parents[1]
    ROLE=ROOT/'roles/bind9'

    @pytest.mark.parametrize('value,mode,enabled',[
        (False,'disabled',False),('false','disabled',False),(True,'enabled',True),
        ('true','enabled',True),('auto','auto',True)])
    def test_explicit_modes_are_normalized_without_yes_no_fact_coercion(self,value,mode,enabled):
        result=TaskExpressions({'bind_dnssec_enabled':value}).run('dnssec_settings.yml')
        assert result['bind_dnssec_mode']==mode
        assert result['bind_dnssec_active'] is enabled

    @pytest.mark.parametrize('value',[None,0,1,2,[],{},'yes','no','AUTO','enabled','auto; any;',''])
    def test_unknown_values_cannot_silently_enable_or_disable_validation(self,value):
        with pytest.raises(ValueError):
            TaskExpressions({'bind_dnssec_enabled':value}).run('dnssec_settings.yml')

    @pytest.mark.parametrize('mode,directive,anchor',[
        ('disabled','no',False),('enabled','yes',True),('auto','auto',True)])
    def test_template_has_no_dnssec_assets_or_markers_in_disabled_mode(self,mode,directive,anchor):
        context=dict(bind_dnssec_mode=mode,bind_allowed_clients=['192.0.2.0/24'],
                     bind_cloud_clients=['192.0.2.0/24'],bind_internal_clients=[],
                     bind_primary_host='p',bind_secondary_host='s',group_names=['dns_primary'],
                     hostvars={'p':{'bind_back_ip':'10.0.0.11'},'s':{'bind_back_ip':'10.0.0.12'}},
                     bind_front_ip='192.0.2.11',bind_back_ip='10.0.0.11',
                     bind_tsig_key_name='test-transfer',bind_tsig_algorithm='hmac-sha256',
                     bind_tsig_secret='render-only',bind_authoritative_zones=[],
                     bind_forward_zones=[{'domain':'private.test','dnssec_validate':False,
                                          'forwarders':[{'address':'10.11.0.53'}]}])
        output=Environment(undefined=StrictUndefined).from_string((self.ROLE/'templates/named.conf.j2').read_text()).render(**context)
        assert 'dnssec-validation '+directive+';' in output
        assert ('managed-keys-directory' in output) is anchor
        assert ('/etc/named.root.key' in output) is anchor
        assert ('DNSSEC-PRIVATE private.test' in output) is anchor
        assert ('include "/etc/named.root.key";' in output) is (mode=='enabled')
        assert ('bindkeys-file' in output) is (mode=='auto')
        assert 'include "/etc/named.rfc1912.zones";' in output

    def test_default_is_false_and_dnssec_installation_and_activation_are_gated(self):
        defaults=yaml.safe_load((self.ROLE/'defaults/main.yml').read_text())
        assert defaults['bind_dnssec_enabled'] is False
        tasks=yaml.safe_load((self.ROLE/'tasks/main.yml').read_text())
        assert tasks[0]['ansible.builtin.import_tasks']=='tasks.d/dnssec_settings.yml'
        assert tasks[0]['tags']==['always']
        deployment=next(task for task in tasks if 'rescue' in task)
        imports={task['ansible.builtin.import_tasks']:task for task in deployment['block'] if 'ansible.builtin.import_tasks' in task}
        for name in ('dnssec_install.yml','dnssec_activate.yml'):
            assert imports['tasks.d/'+name]['when']=='bind_dnssec_active'
        assert imports['tasks.d/dnssec_disable.yml']['when']=='not bind_dnssec_active'
        assert 'when' not in imports['tasks.d/named_runtime.yml']

    def test_ipv4_and_administrative_reload_do_not_depend_on_dnssec(self):
        common=(self.ROLE/'templates/named-runtime-override.j2').read_text()
        hook=(self.ROLE/'templates/named-dnssec-override.j2').read_text()
        assert 'named -4' in common and 'rndc -s 127.0.0.1' in common
        assert 'bind-private-dnssec' not in common and 'ExecStartPost' not in common
        assert 'ExecStartPost=' in hook and 'ExecReload=' in hook
        assert 'ExecReload=\n' not in hook  # Append after the common RNDC reload.

    def test_rollback_restores_runtime_files_before_restarting_named(self):
        main=yaml.safe_load((self.ROLE/'tasks/main.yml').read_text())
        deployment=next(task for task in main if 'rescue' in task)
        snapshot=next(t for t in main if t.get('ansible.builtin.import_tasks')=='tasks.d/runtime_snapshot.yml')
        assert main.index(snapshot)<main.index(deployment)
        names=[task['name'] for task in deployment['rescue']]
        assert names.index('Stop named before restoring files and journals') < names.index('Restore managed runtime files from before the failed deployment') < names.index('Restart restored BIND configuration')
        assert 'Record whether the private DNSSEC timer was active' not in names
        snapshot=(self.ROLE/'tasks/tasks.d/runtime_snapshot.yml').read_text()
        assert 'systemctl, is-active' in snapshot and 'systemctl, is-enabled' in snapshot
