"""Regression coverage for delegation cuts, dual-stack upstreams and unsafe inputs."""
import base64
import importlib.util
import ipaddress
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined
import yaml

from route_task_expressions import TaskExpressions

ROLE = Path(__file__).resolve().parents[1] / 'roles/bind9'
spec = importlib.util.spec_from_file_location('dns_topology', ROLE / 'filter_plugins/dns_topology.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
DnsTopology = module.DnsTopology
FilterModule = module.FilterModule


def forwarding(domain='corp.example.test', **values):
    result = dict(domain=domain, forwarders=[{'address': '192.0.2.53'}, {'address': '198.51.100.53'}])
    result.update(values)
    return result


class TestDnsTopology:
    def test_native_ipv6_loopback_zone_cannot_be_redeclared(self):
        domain = ipaddress.IPv6Address('::1').reverse_pointer
        for authoritative, forwarded in (([{'domain': domain}], []), ([], [forwarding(domain)])):
            with pytest.raises(ValueError, match='native zone'):
                DnsTopology(authoritative, forwarded)

    def test_child_forwarding_gets_cut_in_nearest_authority_not_root(self):
        planner = DnsTopology([{'domain': 'example.test'}, {'domain': 'site.example.test'}],
                              [forwarding('corp.site.example.test')])
        assert planner.delegations('.') == []
        assert planner.delegations('example.test') == []
        cut = planner.delegations('site.example.test')[0]
        assert cut['domain'] == 'corp.site.example.test.'
        assert [server['type'] for server in cut['servers']] == ['A', 'A']

    def test_unrelated_forwarding_gets_root_cut_and_empty_inputs_stay_empty(self):
        planner = DnsTopology([{'domain': 'example.test'}], [forwarding('corp.other.test')])
        assert len(planner.delegations('.')) == 1
        assert DnsTopology([], []).delegations('.') == []

    @pytest.mark.parametrize('authoritative,forwarded', [
        ([{'domain': 'Example.test'}], [forwarding('example.test')]),
        ([{'domain': 'child.corp.example.test'}], [forwarding()]),
        ([], [forwarding(), forwarding('nested.corp.example.test')]),
        ([{'domain': '127.in-addr.arpa'}], []),
        ([], [forwarding('child.localhost.localdomain')]),
        ([{'domain': 'in-addr.arpa'}], []),
        ([{'domain': 'example.test', 'records': [{'name': 'app.corp'}]}], [forwarding()]),
        ([{'domain': 'example.test', 'records': [{'name': 'corp'}]}], [forwarding()]),
        ([], [forwarding(forwarders=[{'address': '::'}])]),
        ([], [forwarding(dnssec_validate='false')]),
        ([], [forwarding(dnssec_validate=None)]),
    ])
    def test_ambiguous_hidden_native_or_weakly_typed_topology_is_rejected(self, authoritative, forwarded):
        with pytest.raises(ValueError):
            DnsTopology(authoritative, forwarded)

    @pytest.mark.parametrize('domain', ['.', '*.example.test', 'example.test.', 'x\n.test',
                                      '-bad.test', 'bad..test', 'a;include.test', None])
    def test_invalid_suffix_is_not_interpolated(self, domain):
        with pytest.raises(ValueError):
            DnsTopology.domain(domain)

    def test_case_comparison_record_apex_and_filter_contract(self):
        auth = [{'domain': 'Example.test', 'records': [{'name': '@'}, {'name': 'app'}]}]
        filters = FilterModule().filters()
        assert filters['dns_topology_valid'](auth, [forwarding()]) is True
        assert filters['forward_delegations']('example.test', auth, [forwarding()])

    def test_real_zone_templates_contain_the_dns_cut_regression(self):
        env = Environment(loader=FileSystemLoader(str(ROLE / 'templates')), undefined=StrictUndefined)
        env.filters.update(FilterModule().filters())
        zone = {'domain': 'example.test', 'serial': 2, 'records': []}
        context = dict(auth_zone=zone, bind_authoritative_zones=[zone], bind_managed_zones=[zone],
                       bind_forward_zones=[forwarding()], bind_primary_host='p', bind_secondary_host='s',
                       hostvars={'p': {'bind_front_ip': '192.0.2.1'}, 's': {'bind_front_ip': '192.0.2.2'}})
        output = env.get_template('authoritative.zone.j2').render(**context)
        assert 'corp.example.test. IN NS bind-forwarder-1.corp.example.test.' in output
        assert 'bind-forwarder-2.corp.example.test. IN A 198.51.100.53' in output
        root = env.get_template('empty-root.zone.j2').render(**context)
        assert 'corp.example.test. IN NS' not in root
        context['bind_forward_zones'] = [forwarding('other.test')]
        assert 'other.test. IN NS' in env.get_template('empty-root.zone.j2').render(**context)

    @pytest.mark.parametrize('front,back,key,algorithm,secret', [
        ('192.0.2.1', '10.0.0.1', 'site-transfer', 'hmac-sha256', base64.b64encode(b'x' * 32).decode()),
        ('192.0.2.1', '192.0.2.1', 'key', 'hmac-sha256', 'eA=='),
        ('127.0.0.2', '10.0.0.1', 'key', 'hmac-sha256', 'eA=='),
        ('bad;', '10.0.0.1', 'key', 'hmac-sha256', 'eA=='),
        ('192.0.2.1', '10.0.0.1', 'key', 'hmac-sha256', 'not-base64!'),
        (None, '10.0.0.1', 'key', 'hmac-sha256', 'eA=='),
        ('192.0.2.1', '10.0.0.1', 'key', 'hmac-sha256', 'eA=='),
        ('192.0.2.1', '10.0.0.1', 'key";', 'hmac-sha256', base64.b64encode(b'x' * 32).decode()),
        ('192.0.2.1', '10.0.0.1', 'key', 'hmac-md5', base64.b64encode(b'x' * 32).decode()),
    ])
    def test_dns_service_identity_rejects_interpolation_and_invalid_crypto(self, front, back, key, algorithm, secret):
        expected = front == '192.0.2.1' and back == '10.0.0.1' and key == 'site-transfer'
        assert FilterModule.identity(front, back, key, algorithm, secret) is expected

    @pytest.mark.parametrize('client,expected', [('192.0.2.0/24', True), ('192.0.2.3', True),
                                                ('0.0.0.0/0', False), ('::/0', False),
                                                ('224.0.0.0/4', False), ('bad', False),
                                                ('169.254.0.0/16', False)])
    def test_client_transport_and_closed_resolver_guard(self, client, expected):
        assert FilterModule().filters()['dns_client_safe'](client) is expected

    @pytest.mark.parametrize('server,expected', [({'address': '192.0.2.53'}, True),
        ({'address': '2001:db8::53', 'port': '5353'}, True), ({'address': 'bad'}, False),
        ({'address': '0.0.0.0'}, False), ({'address': '224.0.0.1'}, False),
        ({'address': '192.0.2.53', 'port': False}, False), ({'address': '192.0.2.53', 'port': 1.5}, False),
        ({'address': '192.0.2.53', 'port': 'bad;'}, False), ({'address': '192.0.2.53', 'port': 0}, False),
        ({'address': '192.0.2.53', 'port': 65536}, False), ({}, False)])
    def test_forwarder_port_and_destination_are_safe(self, server, expected):
        assert FilterModule().filters()['dns_forwarder_safe'](server) is expected

    @pytest.mark.parametrize('address,expected',[
        ('fd00::53',True),('::1',True),('::',False),('ff02::1',False),
        ('fe80::53',False),('fe80::53%eth0',False),('::ffff:192.0.2.53',False),
        ('2001:db8::53; include',False),(' 2001:db8::53',False),
        ('255.255.255.255',False),('192.000.2.53',False),(53,False),(None,False)])
    def test_dual_stack_forwarder_literals_cannot_bypass_endpoint_guards(self,address,expected):
        assert FilterModule.forwarder({'address':address}) is expected

    @pytest.mark.parametrize('value',[None,'2001:db8::53',[]])
    def test_forwarder_requires_an_endpoint_mapping(self,value):
        assert FilterModule.forwarder(value) is False

    def test_ipv6_forwarders_render_aaaa_glue_in_both_zone_templates(self):
        forward=forwarding(forwarders=[{'address':'192.0.2.53'},{'address':'2001:db8:0::53'}])
        for authorities,parent in (([],'.'),([{'domain':'example.test'}],'example.test')):
            plan=DnsTopology(authorities,[forward]).delegations(parent)[0]
            assert [item['type'] for item in plan['servers']]==['A','AAAA']
            assert plan['servers'][1]['address']=='2001:db8::53'
        env=Environment(loader=FileSystemLoader(str(ROLE/'templates')),undefined=StrictUndefined)
        env.filters.update(FilterModule().filters())
        context=dict(bind_authoritative_zones=[],bind_forward_zones=[forward])
        root=env.get_template('empty-root.zone.j2').render(**context)
        assert 'IN AAAA 2001:db8::53' in root
        zone={'domain':'example.test','serial':2,'records':[]}
        context.update(auth_zone=zone,bind_authoritative_zones=[zone],bind_managed_zones=[zone],
                       bind_primary_host='p',bind_secondary_host='s',
                       hostvars={'p':{'bind_front_ip':'192.0.2.1'},'s':{'bind_front_ip':'192.0.2.2'}})
        authoritative=env.get_template('authoritative.zone.j2').render(**context)
        assert 'IN AAAA 2001:db8::53' in authoritative

    @pytest.mark.parametrize('address,port,accepted',[
        ('2001:db8::53',53,True),('::1',5353,True),('192.0.2.53',53,True),
        ('::1',53,False),('127.0.0.1',53,False),('::ffff:192.0.2.53',53,False)])
    def test_real_forwarder_task_supports_ipv6_without_local_endpoint_bypass(self,address,port,accepted):
        tasks=yaml.safe_load((ROLE/'tasks/tasks.d/validate.yml').read_text())
        task=next(item for item in tasks if item['name']=='Validate each forwarding destination')
        variables=dict(bind_forward_zones=[forwarding(forwarders=[{'address':address,'port':port}])],
                       bind_front_ip='192.0.2.11',bind_back_ip='10.0.0.11',
                       bind_primary_host='p',bind_secondary_host='s',
                       hostvars={'p':{'bind_front_ip':'192.0.2.11','bind_back_ip':'10.0.0.11'},
                                 's':{'bind_front_ip':'192.0.2.12','bind_back_ip':'10.0.0.12'}})
        runner=TaskExpressions(variables)
        if accepted:
            runner.run('validate.yml',tasks=[task])
        else:
            with pytest.raises(ValueError,match='Validate each forwarding destination'):
                runner.run('validate.yml',tasks=[task])

    def test_long_zone_cannot_generate_an_invalid_nameserver_name(self):
        long_name = '.'.join(['a' * 60] * 4 + ['test'])
        with pytest.raises(ValueError, match='too long'):
            DnsTopology([], [forwarding(long_name)])
        with pytest.raises(ValueError, match='too long'):
            DnsTopology([{'domain': '.'.join(['a' * 62] * 4)}], [])
