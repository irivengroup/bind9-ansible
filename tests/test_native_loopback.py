"""Protect vendor-owned loopback integration and independent network recovery."""
from pathlib import Path
import ast
import re
import sys

from jinja2 import Environment, StrictUndefined
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/bind9'


class TestNativeLoopback:
    """Check rendered isolation and the real deployment/verification wiring."""

    def test_target_python_script_parses_on_python36_grammar(self):
        # TSIG and filters run on the controller, not on the Python 3.6.8 target.
        source = ROLE / 'files/reverse-serial.py'
        if sys.version_info < (3, 8):
            # The actual legacy interpreter is stricter than a simulated grammar.
            ast.parse(source.read_text(), filename=str(source))
        else:
            ast.parse(source.read_text(), filename=str(source), feature_version=(3, 6))

    def test_native_el8_paths_ipv4_and_automatic_dnssec_are_explicit(self):
        template = (ROLE / 'templates/named.conf.j2').read_text()
        assert 'dnssec-validation {{ dnssec_mode }};' in template
        assert 'listen-on-v6 { none; };' in template
        assert 'empty-zones-enable' not in template  # Use the native enabled default.
        assert 'include "/etc/named.rfc1912.zones";' in template
        assert 'include "/etc/crypto-policies/back-ends/bind.config";' in template
        assert 'validate-except' not in template
        assert 'bindkeys-file "/etc/named.root.key";' in template
        for path in ('/var/named/data/cache_dump.db', '/var/named/data/named_stats.txt',
                     '/run/named/named.pid', '/run/named/session.key'):
            assert path in template
        directories = yaml.safe_load((ROLE / 'tasks/tasks.d/deploy/directories.yml').read_text())[0]['loop']
        assert {'path': '/var/named/data', 'owner': 'named', 'mode': '0750'} in directories
        assert yaml.safe_load((ROLE / 'defaults/main.yml').read_text())['bind_front_routes_enabled'] is False

    @pytest.mark.parametrize('role', ['dns_primary', 'dns_secondary'])
    @pytest.mark.parametrize('clients,internal', [([], []), (['192.0.2.0/24'], []), ([], ['10.0.0.10']), (['192.0.2.0/24'], ['10.0.0.10'])])
    def test_native_loopback_and_global_acls_are_available_for_each_client_combination(self, role, clients, internal):
        primary = role == 'dns_primary'
        context = dict(bind_tsig_key_name='test-transfer', bind_tsig_algorithm='hmac-sha256',
                       bind_tsig_secret='test-render-only', bind_front_ip='192.0.2.11' if primary else '192.0.2.12',
                       bind_back_ip='10.0.0.11' if primary else '10.0.0.12', bind_cloud_clients=clients, bind_internal_clients=internal,
                       bind_allowed_clients=list(dict.fromkeys(clients + internal)), group_names=[role],
                       bind_primary_host='p', bind_secondary_host='s',
                       hostvars={'p': {'bind_back_ip': '10.0.0.11', 'bind_front_ip': '192.0.2.11'},
                                 's': {'bind_back_ip': '10.0.0.12', 'bind_front_ip': '192.0.2.12'}},
                       bind_authoritative_zones=[{'domain': 'example.test'}],
                       bind_forward_zones=[{'domain': 'corp.test', 'forwarders': [{'address': '10.0.0.53'}]}])
        rendered = Environment(undefined=StrictUndefined).from_string(
            (ROLE / 'templates/named.conf.j2').read_text()).render(**context)
        # Use an exact literal rather than BIND's built-in localhost ACL,
        # whose membership also follows the server's interface addresses.
        assert 'acl "local_loopback" {\n    127.0.0.1;\n};' in rendered
        assert rendered.count('127.0.0.1') == 2
        assert 'inet 127.0.0.1 port 953 allow { local_loopback; }' in rendered
        assert rendered.index('acl "local_loopback"') < rendered.index('options {')
        prefix = 'bind_primary_' if primary else 'bind_secondary_'
        assert 'listen-on port 53 { local_loopback; ' + prefix + 'front_ip; ' + prefix + 'back_ip; };' in rendered
        destinations = re.search(r'allow-query-on\s*\{([^{}]*)\};', rendered).group(1).split()
        assert destinations == ['local_loopback;', prefix + 'front_ip;', prefix + 'back_ip;']
        for directive in ('allow-recursion-on', 'allow-query-cache-on'):
            body = re.search(directive + r'\s*\{([^{}]*)\};', rendered).group(1).split()
            expected = ['local_loopback;']
            if clients:
                expected.append(prefix + 'front_ip;')
            if internal:
                expected.append(prefix + 'back_ip;')
            assert body == expected
        for address in clients + internal:
            assert address + ';' in rendered
        assert 'view "' not in rendered and 'in-view' not in rendered
        assert 'allow-query { dns_query_clients; };' in rendered
        assert 'allow-recursion { dns_recursive_clients; };' in rendered
        assert 'allow-query-cache { dns_recursive_clients; };' in rendered
        assert 'allow-transfer { none; };' in rendered
        assert rendered.count('zone "example.test" IN {') == 1
        assert rendered.count('zone "corp.test" IN {') == 1
        assert rendered.count('include "/etc/named.rfc1912.zones";') == 1
        assert 'zone "localhost"' not in rendered  # Vendor definitions are not copied.

    def test_native_files_are_read_only_prerequisites_after_package_install(self):
        tasks = yaml.safe_load((ROLE / 'tasks/tasks.d/native_loopback.yml').read_text())
        assert tasks[0]['ansible.builtin.stat']['follow'] is False
        assert tasks[0]['loop'] == ['/etc/named.rfc1912.zones', '/var/named/named.localhost', '/var/named/named.loopback', '/var/named/named.empty']
        assert 'ansible.builtin.assert' in tasks[1]
        main = (ROLE / 'tasks/tasks.d/deploy/main.yml').read_text()
        assert main.index('tasks.d/deploy/packages.yml') < main.index('tasks.d/native_loopback.yml') < main.index('tasks.d/deploy/configuration.yml')

    def test_loopback_checks_cover_native_a_and_ptr_on_both_transports(self):
        task = yaml.safe_load((ROLE / 'tasks/tasks.d/deploy/health.yml').read_text())[0]
        assert '@127.0.0.1' in task['ansible.builtin.command']['argv']
        assert {(x['transport'], x['type']) for x in task['loop']} == {('+tcp', 'A'), ('+notcp', 'A'), ('+tcp', 'PTR'), ('+notcp', 'PTR')}
        assert 'dns_response_complete' in task['failed_when'] and 'item.answer' in task['failed_when']
        assert task['changed_when'] is False

    def test_native_routes_and_bind_recovery_are_integrated(self):
        main = (ROLE / 'tasks/tasks.d/deploy/main.yml').read_text()
        assert 'tasks.d/deploy/front_return_routes.yml' in main and 'bind-front-routes' not in main
        assert 'Restore the pre-deployment BIND backup' in main
        assert not (ROLE / 'files/front-routes.py').exists()
        assert (ROLE / 'tasks/tasks.d/deploy/front_return_routes.yml').exists()

    def test_periodic_monitor_checks_native_loopback(self):
        source = (ROLE / 'files/named-healthcheck.sh').read_text()
        assert 'for transport in +notcp +tcp' in source
        assert 'check_loopback "$transport" localhost A' in source
        assert 'check_loopback "$transport" 1.0.0.127.in-addr.arpa PTR' in source
        assert '-b 127.0.0.1 @127.0.0.1' in source
