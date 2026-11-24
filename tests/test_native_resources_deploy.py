"""Native deployment customization transport with genuine BIND validation."""
import json
import os
from pathlib import Path

from ansible.plugins.loader import module_loader, module_utils_loader
import pytest

from native_ansible_runner import NativeFilesystemScope, NativeProvisionRun, NativeUnitBoundary
from test_management import CONF, ROOT_ZONE, ZONE
from test_native_directories import named_identity

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires disconnected native Linux root and the real BIND checker')


class NativeResourcesRun(NativeProvisionRun):
    """Admit only the inspected module and its guarded named.conf publication."""

    FILES = {'tasks.d/deploy/customization.yml'}
    TARGETS = ('/etc/named.conf', '/var/named', '/var/log/named', '/usr/local/bin/systemctl',
               '/usr/share/named', '/etc/named.rfc1912.zones')
    SAFE_ACTIONS = NativeProvisionRun.SAFE_ACTIONS | {'bind9_deploy_resources'}

    def execute_native(self, runner):
        module_loader.add_directory(str(self.role / 'library'))
        module_utils_loader.add_directory(str(self.role / 'module_utils'))
        return super().execute_native(runner)


@pytest.fixture
def deployment(tmp_path):
    with NativeFilesystemScope(NativeResourcesRun):
        named_identity()
        Path('/var/named/zones').mkdir(parents=True)
        Path('/var/log/named').mkdir(parents=True)
        Path('/etc/named.conf').write_text(CONF)
        Path('/etc/named.conf').chmod(0o640)
        Path('/var/named/empty-root.zone').write_text(ROOT_ZONE)
        Path('/var/named/zones/example.test.zone').write_text(ZONE)
        with NativeUnitBoundary(tmp_path, {'named': {'active': True, 'enabled': True}}) as units:
            yield units


def run(operations, checks=(), major='8', secondary=False):
    hosts = dict(localhost=dict(bind_front_ip='192.0.2.11', bind_back_ip='10.0.0.11'),
                 peer=dict(bind_front_ip='192.0.2.12', bind_back_ip='10.0.0.12'))
    return NativeResourcesRun('tasks.d/deploy/customization.yml', dict(
        _bind_deploy_extra_operations=operations, ansible_distribution_major_version=major,
        bind_primary_host='peer' if secondary else 'localhost',
        bind_secondary_host='localhost' if secondary else 'peer', bind_tsig_key_name='site-transfer'),
        checks=checks, inventory_hosts=hosts, inventory_groups=dict(
            dns_primary=['peer' if secondary else 'localhost'],
            dns_secondary=['localhost' if secondary else 'peer'])).run()


@pytest.mark.parametrize('major', ['7', '8', '9', '10'])
@pytest.mark.parametrize('secondary', [False, True])
def test_native_options_acl_logging_use_real_checker_and_atomic_copy(deployment, major, secondary):
    if secondary:
        Path('/var/named/slaves').mkdir()
        Path('/etc/named.conf').write_text(CONF.replace(
            'type master; file "zones/example.test.zone"; allow-update { none; };',
            'type slave; file "slaves/example.test.zone"; masters { 10.0.0.12; };'))
    operations = [dict(resource='option', action='update', name='minimal-responses', value='no'),
        dict(resource='acl', action='create', name='monitoring', value='192.0.2.40;'),
        dict(resource='logging', action='create', name='logging',
             value='channel discard { null; }; category default { discard; };')]
    status, result = run(operations, ["_bind_deploy_customization.changed == false",
                                    "_bind_deploy_customization.content is search('acl')"], major, secondary)
    assert status == 0 and not result.failed
    assert 'minimal-responses no;' in Path('/etc/named.conf').read_text()
    assert 'acl "monitoring"' in Path('/etc/named.conf').read_text()
    assert 'logging {' in Path('/etc/named.conf').read_text()
    assert Path('/etc/named.conf').stat().st_mode & 0o777 == 0o640
    assert 'censored' in result.registers['_bind_deploy_customization']
    assert '_bind_deploy_customization' not in result.facts
    assert not Path('/var/backups/Bind9-Ansible').exists()
    # The unchanged canonical handler reloads systemd's unit definitions first.
    assert json.loads(deployment.read_text())['operations'] == [
        {'daemon_reload': True}, {'name': 'named', 'state': 'reloaded'}]


@pytest.mark.parametrize('operations', [[],
    [dict(resource='acl', action='create', name='monitoring', value='missing_acl;')],
    [dict(resource='option', action='update', name='minimal-responses', value='not-a-boolean')],
    [dict(resource='option', action='update', name='allow-query', value='{ any; }')],
    [dict(resource='logging', action='create', name='logging', value='channel bad { file "/etc/named.conf"; };')]])
def test_native_empty_request_and_refusals_preserve_generated_baseline(deployment, operations):
    status, result = run(operations)
    assert status == (2 if operations else 0)
    assert result.failed == (['Validate custom resources against the generated baseline and native includes'] if operations else [])
    assert Path('/etc/named.conf').read_text() == CONF
    assert json.loads(deployment.read_text())['operations'] == []


@pytest.mark.parametrize('fault', [None, 'wrong-link', 'writable-target'])
def test_native_customization_reads_only_the_exact_protected_RPM_zone_link(deployment, fault):
    target = Path('/usr/share/named/named.localhost')
    target.parent.mkdir(mode=0o755)
    target.write_text(ROOT_ZONE)
    target.chmod(0o664 if fault == 'writable-target' else 0o644)
    link = Path('/var/named/named.localhost')
    link.symlink_to('../../usr/share/named/' + ('named.empty' if fault == 'wrong-link' else 'named.localhost'))
    Path('/etc/named.rfc1912.zones').write_text('zone "localhost" { type master; file "named.localhost"; };\n')
    baseline = CONF + 'include "/etc/named.rfc1912.zones";\n'
    Path('/etc/named.conf').write_text(baseline)
    before = (link.lstat().st_ino, target.stat().st_ino, target.read_bytes())
    status, result = run([dict(resource='option', action='update', name='minimal-responses', value='no')], major='10')
    assert status == (2 if fault else 0)
    assert result.failed == (['Validate custom resources against the generated baseline and native includes'] if fault else [])
    assert before == (link.lstat().st_ino, target.stat().st_ino, target.read_bytes())
    assert Path('/etc/named.conf').read_text() == (baseline if fault else baseline.replace('minimal-responses yes;', 'minimal-responses no;'))
    assert len(json.loads(deployment.read_text())['operations']) == (0 if fault else 2)
