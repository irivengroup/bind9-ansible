"""Security regressions for the 2026-10 remediation."""
import importlib.util
from pathlib import Path
import pytest

ROOT = Path('roles/bind9')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_front_conflict_detection_checks_all_more_specific_routes(monkeypatch):
    module = load('front_routes_security', ROOT / 'files/front-routes.py')
    monkeypatch.setattr(module, 'run', lambda *args: '[{"dst":"203.0.113.128/25","dev":"back0"},{"dst":"default","dev":"back0"}]')
    networks = module.targets(['203.0.113.0/24'])
    assert module.conflicts(networks, 'front0') == ['203.0.113.128/25 via back0']


def test_front_conflict_detection_allows_same_device(monkeypatch):
    module = load('front_routes_security_ok', ROOT / 'files/front-routes.py')
    monkeypatch.setattr(module, 'run', lambda *args: '[{"dst":"203.0.113.128/25","dev":"front0"}]')
    assert module.conflicts(module.targets(['203.0.113.0/24']), 'front0') == []


def test_invalid_forwarder_and_record_rejected():
    module = load('acl_security', ROOT / 'filter_plugins/client_acl.py')
    assert module.valid_ip_address('10.0.0.53')
    assert not module.valid_ip_address('10.0.0.53; allow-transfer { any; };')
    assert module.valid_dns_record({'type': 'A', 'value': '192.0.2.3'})
    assert not module.valid_dns_record({'type': 'A', 'value': 'bad-ip'})
    assert not module.valid_dns_record({'type': 'AAAA', 'value': '192.0.2.3'})
    assert not module.valid_dns_record({'type': 'TXT', 'value': 'foo\nbar'})


def test_network_rollback_separate_from_bind_restore():
    main = (ROOT / 'tasks/main.yml').read_text()
    assert main.index('tasks.d/migration.yml') < main.index('tasks.d/front_return_routes.yml')
    assert '--rollback' in main and '--commit' in main


def test_backups_have_collision_resistant_names_and_owned_cleanup():
    shell = (ROOT / 'files/bind-configtool.sh').read_text()
    assert '/dev/urandom' in shell
    assert 'Bind9-Ansible-MANAGED-ZONE' in shell
    assert 'named-checkconf -z' in shell
