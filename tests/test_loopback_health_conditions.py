"""Evaluate deployment health conditions through Ansible, including Jinja escapes."""
from pathlib import Path

from jinja2 import Environment, nodes
import pytest
import yaml

pytest.importorskip('ansible')
from route_task_expressions import TaskExpressions

ROOT = Path(__file__).resolve().parents[1]
TASK = yaml.safe_load((ROOT / 'roles/bind9/tasks/tasks.d/health.yml').read_text())[0]
ITEMS = TASK['loop']


class TestLoopbackHealthConditions:
    """Require a real authoritative exact answer rather than dig's zero exit alone."""

    @staticmethod
    def output(item, flags='qr aa'):
        answer = '127.0.0.1' if item['type'] == 'A' else 'localhost.'
        return (';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\n'
                ';; flags: ' + flags + '; QUERY: 1, ANSWER: 1, AUTHORITY: 0, ADDITIONAL: 0\n'
                + item['name'] + '.\t86400\tIN\t' + item['type'] + '\t' + answer + '\n')

    @staticmethod
    def check(item, output, rc=0, healthy=True):
        scope = {'item': item, 'bind_loopback_check': {'stdout': output, 'rc': rc}}
        evaluator = TaskExpressions(scope)
        assert evaluator.condition([TASK['until']], scope) is healthy
        assert evaluator.condition([TASK['failed_when']], scope) is not healthy

    @pytest.mark.parametrize('item', ITEMS)
    @pytest.mark.parametrize('flags', ['qr aa', 'aa', 'qr aa rd', 'qr rd aa'])
    def test_authoritative_native_answers_are_accepted(self, item, flags):
        # The old Jinja condition rejected all of these because \b became 0x08.
        self.check(item, self.output(item, flags))

    @pytest.mark.parametrize('item', ITEMS)
    @pytest.mark.parametrize('scenario', ['command_error', 'empty', 'servfail', 'refused',
                                        'no_aa', 'aa_prefix', 'aa_suffix', 'aa_after_flags',
                                        'wrong_owner', 'wrong_type', 'wrong_answer', 'answer_suffix'])
    def test_invalid_or_non_authoritative_results_still_fail(self, item, scenario):
        output = self.output(item)
        rc = 0
        if scenario == 'command_error':
            rc = 9
        elif scenario == 'empty':
            output = ''
        elif scenario in ('servfail', 'refused'):
            output = output.replace('NOERROR', scenario.upper())
        elif scenario in ('no_aa', 'aa_prefix', 'aa_suffix', 'aa_after_flags'):
            flags = {'no_aa': 'qr ra', 'aa_prefix': 'qr xaa', 'aa_suffix': 'qr aaa',
                     'aa_after_flags': 'qr; aa'}[scenario]
            output = self.output(item, flags)
        elif scenario == 'wrong_owner':
            output = output.replace(item['name'] + '.\t', 'other.' + item['name'] + '.\t')
        elif scenario == 'wrong_type':
            output = output.replace('\tIN\t' + item['type'] + '\t', '\tIN\tTXT\t')
        elif scenario == 'wrong_answer':
            expected = '127.0.0.1' if item['type'] == 'A' else 'localhost.'
            output = output.replace('\t' + expected + '\n', '\twrong.invalid.\n')
        elif scenario == 'answer_suffix':
            output = output.rstrip() + '1\n'
        self.check(item, output, rc=rc, healthy=False)


