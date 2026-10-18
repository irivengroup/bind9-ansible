"""Security regressions for the 2026-10 remediation."""
import importlib.util
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path('roles/bind9')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_invalid_forwarder_and_record_rejected():
    module = load('acl_security', ROOT / 'filter_plugins/client_acl.py')
    assert module.valid_ip_address('10.0.0.53')
    assert not module.valid_ip_address('10.0.0.53; allow-transfer { any; };')
    assert module.valid_dns_record({'type': 'A', 'value': '192.0.2.3'})
    assert not module.valid_dns_record({'type': 'A', 'value': 'bad-ip'})
    assert not module.valid_dns_record({'type': 'AAAA', 'value': '192.0.2.3'})
    assert not module.valid_dns_record({'type': 'TXT', 'value': 'foo\nbar'})


def test_ipv4_transport_does_not_disable_ipv6_record_data():
    """IPv6 endpoints are refused; native AAAA data still travels over IPv4."""
    module = load('acl_ipv4', ROOT / 'filter_plugins/client_acl.py')
    for value, expected in [('192.0.2.1', True), ('2001:db8::1', False),
                            ('192.0.2.1/24', False), ('bad', False), (None, False),
                            (' 192.0.2.1', False)]:
        assert module.valid_ip_address(value) is expected
    for value, expected in [('192.0.2.0/24', True), ('192.0.2.1', True),
                            ('2001:db8::/64', False), ('2001:db8::1', False),
                            ('192.0.2.1/24', False), (None, False), (' bad', False)]:
        assert module.valid_client_address(value) is expected
    assert module.valid_dns_record({'type': 'AAAA', 'value': '::1'})
    exported = module.FilterModule().filters()
    assert set(exported) == {'valid_client_address', 'valid_ip_address', 'valid_dns_record'}
    assert exported['valid_ip_address']('2001:db8::1') is False


def test_record_validator_rejects_invalid_shapes_empty_data_and_control_characters():
    module = load('acl_records', ROOT / 'filter_plugins/client_acl.py')
    for record in (None, {}, {'type': 'TXT', 'value': ''}, {'type': 'TXT', 'value': 42},
                   {'type': 'TXT', 'value': ' trailing '}, {'type': 'TXT', 'value': '\x7f'},
                   {'type': 'A', 'value': 'invalid'}):
        assert module.valid_dns_record(record) is False
    assert module.valid_dns_record({'type': 'TXT', 'value': '"private DNS"'})


def test_bind_restore_is_independent_of_committed_native_network_routes():
    main = (ROOT / 'tasks/tasks.d/deploy/main.yml').read_text()
    assert 'tasks.d/deploy/front_return_routes.yml' in main
    assert 'bind-front-routes' not in main
    assert 'bind_recovery_archive | basename' in main
    assert main.index('tasks.d/deploy/migration.yml') < main.index('tasks.d/deploy/configuration.yml')


def test_backups_have_collision_resistant_names_and_owned_cleanup():
    shell = (ROOT / 'files/bind-configtool.sh').read_text()
    assert '/dev/urandom' in shell
    assert 'Bind9-Ansible-MANAGED-ZONE' in shell
    assert 'named-checkconf -z' in shell


@pytest.mark.skipif(not shutil.which('sh'), reason='Requires a POSIX shell for the command double')
def test_restorecon_succeeds_with_canonical_layout_and_no_legacy_directory(tmp_path):
    """Exercise the actual argv against a fresh layout, detecting missing paths.

    This checks the missing-directory regression; it does not emulate SELinux
    policy or prove the labels applied by restorecon on RHEL.
    """
    tasks = yaml.safe_load((ROOT / 'tasks/tasks.d/deploy/security.yml').read_text())
    task = next(task for task in tasks if task['name'] == 'Restore SELinux contexts')
    argv = task['ansible.builtin.command']['argv']
    (tmp_path / 'etc').mkdir()
    (tmp_path / 'etc/named.conf').write_text('')
    (tmp_path / 'var/named').mkdir(parents=True)
    (tmp_path / 'var/log/named').mkdir(parents=True)
    double = tmp_path / 'restorecon-double.sh'
    double.write_text('for path do\n  [ "$path" = "-RF" ] && continue\n'
                      '  [ -e "$path" ] || exit 1\ndone\n')
    paths = [str(tmp_path / path.lstrip('/')) if path.startswith('/') else path for path in argv[1:]]
    result = subprocess.run(['sh', str(double)] + paths, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    assert result.returncode == 0, 'restorecon must not target absent legacy files'
    assert set(argv[2:]) == {'/etc/named.conf', '/var/named', '/var/log/named'}
