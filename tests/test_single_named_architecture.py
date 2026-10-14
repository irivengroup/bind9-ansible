"""Regression tests for the intentionally non-modular named.conf architecture."""
from pathlib import Path
import ast

from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/bind9'
TEMPLATE = ROLE / 'templates/named.conf.j2'


def test_exactly_one_named_configuration_template():
    assert sorted(p.name for p in (ROLE / 'templates').glob('*.conf.j2')) == ['named.conf.j2']
    # Only distribution-owned native zones may be included; no custom fragments.
    assert TEMPLATE.read_text().count('include "') == 1
    assert 'include "/etc/named.rfc1912.zones";' in TEMPLATE.read_text()


def test_single_template_contains_all_views_and_security_controls():
    s = TEMPLATE.read_text()
    for part in ('options {', 'view "peer"', 'view "external"', 'view "internal"', 'view "deny"',
                 'recursion no;', 'allow-transfer { none; };', 'dnssec-validation auto;',
                 'type master;', 'type slave;', 'allow-notify { !{ !', 'allow-transfer { !{ !'):
        assert part in s
    assert s.index('view "peer"') < s.index('view "external"') < s.index('view "internal"') < s.index('view "deny"')


def test_peer_source_only_in_peer():
    s = TEMPLATE.read_text()
    before, after = s.split('view "peer"', 1)
    peer, after = after.split('view "external"', 1)
    assert 'transfer-source {{ bind_back_ip }};' in peer
    assert 'notify-source {{ bind_back_ip }};' in peer
    assert 'transfer-source' not in before + after
    assert 'notify-source' not in before + after
    assert 'query-source' not in s


def test_named_conf_render_with_both_client_views():
    context = {
        'bind_tsig_key_name': 'site-transfer', 'bind_tsig_algorithm': 'hmac-sha256',
        'bind_tsig_secret': 'base64fake=', 'bind_front_ip': '192.0.2.10',
        'bind_back_ip': '10.10.0.10', 'bind_frontend_clients': ['192.0.2.0/24'],
        'bind_backend_clients': ['10.10.0.0/24'],
        'bind_primary_group': 'dns_primary', 'group_names': ['dns_primary'],
        'bind_primary_host': 'p', 'bind_secondary_host': 's',
        'hostvars': {'p': {'bind_back_ip': '10.10.0.10', 'bind_front_ip': '192.0.2.10'},
                     's': {'bind_back_ip': '10.10.0.11', 'bind_front_ip': '192.0.2.11'}},
        'bind_authoritative_zones': [{'domain': 'prod.example.test'}],
        'bind_forward_zones': [{'domain': 'corp.example.test', 'forwarders': [{'address': '10.20.0.53'}]}],
    }
    out = Environment(undefined=StrictUndefined).from_string(TEMPLATE.read_text()).render(**context)
    assert 'view "external"' in out and 'view "internal"' in out
    assert 'zone "prod.example.test" IN' in out
    assert 'zone "corp.example.test" IN' in out
    assert 'masters {' not in out
    assert 'allow-transfer { !{ !bind_secondary_back_ip; any; }; key "site-transfer"; };' in out
    assert out.count('include "') == 1
    assert 'include "/etc/named.rfc1912.zones";' in out
    assert 'secret "base64fake=";' in out


def test_named_conf_render_secondary_front_only():
    context = {
        'bind_tsig_key_name': 'site-transfer', 'bind_tsig_algorithm': 'hmac-sha256',
        'bind_tsig_secret': 'base64fake=', 'bind_front_ip': '192.0.2.11',
        'bind_back_ip': '10.10.0.11', 'bind_frontend_clients': ['192.0.2.0/24'],
        'bind_backend_clients': [], 'bind_primary_group': 'dns_primary',
        'group_names': ['dns_secondary'], 'bind_primary_host': 'p', 'bind_secondary_host': 's',
        'hostvars': {'p': {'bind_back_ip': '10.10.0.10', 'bind_front_ip': '192.0.2.10'},
                     's': {'bind_back_ip': '10.10.0.11', 'bind_front_ip': '192.0.2.11'}},
        'bind_authoritative_zones': [{'domain': 'prod.example.test'}], 'bind_forward_zones': [],
    }
    out = Environment(undefined=StrictUndefined).from_string(TEMPLATE.read_text()).render(**context)
    assert 'view "internal"' not in out
    assert 'type slave;' in out and 'masters { 10.10.0.10 key "site-transfer"; };' in out
    assert 'allow-notify { !{ !bind_primary_back_ip; any; }; key "site-transfer"; };' in out


def test_configuration_task_deploys_single_file_and_removes_old_fragments():
    s = (ROLE / 'tasks/tasks.d/configuration.yml').read_text()
    assert 'src: named.conf.j2' in s
    assert 'dest: /etc/named.conf' in s
    assert 'no_log: true' in s
    assert '/etc/named/40-views.conf' in s
    assert 'state: absent' in s


def test_backup_only_no_transaction_controller():
    assert not (ROLE / 'files/named-transaction.py').exists()
    assert not (ROLE / 'tasks/tasks.d/transaction.yml').exists()
    assert 'snapshot' not in (ROLE / 'files/bind-configtool.sh').read_text().lower()
    assert '--force' in (ROLE / 'tasks/tasks.d/migration.yml').read_text()
    assert 'bind_recovery_archive' in (ROLE / 'tasks/main.yml').read_text()


def test_recovery_uses_pre_deployment_archive():
    s = (ROLE / 'tasks/main.yml').read_text()
    assert s.index('tasks.d/migration.yml') < s.index('tasks.d/configuration.yml')
    assert 'bind-configtool' in s and 'restore' in s
    assert 'bind_recovery_archive | basename' in s
    assert 'named-transaction' not in s


def test_reverse_serial_uses_backup_not_snapshot():
    s = (ROLE / 'files/reverse-serial.py').read_text()
    assert "parser.add_argument('--backup'" in s
    assert '--snapshot' not in s
    assert "'--backup', bind_recovery_archive" in (ROLE / 'tasks/tasks.d/zones.yml').read_text()


def test_only_new_inventory_architecture():
    assert not (ROOT / 'inventory/hosts.ini').exists()
    assert not (ROOT / 'inventory/group_vars').exists()
    assert not (ROOT / 'inventory/host_vars').exists()
    for env in ('prod', 'dev', 'preprod'):
        assert (ROOT / 'inventory' / env / 'hosts.ini').exists()
        assert (ROOT / 'inventory' / env / 'group_vars/site1.yml').exists()
    assert 'inventory/prod/hosts.ini' in (ROOT / 'ansible.cfg').read_text()


def test_python_files_parse_on_python38_grammar():
    for p in list((ROLE / 'files').glob('*.py')) + list((ROLE / 'filter_plugins').glob('*.py')):
        ast.parse(p.read_text(), filename=str(p), feature_version=(3, 8))


def test_no_config_include_directories_managed():
    s = (ROLE / 'tasks/tasks.d/directories.yml').read_text()
    assert '/etc/named/zones' not in s
    assert '/var/named/zones' in s
