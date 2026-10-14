"""Protect site-scoped server ACLs and the exact local listener identity."""
from pathlib import Path
import re

from jinja2 import Environment, StrictUndefined
import pytest


TEMPLATE = Path(__file__).resolve().parents[1] / 'roles/bind9/templates/named.conf.j2'


class TestServerAcls:
    """Check both roles without trusting ACL names to imply correct membership."""

    @pytest.mark.parametrize('role', ['dns_primary', 'dns_secondary'])
    def test_membership_is_local_to_the_site_and_preserves_transfer_guards(self, role):
        hosts = {'p': {'bind_back_ip': '10.0.0.11', 'bind_front_ip': '192.0.2.11'},
                 's': {'bind_back_ip': '10.0.0.12', 'bind_front_ip': '192.0.2.12'},
                 'other-site': {'bind_back_ip': '10.99.0.11', 'bind_front_ip': '198.51.100.11'}}
        primary = role == 'dns_primary'
        local = hosts['p' if primary else 's']
        context = dict(local, bind_primary_host='p', bind_secondary_host='s', hostvars=hosts,
                       group_names=[role], bind_tsig_key_name='site-transfer',
                       bind_tsig_algorithm='hmac-sha256', bind_tsig_secret='render-only',
                       bind_frontend_clients=['192.0.2.0/24'], bind_backend_clients=['10.0.0.0/24'],
                       bind_authoritative_zones=[{'domain': 'example.test'}], bind_forward_zones=[])
        output = Environment(undefined=StrictUndefined).from_string(TEMPLATE.read_text()).render(**context)
        definitions = dict(re.findall(r'acl "([^"]+)"\s*\{([^{}]+)\};', output))
        for peer, prefix in (('p', 'primary'), ('s', 'secondary')):
            for interface in ('front', 'back'):
                name = 'bind_' + prefix + '_' + interface + '_ip'
                assert definitions[name].strip() == hosts[peer]['bind_' + interface + '_ip'] + ';'
                assert output.count('acl "' + name + '"') == 1
        for interface in ('front', 'back'):
            name = 'bind_servers_' + interface + '_ip'
            assert definitions[name].split() == ['bind_primary_' + interface + '_ip;',
                                                  'bind_secondary_' + interface + '_ip;']
            assert output.count('acl "' + name + '"') == 1
        assert not any(address in output for address in hosts['other-site'].values())
        prefix = 'bind_primary_' if primary else 'bind_secondary_'
        assert 'listen-on port 53 { local_loopback; ' + prefix + 'front_ip; ' + prefix + 'back_ip; };' in output
        assert 'allow-query-on { local_loopback; bind_servers_front_ip; bind_servers_back_ip; };' in output
        assert 'match-clients { bind_servers_back_ip; key "site-transfer"; };' in output
        assert 'match-clients { !bind_servers_back_ip; !key "site-transfer"; frontend_clients; };' in output
        # Named ACLs must preserve address AND TSIG, never turn it into OR.
        if primary:
            assert 'allow-transfer { !{ !bind_secondary_back_ip; any; }; key "site-transfer"; };' in output
            assert 'also-notify { 10.0.0.12 key "site-transfer"; };' in output
        else:
            assert 'allow-notify { !{ !bind_primary_back_ip; any; }; key "site-transfer"; };' in output
            assert 'masters { 10.0.0.11 key "site-transfer"; };' in output
        # These directives require an address, not an address-match list.
        assert 'transfer-source ' + local['bind_back_ip'] + ';' in output
        assert 'notify-source ' + local['bind_back_ip'] + ';' in output