class TestOtherDnsHealthConditions:
    """Cover SOA readiness, reconciled serials and forwarding through real Jinja."""

    @pytest.mark.parametrize('kind', ['primary_readiness', 'authoritative', 'forward'])
    @pytest.mark.parametrize('scenario', ['valid', 'command_error', 'empty', 'servfail', 'refused',
                                        'wrong_owner', 'wrong_type', 'no_aa', 'aa_prefix',
                                        'aa_suffix', 'aa_after_flags', 'wrong_serial'])
    def test_other_query_conditions(self, kind, scenario):
        tasks = ROOT / 'roles/bind9/tasks/tasks.d'
        if kind == 'primary_readiness':
            task = yaml.safe_load((tasks / 'primary_readiness.yml').read_text())[1]
            register = 'bind_primary_soa'
        else:
            task = next(t for t in yaml.safe_load((tasks / 'health.yml').read_text())
                        if t.get('register') == ('bind_soa_checks' if kind == 'authoritative' else 'bind_forward_health'))
            register = task['register']
        output = (';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\n'
                  ';; flags: qr aa; QUERY: 1, ANSWER: 1\n'
                  'example.test.\t300\tIN\tSOA\tns.example.test. hostmaster.example.test. 12 3600 900 604800 60\n')
        rc = 9 if scenario == 'command_error' else 0
        if scenario == 'empty':
            output = ''
        elif scenario in ('servfail', 'refused'):
            output = output.replace('NOERROR', scenario.upper())
        elif scenario == 'wrong_owner':
            output = output.replace('example.test.\t', 'other.example.test.\t')
        elif scenario == 'wrong_type':
            output = output.replace('\tSOA\t', '\tTXT\t')
        elif scenario in ('no_aa', 'aa_prefix', 'aa_suffix', 'aa_after_flags'):
            output = output.replace('qr aa;', {'no_aa': 'qr ra;', 'aa_prefix': 'qr xaa;',
                                              'aa_suffix': 'qr aaa;', 'aa_after_flags': 'qr; aa;'}[scenario])
        elif scenario == 'wrong_serial':
            output = output.replace(' 12 ', ' 13 ')
        # Recursive forwarder answers need not be authoritative, nor have a
        # locally reconciled serial; primary readiness only requires a SOA.
        healthy = (scenario == 'valid' or
                   (kind == 'forward' and scenario in ('no_aa', 'aa_prefix', 'aa_suffix', 'aa_after_flags')) or
                   (kind != 'authoritative' and scenario == 'wrong_serial'))
        domain = {'domain': 'example.test'}
        scope = {register: {'stdout': output, 'rc': rc},
                 'item': [domain, '+tcp'] if kind == 'forward' else domain,
                 'bind_expected_soa_serials': {'example.test': 12}}
        evaluator = TaskExpressions(scope)
        assert evaluator.condition([task['failed_when']], scope) is not healthy
        if 'until' in task:
            assert evaluator.condition([task['until']], scope) is healthy


class TestDeployedConfigurationConditions:
    """Check the real post-install assertion against BIND's multiline format."""

    @pytest.mark.parametrize('configuration,safe', [
        ('options { allow-transfer { none; }; };', True),
        ('options {\n\tallow-transfer {\n\t\tany;\n\t};\n};', False),
        ('options {\n\tallow-transfer {\n\t\t"any";\n\t};\n};', False),
        ('options { allow-transfer { any; }; };', False),
        ('zone "example.test" {\nallow-transfer {\nany;\n};\n};', False),
        ('allow-transfer { !{ !bind_secondary_back_ip; any; }; key "transfer"; };', True),
        ('allow-transfer { 192.0.2.2; };', True),
    ])
    def test_direct_wildcard_transfer_is_rejected_in_canonical_output(self, configuration, safe):
        tasks = yaml.safe_load((ROOT / 'roles/bind9/tasks/tasks.d/verify.yml').read_text())
        task = next(t for t in tasks if 'ansible.builtin.assert' in t)
        scope = {'bind_checked_configuration': {'stdout': configuration}}
        assert TaskExpressions(scope).condition(task['ansible.builtin.assert']['that'], scope) is safe

    def test_role_conditions_do_not_decode_accidental_control_characters(self):
        # Parse with the installed Jinja itself; raw text searches missed the
        # old AA expression, whose source looked like a valid Python regex.
        def conditions(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in ('when', 'until', 'failed_when', 'changed_when', 'that'):
                        for expression in child if isinstance(child, list) else [child]:
                            if isinstance(expression, str):
                                yield expression
                    yield from conditions(child)
            elif isinstance(value, list):
                for child in value:
                    yield from conditions(child)

        environment = Environment()
        for path in (ROOT / 'roles/bind9').rglob('*.yml'):
            for expression in conditions(yaml.safe_load(path.read_text())):
                syntax = environment.parse('{% if ' + expression + ' %}ok{% endif %}')
                for constant in syntax.find_all(nodes.Const):
                    if isinstance(constant.value, str):
                        assert not set(constant.value) & set('\x00\x07\x08\x0b\x0c'), (str(path), expression)
