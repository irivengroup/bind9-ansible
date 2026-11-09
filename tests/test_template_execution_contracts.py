"""Render the real zone/service templates and check their operational contracts."""
from pathlib import Path
import shlex

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from test_dns_topology import FilterModule
from test_management import M

ROLE = Path(__file__).resolve().parents[1] / 'roles/bind9'


def environment():
    renderer = Environment(loader=FileSystemLoader(str(ROLE / 'templates')), undefined=StrictUndefined)
    renderer.filters.update(FilterModule().filters())
    return renderer


def test_authoritative_records_are_rendered_and_keep_their_types_and_rdata():
    zone = {'domain': 'example.test', 'serial': 7, 'records': [
        {'name': 'app', 'type': 'A', 'value': '192.0.2.80'},
        {'name': 'v6', 'type': 'AAAA', 'value': '2001:db8::80'},
        {'name': 'text', 'type': 'TXT', 'value': '"two words"'},
    ]}
    rendered = environment().get_template('authoritative.zone.j2').render(
        auth_zone=zone, bind_authoritative_zones=[zone], bind_forward_zones=[],
        bind_primary_host='p', bind_secondary_host='s',
        hostvars={'p': {'bind_front_ip': '192.0.2.11'}, 's': {'bind_front_ip': '192.0.2.12'}})
    document = M.ZoneDocument(rendered, 'example.test.')
    assert document.serial().text == '7'
    for owner, kind, data in [('app', 'A', ['192.0.2.80']), ('v6', 'AAAA', ['2001:db8::80']),
                               ('text', 'TXT', ['"two words"'])]:
        records = document.rrset(owner + '.example.test.', kind)
        assert len(records) == 1 and [token.text for token in records[0]['tokens']] == data


def test_reverse_delegation_renders_both_ipv4_and_ipv6_glue_without_hiding_ptrs():
    reverse = {'domain': '113.0.203.in-addr.arpa', 'serial': 8, 'records': [
        {'name': '5', 'type': 'PTR', 'value': 'app.example.test.'}]}
    authorities = [{'domain': 'example.test', 'records': []}, reverse]
    forwarded = {'domain': '0.113.0.203.in-addr.arpa', 'forwarders': [
        {'address': '198.51.100.53'}, {'address': '2001:db8::53'}]}
    rendered = environment().get_template('reverse.zone.j2').render(
        auth_zone=reverse, bind_authoritative_zones=authorities, bind_managed_zones=authorities,
        bind_forward_zones=[forwarded])
    document = M.ZoneDocument(rendered, reverse['domain'] + '.')
    assert document.serial().text == '8'
    assert [token.text for token in document.rrset('5.' + reverse['domain'] + '.', 'PTR')[0]['tokens']] == ['app.example.test.']
    cut = forwarded['domain'] + '.'
    records = document.rrset(cut, 'NS')
    assert [record['tokens'][0].text for record in records] == ['bind-forwarder-1.' + cut, 'bind-forwarder-2.' + cut]
    assert document.rrset('bind-forwarder-1.' + cut, 'A')[0]['tokens'][0].text == '198.51.100.53'
    assert document.rrset('bind-forwarder-2.' + cut, 'AAAA')[0]['tokens'][0].text == '2001:db8::53'


def test_healthcheck_service_invokes_each_managed_zone_on_the_back_address():
    rendered = environment().get_template('named-healthcheck.service.j2').render(
        bind_back_ip='10.0.0.11', bind_managed_zones=[{'domain': 'example.test'}, {'domain': '113.0.203.in-addr.arpa'}])
    command = next(line.split('=', 1)[1] for line in rendered.splitlines() if line.startswith('ExecStart='))
    assert shlex.split(command) == ['/usr/local/sbin/named-healthcheck', '10.0.0.11', 'example.test', '113.0.203.in-addr.arpa']
    assert 'NoNewPrivileges=yes' in rendered and 'ProtectSystem=strict' in rendered
    assert 'ProtectHome=yes' in rendered and 'PrivateTmp=yes' in rendered


def test_runtime_dropin_keeps_native_startup_and_an_explicit_local_rndc_reload():
    rendered = environment().get_template('named-runtime-override.j2').render()
    lines = [line for line in rendered.splitlines() if not line.startswith('#')]
    assert '[Service]' in lines
    assert [line for line in lines if line.startswith('UMask=')] == ['UMask=0027']
    assert [line for line in lines if line.startswith('ExecReload=')] == [
        'ExecReload=', 'ExecReload=/usr/sbin/rndc -s 127.0.0.1 -k /etc/rndc.key reload']
    assert not any(line.startswith('ExecStart=') for line in lines)
