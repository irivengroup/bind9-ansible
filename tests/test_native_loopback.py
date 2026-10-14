"""Protect vendor-owned loopback integration and independent network recovery."""
from pathlib import Path
import ast

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
        ast.parse(source.read_text(), filename=str(source), feature_version=(3, 6))

    @pytest.mark.parametrize('role', ['dns_primary', 'dns_secondary'])
    @pytest.mark.parametrize('front,back', [([], []), (['192.0.2.0/24'], []), ([], ['10.0.0.0/24']), (['192.0.2.0/24'], ['10.0.0.0/24'])])
    def test_local_view_is_available_and_restricted_in_all_client_modes(self, role, front, back):
        primary = role == 'dns_primary'
        context = dict(bind_tsig_key_name='test-transfer', bind_tsig_algorithm='hmac-sha256',
                       bind_tsig_secret='test-render-only', bind_front_ip='192.0.2.11' if primary else '192.0.2.12',
                       bind_back_ip='10.0.0.11' if primary else '10.0.0.12', bind_frontend_clients=front,
                       bind_backend_clients=back, group_names=[role],
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
        assert rendered.count('127.0.0.1') == 1
        assert rendered.index('acl "local_loopback"') < rendered.index('options {')
        prefix = 'bind_primary_' if primary else 'bind_secondary_'
        assert 'listen-on port 53 { local_loopback; ' + prefix + 'front_ip; ' + prefix + 'back_ip; };' in rendered
        peer = rendered.split('view "peer" IN {', 1)[1].split('view "local" IN {', 1)[0]
        assert 'allow-query {\n        local_loopback;' in peer
        assert 'match-destinations { ' + prefix + 'back_ip; };' in peer
        assert rendered.index('view "peer"') < rendered.index('view "local"') < rendered.index('view "deny"')
        local = rendered.split('view "local" IN {', 1)[1].split('\nview "', 1)[0]
        for directive in ('match-clients', 'match-destinations', 'allow-recursion', 'allow-query-cache', 'allow-query'):
            assert directive + ' { local_loopback; };' in local
        assert 'allow-transfer { none; };' in local
        assert 'include "/etc/named.rfc1912.zones";' in local
        assert 'zone "example.test" IN { in-view "peer"; };' in local
        assert 'zone "corp.test" IN {' in local
        assert rendered.count('include "/etc/named.rfc1912.zones";') == 1
        assert 'zone "localhost"' not in rendered  # Vendor definitions are not copied.

    def test_native_files_are_read_only_prerequisites_after_package_install(self):
        tasks = yaml.safe_load((ROLE / 'tasks/tasks.d/native_loopback.yml').read_text())
        assert tasks[0]['ansible.builtin.stat']['follow'] is False
        assert tasks[0]['loop'] == ['/etc/named.rfc1912.zones', '/var/named/named.localhost', '/var/named/named.loopback', '/var/named/named.empty']
        assert 'ansible.builtin.assert' in tasks[1]
        main = (ROLE / 'tasks/main.yml').read_text()
        assert main.index('tasks.d/packages.yml') < main.index('tasks.d/native_loopback.yml') < main.index('tasks.d/configuration.yml')

    def test_loopback_checks_cover_native_a_and_ptr_on_both_transports(self):
        task = yaml.safe_load((ROLE / 'tasks/tasks.d/health.yml').read_text())[0]
        assert '@127.0.0.1' in task['ansible.builtin.command']['argv']
        assert {(x['transport'], x['type']) for x in task['loop']} == {('+tcp', 'A'), ('+notcp', 'A'), ('+tcp', 'PTR'), ('+notcp', 'PTR')}
        assert 'NOERROR' in task['failed_when'] and 'item.answer' in task['failed_when']
        assert task['changed_when'] is False

    def test_native_routes_and_bind_recovery_are_integrated(self):
        main = (ROLE / 'tasks/main.yml').read_text()
        assert 'tasks.d/front_return_routes.yml' in main and 'bind-front-routes' not in main
        assert 'Restore the pre-deployment BIND backup' in main
        assert not (ROLE / 'files/front-routes.py').exists()
        assert (ROLE / 'tasks/tasks.d/front_return_routes.yml').exists()

    def test_periodic_monitor_checks_native_loopback(self):
        source = (ROLE / 'files/named-healthcheck.sh').read_text()
        assert 'for transport in +notcp +tcp' in source
        assert 'check_loopback "$transport" localhost A' in source
        assert 'check_loopback "$transport" 1.0.0.127.in-addr.arpa PTR' in source
        assert '-b 127.0.0.1 @127.0.0.1' in source
