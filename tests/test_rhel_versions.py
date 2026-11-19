"""Exercise the real version gates, package choices and native template profiles."""

from pathlib import Path

from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar
from jinja2 import Environment, StrictUndefined
import pytest
import yaml

from route_task_expressions import TaskExpressions
from test_ansible_canonical_predicates import CanonicalPredicates
from test_validation_guard_contracts import settings

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / 'roles/bind9/tasks/tasks.d'


@pytest.mark.parametrize('major', ['7', '8', '9', '10', '11', '99'])
def test_deployment_and_management_admit_redhat_major_versions_from_seven(major):
    values = settings()
    values.update(ansible_distribution_major_version=major,
                  bind_manage=dict(resource='option', action='read', name='minimal-responses'))
    CanonicalPredicates('deploy/validate.yml').check('Validate supported platform and mandatory inputs', values, True)
    CanonicalPredicates('manage/main.yml').check('Validate the management platform and explicit request envelope', values, True)


@pytest.mark.parametrize('distribution,major', [('RedHat', '6'), ('RedHat', '0'),
    ('RedHat', 'invalid'), ('Debian', '12'), ('Rocky', '9')])
def test_platform_expansion_preserves_unsupported_host_refusals(distribution, major):
    values = settings()
    values.update(ansible_distribution=distribution, ansible_distribution_major_version=major,
                  bind_manage=dict(resource='option', action='read', name='minimal-responses'))
    CanonicalPredicates('deploy/validate.yml').check('Validate supported platform and mandatory inputs', values, False)
    CanonicalPredicates('manage/main.yml').check('Validate the management platform and explicit request envelope', values, False)


@pytest.mark.parametrize('major,backend,policy', [('7', 'yum', 'policycoreutils-python'),
    ('8', 'dnf', 'policycoreutils-python-utils'), ('9', 'dnf', 'policycoreutils-python-utils'),
    ('10', 'dnf', 'policycoreutils-python-utils'), ('11', 'dnf', 'policycoreutils-python-utils')])
def test_dependencies_select_native_package_names_without_changing_security_requirements(major, backend, policy):
    task = next(item for item in yaml.safe_load((TASKS / 'deploy/packages.yml').read_text())
                if 'ansible.builtin.package' in item)
    variables = {'ansible_distribution_major_version': major}
    args = Templar(loader=DataLoader(), variables=variables).template(task['ansible.builtin.package'])
    assert args == dict(use=backend, name=['bind', 'bind-utils', 'python3', policy], state='present')


@pytest.mark.parametrize('major,interpreter,expected', [
    ('7', '/usr/bin/python3', '/usr/bin/python2'),
    ('8', '/usr/libexec/platform-python', '/usr/libexec/platform-python'),
    ('9', '/opt/python3', '/opt/python3'), ('10', '/usr/bin/python3', '/usr/bin/python3')])
def test_package_manager_uses_its_own_bindings_and_preserves_modern_interpreters(major, interpreter, expected):
    task = TaskExpressions.load('deploy/packages.yml')[0]
    values = dict(ansible_distribution_major_version=major, ansible_python_interpreter=interpreter)
    assert TaskExpressions(values).run('', tasks=[task])['_bind_package_python'] == expected


@pytest.mark.parametrize('major,version,interpreter,expected', [
    ('7', '2.7.5', '/usr/bin/python', '/usr/bin/python3'),
    ('7', '3.6.8', '/opt/python3', '/opt/python3'),
    ('8', '3.6.8', '/usr/libexec/platform-python', '/usr/libexec/platform-python'),
    ('9', '3.9.21', '/usr/bin/python3', '/usr/bin/python3'),
    ('10', '3.12.9', '/usr/bin/python3', '/usr/bin/python3'),
])
def test_management_uses_python_three_on_legacy_targets_and_preserves_selected_modern_runtimes(major, version, interpreter, expected):
    tasks = TaskExpressions.load('manage/main.yml')
    task = next(item for item in tasks if item['name'] == 'Select the Python runtime required by the private management module')
    variables = dict(ansible_distribution_major_version=major, ansible_python_version=version,
                     ansible_python_interpreter=interpreter)
    assert TaskExpressions(variables).run('manage/main.yml', tasks=[task])['_bind_management_python'] == expected


@pytest.mark.parametrize('major', ['7', '8', '9', '10', '11'])
@pytest.mark.parametrize('controller', ['plain', 'ansible'])
def test_template_preserves_native_crypto_policy_where_the_platform_provides_it(major, controller):
    values = settings()
    values['ansible_distribution_major_version'] = major
    source = (ROOT / 'roles/bind9/templates/named.conf.j2').read_text()
    if controller == 'plain':
        output = Environment(undefined=StrictUndefined).from_string(source).render(**values)
    else:
        output = Templar(loader=DataLoader(), variables=values).template(source, convert_data=False)
    assert ('include "/etc/crypto-policies/back-ends/bind.config";' in output) is (int(major) >= 8)
    assert 'include "/etc/named.rfc1912.zones";' in output
    assert 'bindkeys-file "/etc/named.root.key";' in output
    assert 'dnssec-validation auto;' in output
    assert 'allow-transfer { none; };' in output
