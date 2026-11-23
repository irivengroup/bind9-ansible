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
                       bind_allowed_clients=['192.0.2.0/24', '10.0.0.0/24'],
                       bind_authoritative_zones=[{'domain': 'example.test'}], bind_forward_zones=[])
        output = Environment(undefined=StrictUndefined).from_string(TEMPLATE.read_text()).render(**context)
        definitions = dict(re.findall(r'acl "([^"]+)"\s*\{([^{}]+)\};', output))
        for peer, prefix in (('p', 'primary'), ('s', 'secondary')):
            for interface in ('front', 'back'):
                name = 'bind_' + prefix + '_' + interface + '_ip'
                if interface == 'front' and (peer == 'p') != primary:
                    assert name not in output
                    continue
                assert definitions[name].strip() == hosts[peer]['bind_' + interface + '_ip'] + ';'
                assert output.count('acl "' + name + '"') == 1
        for interface in ('back',):
            name = 'bind_servers_' + interface + '_ip'
            assert definitions[name].split() == ['bind_primary_' + interface + '_ip;',
                                                  'bind_secondary_' + interface + '_ip;']
            assert output.count('acl "' + name + '"') == 1
        assert 'bind_servers_front_ip' not in output
        # A declaration must have a consumer in the generated configuration.
        for name in definitions:
            assert len(re.findall(r'\b' + re.escape(name) + r'\b', output)) > 1
        assert not any(address in output for address in hosts['other-site'].values())
        prefix = 'bind_primary_' if primary else 'bind_secondary_'
        assert 'listen-on port 53 { local_loopback; ' + prefix + 'front_ip; ' + prefix + 'back_ip; };' in output
        destinations = re.search(r'allow-query-on\s*\{([^{}]*)\};', output).group(1).split()
        assert destinations == ['local_loopback;', prefix + 'front_ip;', prefix + 'back_ip;']
        assert 'view "' not in output and 'in-view' not in output
        assert 'allow-query { dns_query_clients; };' in output
        assert 'allow-recursion { dns_recursive_clients; };' in output
        recursive = re.sub(r'//[^\n]*', '', definitions['dns_recursive_clients']).split()
        assert recursive[:3] == ['!bind_servers_back_ip;', '!key', '"site-transfer";']
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
