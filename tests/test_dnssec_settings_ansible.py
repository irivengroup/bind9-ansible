"""Exercise DNSSEC policy through Ansible 2.9 with both Jinja typing modes."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest
import yaml

ROOT=Path(__file__).resolve().parents[1]
pytestmark=pytest.mark.skipif(not shutil.which('ansible-playbook'),reason='Requires an Ansible controller')


class DnssecPolicyRunner:
    """Run only controller-side validation and assertions, with no target writes."""

    def run(self,plays,native):
        environment=dict(os.environ,ANSIBLE_CONFIG=str(ROOT/'ansible.cfg'),
                         ANSIBLE_ROLES_PATH=str(ROOT/'roles'),ANSIBLE_JINJA2_NATIVE=str(native).lower())
        with tempfile.TemporaryDirectory(prefix='bind-dnssec-policy-') as folder:
            path=Path(folder)/'policy.yml'
            path.write_text(yaml.safe_dump(plays,sort_keys=False))
            # Extra vars cannot be overwritten by set_fact. This reproduces the
            # former failure and checks that this obsolete name has no policy role.
            result=subprocess.run(['ansible-playbook','-i','localhost,',str(path),
                                   '-e','{"bind_dnssec_mode":"auto"}'],env=environment,
                                  stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                  universal_newlines=True,timeout=120)
        assert result.returncode==0,result.stdout

    @staticmethod
    def validation():
        # Expose role defaults to the following test assertions as well as to
        # the role itself; include_role otherwise keeps defaults private.
        return {'include_role':{'name':'bind9','tasks_from':'tasks.d/dnssec_settings.yml','public':True}}

    @staticmethod
    def play(value,tasks,default=False):
        return {'hosts':'localhost','connection':'local','gather_facts':False,
                'vars':{} if default else {'bind_dnssec_active':value},'tasks':tasks}


class TestDnssecPolicyRunner:
    """Protect false, auto, stale high-precedence state and invalid input refusal."""

    @pytest.mark.parametrize('native',[False,True])
    def test_public_modes_ignore_stale_high_precedence_internal_state(self,native):
        runner=DnssecPolicyRunner()
        main=yaml.safe_load((ROOT/'roles/bind9/tasks/main.yml').read_text())
        deployment=next(task for task in main if 'rescue' in task)
        imports={task['ansible.builtin.import_tasks']:task for task in deployment['block'] if 'ansible.builtin.import_tasks' in task}
        plays=[]
        for value,active,default in [(False,False,False),('false',False,False),
                                     (True,True,False),('true',True,False),('auto',True,False),(False,False,True)]:
            assertions=[]
            for name in ('dnssec_install.yml','dnssec_activate.yml','dnssec_disable.yml'):
                expected=not active if name=='dnssec_disable.yml' else active
                condition=imports['tasks.d/'+name]['when']
                assertions.append('('+condition+') == '+str(expected).lower())
            plays.append(runner.play(value,[runner.validation(),{'assert':{'that':assertions}}],default))
        runner.run(plays,native)

    @pytest.mark.parametrize('native',[False,True])
    def test_invalid_policy_is_rejected_before_any_target_operation(self,native):
        runner=DnssecPolicyRunner()
        plays=[]
        for value in (None,0,1,2,[],{},'yes','no','AUTO','enabled','auto; any;',''):
            tasks=[{'set_fact':{'test_policy_rejected':False}},
                   {'block':[runner.validation()],
                    'rescue':[{'set_fact':{'test_policy_rejected':True}}]},
                   {'assert':{'that':['test_policy_rejected is sameas true']}}]
            plays.append(runner.play(value,tasks))
        runner.run(plays,native)
