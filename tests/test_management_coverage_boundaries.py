"""Exercise security boundaries through the real static transaction engine."""
import base64
import os

import pytest

from test_dns_topology import FilterModule
from test_management import CONF, Lab, M, ZONE, pytestmark as base_pytestmark

pytestmark = base_pytestmark


@pytest.fixture
def lab(tmp_path):
    return Lab(tmp_path)


@pytest.mark.parametrize('field,value', [
    ('tsig_key_name', None), ('tsig_key_name', True), ('tsig_key_name', []),
    ('tsig_key_name', 'bad;key'), ('primary_front', 3221225985),
    ('primary_front', '224.0.0.1'), ('primary_front', '0.0.0.0'),
    ('primary_front', '127.0.0.1'), ('primary_front', '169.254.1.1'),
    ('primary_front', '255.255.255.255'),
])
def test_authoritative_creation_refuses_nonliteral_or_nonunicast_identity(lab, field, value):
    manager = lab.manager('zone', 'create', 'new.test', kind='authoritative')
    manager.context[field] = value
    manager.request['expected_checksum'] = M.Configuration(manager.files).revision()
    with pytest.raises(M.ManagementError, match='(identity|peer)'):
        manager.run()
    assert not lab.calls
    assert (lab.root / 'etc/named.conf').read_text() == CONF
    assert not (lab.root / 'var/named/zones/new.test.zone').exists()


def test_deployment_identity_refuses_the_limited_broadcast_endpoint():
    assert not FilterModule.identity('255.255.255.255', '10.0.0.1', 'site-transfer',
                                     'hmac-sha256', base64.b64encode(bytes(range(32))).decode())


@pytest.mark.parametrize('field,value', [('serial', '1'), ('serial', 0), ('serial', 4294967296),
                                       ('ttl', '300'), ('ttl', 0), ('ttl', 4294967296)])
def test_new_authority_requires_positive_uint32_serial_and_ttl(lab, field, value):
    with pytest.raises(M.ManagementError, match='Positive integer'):
        lab.write('zone', 'create', 'new.test', kind='authoritative', **{field: value})
    assert not lab.calls


@pytest.mark.parametrize('case', ['nested-forward', 'masked-authority'])
def test_secondary_uses_the_same_forwarding_topology_refusals_as_primary(lab, case):
    secondary = CONF.replace('type master; file "zones/example.test.zone"',
                             'type slave; file "zones/example.test.zone"')
    if case == 'nested-forward':
        secondary += 'zone "corp.example.test" { type forward; forwarders { 198.51.100.53; }; };\n'
        name = 'child.corp.example.test'
    else:
        secondary += 'zone "inner.corp.example.test" { type slave; file "slaves/inner.zone"; };\n'
        name = 'corp.example.test'
    lab.put('/etc/named.conf', secondary)
    with pytest.raises(M.ManagementError, match='(Nested forwarding|mask an authoritative)'):
        lab.write('zone', 'create', name, kind='forward', forwarders=[{'address': '198.51.100.54'}])
    assert not lab.calls
    assert (lab.root / 'etc/named.conf').read_text() == secondary


@pytest.mark.parametrize('source', ['x one two;', 'x "unfinished', 'x yes'])
def test_scalar_and_trailing_syntax_have_an_explicit_refusal(source):
    with pytest.raises(M.ManagementError):
        syntax = M.Syntax(source)
        syntax.clauses[0].scalar()


@pytest.mark.parametrize('kind', ['file-owner', 'directory-owner', 'directory-link'])
def test_configuration_ownership_and_ancestor_types_are_checked(lab, kind):
    config = lab.root / 'etc/named.conf'
    if kind == 'file-owner':
        os.chown(config, 1, -1)
    elif kind == 'directory-owner':
        os.chown(config.parent, 1, -1)
    else:
        config.parent.rename(lab.root / 'old-etc')
        config.parent.symlink_to(lab.root / 'old-etc', target_is_directory=True)
    with pytest.raises(M.ManagementError, match='Unsafe configuration'):
        lab.manager().run()
    assert not lab.calls


def test_maximum_uint32_serial_wraps_once_for_a_real_record_edit(lab):
    lab.put('/var/named/zones/example.test.zone', ZONE.replace('41 3600', '4294967295 3600'))
    result = lab.write('record', 'update', 'www', zone='example.test', type='A', values=['192.0.2.81'])
    assert result['serials'] == {'example.test.': '1'}
    assert lab.manager('zone', 'read', 'example.test').run()['serial'] == '1'


def test_missing_dnssec_option_retains_the_native_enabled_default():
    assert M.Manager.dnssec_active('options {};') is True
    assert M.Manager.dnssec_active('options { dnssec-validation no; };') is False


@pytest.mark.parametrize('source', ['$TTL\n', '$TTL 300 extra\n', '$TTL invalid\n'])
def test_malformed_ttl_directive_is_refused_explicitly(source):
    with pytest.raises(M.ManagementError, match=r'Invalid \$TTL'):
        M.ZoneDocument(source + ZONE, 'example.test.')


@pytest.mark.parametrize('serial', ['²', '٤١'])
def test_soa_serial_requires_ascii_decimal_syntax(serial):
    document = M.ZoneDocument(ZONE.replace('41 3600', serial + ' 3600'), 'example.test.')
    with pytest.raises(M.ManagementError, match='Invalid SOA serial'):
        document.serial()


@pytest.mark.parametrize('source', [
    'www IN TXT "unfinished\n', 'www IN TXT trailing\\', '; final comment',
    '@ IN SOA ns. host. 1 2 3 4\n',
])
def test_zone_parser_distinguishes_incomplete_input_from_final_comments(source):
    if source.startswith(';'):
        assert M.ZoneDocument(source, 'example.test.').records == []
    else:
        with pytest.raises(M.ManagementError):
            M.ZoneDocument(source, 'example.test.').serial()


@pytest.mark.parametrize('values,ttl', [(None, None), ([' '], None), (['abc\rdef'], None),
                                      (['192.0.2.80'], 'bad'), (['192.0.2.80'], True)])
def test_record_rdata_and_ttl_errors_are_refused(values, ttl):
    document = M.ZoneDocument(ZONE, 'example.test.')
    with pytest.raises(M.ManagementError):
        document.change('update', 'www.example.test.', 'A', values, ttl)


def test_ttl_only_edit_is_a_real_change_and_preserves_unrelated_data(lab):
    result = lab.write('record', 'update', 'www', zone='example.test', type='A', values=['192.0.2.80'], ttl=600)
    assert result['serials'] == {'example.test.': '42'}
    record = lab.manager('record', 'read', 'www', zone='example.test', type='A').run()
    assert record['records'] == [{'value': '192.0.2.80', 'ttl': '600'}]
    assert 'ns1 IN A 192.0.2.11' in (lab.root / 'var/named/zones/example.test.zone').read_text()


@pytest.mark.parametrize('text', [None, 'x' * (M.NativeCommand.LIMIT + 1), '\x00'])
def test_unbounded_or_nontext_dns_output_never_proves_health(text):
    assert not M.DnsReply(text).matches('localhost.', 'A', '127.0.0.1')


@pytest.mark.parametrize('rows', ['short', 'localhost. bad IN A 127.0.0.1',
                                 'localhost. 60 CH A 127.0.0.1',
                                 'localhost. 60 IN AAAA ::1',
                                 'localhost. 60 IN A 127.0.0.1\nextra invalid row',
                                 'localhost. 60 IN A 127.0.0.1\nlocalhost. 60 IN A 127.0.0.1'])
def test_wrong_dns_fields_type_or_extra_rows_are_not_accepted(rows):
    text = ';; status: NOERROR\n;; flags: qr aa; QUERY: 1, ANSWER: 1\n\n' + rows
    assert not M.DnsReply(text).matches('localhost.', 'A', '127.0.0.1')


def test_short_soa_payload_cannot_satisfy_a_serial_probe():
    text = ';; status: NOERROR\n;; flags: qr aa; QUERY: 1, ANSWER: 1\nexample.test. 60 IN SOA ns. host. 41 2 3 4\n'
    assert not M.DnsReply(text).matches('example.test.', 'SOA', '41')


def test_native_directory_default_is_observed_without_an_explicit_clause(lab):
    lab.put('/etc/named.conf', CONF.replace('directory "/var/named";', ''))
    assert lab.manager().run()['value'] == 'yes'


@pytest.mark.parametrize('header', ['zone', 'zone "example.test" IN extra'])
def test_zone_declaration_header_cardinality_is_checked(lab, header):
    lab.put('/etc/named.conf', 'options {}; ' + header + ' { type forward; forwarders { 192.0.2.53; }; };')
    with pytest.raises(M.ManagementError, match='Only class IN'):
        lab.manager().run()


@pytest.mark.parametrize('fields', [dict(name=''), dict(purge_data='yes')])
def test_invalid_zone_identity_and_purge_flag_are_rejected_before_commands(lab, fields):
    name = fields.get('name', 'example.test')
    with pytest.raises(M.ManagementError):
        lab.manager('zone', 'delete', name, **{k: v for k, v in fields.items() if k != 'name'})
    assert not lab.calls


@pytest.mark.parametrize('port', ['53', None, 0, 65536])
def test_forwarder_port_is_a_strict_integer_in_range(lab, port):
    with pytest.raises(M.ManagementError, match='forwarder'):
        lab.write('zone', 'create', 'new.test', kind='forward', forwarders=[{'address': '198.51.100.53', 'port': port}])
    assert not lab.calls


@pytest.mark.parametrize('action', ['read', 'update'])
def test_forwarded_zone_has_no_local_record_management(lab, action):
    lab.put('/etc/named.conf', CONF + 'zone "corp.test" { type forward; forwarders { 198.51.100.53; }; };')
    fields = {'values': ['192.0.2.80']} if action == 'update' else {}
    with pytest.raises(M.ManagementError, match='primary static zone'):
        if action == 'update':
            lab.write('record', action, 'app', zone='corp.test', type='A', **fields)
        else:
            lab.manager('record', action, 'app', zone='corp.test', type='A').run()
    assert not lab.calls


def test_record_at_the_forwarding_cut_is_refused_even_without_a_child_label(lab):
    lab.put('/etc/named.conf', CONF + 'zone "corp.example.test" { type forward; forwarders { 198.51.100.53; }; };')
    with pytest.raises(M.ManagementError, match='forwarding cut'):
        lab.write('record', 'create', 'corp', zone='example.test', type='A', values=['192.0.2.80'])
    assert not lab.calls


@pytest.mark.parametrize('operation', ['unknown-kind', 'purge-unowned', 'empty-settings'])
def test_unsupported_zone_storage_and_empty_mutations_are_refused(lab, operation):
    if operation == 'unknown-kind':
        lab.put('/etc/named.conf', CONF.replace('type master; file "zones/example.test.zone"', 'type stub; file "zones/example.test.zone"'))
        action, fields = 'delete', {}
    elif operation == 'purge-unowned':
        lab.put('/var/named/manual.zone', ZONE)
        lab.put('/etc/named.conf', CONF.replace('zones/example.test.zone', 'manual.zone'))
        action, fields = 'delete', {'purge_data': True}
    else:
        action, fields = 'update', {'settings': {}}
    with pytest.raises(M.ManagementError):
        lab.write('zone', action, 'example.test', **fields)
    assert not lab.calls


def test_new_zone_is_separated_from_a_configuration_without_a_final_newline(lab):
    lab.put('/etc/named.conf', CONF.rstrip('\n'))
    result = lab.write('zone', 'create', 'new.test', kind='forward', forwarders=[{'address': '198.51.100.53'}])
    assert result['changed']
    assert lab.manager('zone', 'read', 'new.test').run()['forwarders'] == [{'address': '198.51.100.53', 'port': 53}]


def test_shared_primary_file_is_allowed_when_the_record_update_is_a_noop(lab):
    lab.put('/etc/named.conf', CONF + 'zone "other.test" { type master; file "zones/example.test.zone"; };')
    assert lab.write('record', 'update', 'www', zone='example.test', type='A', values=['192.0.2.80'])['changed'] is False
    assert not lab.calls


def test_secondary_health_retry_uses_its_bounded_transfer_wait(lab, monkeypatch):
    manager = lab.manager()
    manager.context['primary'] = False
    calls, delays = [], []
    answer = (';; status: NOERROR\n;; flags: qr aa; QUERY: 1, ANSWER: 1\n'
              'example.test. 60 IN SOA ns. host. 41 1 2 3 4\n')
    def reply(argv):
        calls.append(argv)
        return answer if len(calls) % 2 == 0 else ''
    manager.runner = reply
    monkeypatch.setattr(M.time, 'sleep', delays.append)
    manager.check_probes([('example.test.', 'SOA', '*')])
    assert len(calls) == 4 and delays == [2, 2]
    assert all('+norecurse' in call for call in calls)


@pytest.mark.parametrize('operations', [None, {}, 'update'])
def test_batch_requires_an_explicit_operation_list(lab, operations):
    with pytest.raises(M.ManagementError, match='1 to 64 operations'):
        M.BatchManager({'resource': 'batch', 'action': 'read', 'operations': operations}, {}, root=lab.root)


def test_batch_selects_the_native_runner_when_none_is_injected(lab):
    manager = M.BatchManager({'resource': 'batch', 'action': 'read', 'operations': [
        {'resource': 'option', 'action': 'update', 'name': 'minimal-responses', 'value': 'no'}]}, {}, root=lab.root)
    assert manager.runner is M.Manager.command
    assert not lab.calls


def test_dnssec_refresh_does_not_invoke_a_helper_without_owned_exceptions(lab):
    lab.put('/etc/named.conf', CONF.replace('dnssec-validation no', 'dnssec-validation auto'))
    manager = lab.manager()
    manager.config = M.Configuration(manager.files)
    manager.nta()
    assert not lab.calls


def test_a_quote_at_end_of_input_cannot_be_dropped_by_the_tokenizer():
    with pytest.raises(M.ManagementError, match='Unclosed BIND'):
        M.Syntax('x "')


def test_dns_names_are_bounded_before_zone_lookup():
    with pytest.raises(M.ManagementError):
        M.canonical_name('.'.join(['a' * 63] * 4))


def test_missing_required_options_are_not_replaced_with_implicit_state(lab):
    lab.put('/etc/named.conf', 'zone "." { type master; file "empty-root.zone"; };')
    with pytest.raises(M.ManagementError, match='Missing or ambiguous options'):
        lab.manager().run()
    assert not lab.calls


def test_unknown_record_type_presentation_is_refused(lab):
    with pytest.raises(M.ManagementError, match='Invalid record type'):
        lab.manager('record', 'read', 'www', zone='example.test', type='A;bad').run()
    assert not lab.calls


def test_inherited_native_soa_ttl_is_preserved_for_unrelated_records():
    original = ZONE.replace('$TTL 300\n', '')
    document = M.ZoneDocument(original, 'example.test.')
    changed = document.change('update', 'www.example.test.', 'A', ['192.0.2.81'])
    assert 'ns1 IN A 192.0.2.11' in changed
    assert 'www.example.test. IN A 192.0.2.81' in changed
    assert M.ZoneDocument(changed, 'example.test.').serial().text == '42'


def test_private_forwarding_is_recorded_only_when_global_validation_is_enabled(lab):
    result = lab.write('zone', 'create', 'private.test', kind='forward', dnssec_validate=False,
                       forwarders=[{'address': '198.51.100.53'}])
    assert result['changed']
    assert '// Bind9-Ansible DNSSEC-PRIVATE' not in (lab.root / 'etc/named.conf').read_text()


def test_read_reports_an_existing_private_forwarding_marker(lab):
    lab.put('/etc/named.conf', CONF.replace('dnssec-validation no', 'dnssec-validation auto') +
            '// Bind9-Ansible DNSSEC-PRIVATE private.test\n'
            'zone "private.test" { type forward; forwarders { 198.51.100.53 port 5353; }; };')
    result = lab.manager('zone', 'read', 'private.test').run()
    assert result['dnssec_validate'] is False
    assert result['forwarders'] == [{'address': '198.51.100.53', 'port': 5353}]


def test_a_nontext_backup_root_cannot_reach_the_backup_command(lab):
    manager = lab.manager(action='update', value='no')
    manager.context['backup_root'] = None
    manager.request['expected_checksum'] = M.Configuration(manager.files).revision()
    with pytest.raises(M.ManagementError, match='backup root'):
        manager.run()
    assert not any('bind-configtool' in call[0] for call in lab.calls)
    assert (lab.root / 'etc/named.conf').read_text() == CONF


@pytest.mark.parametrize('name', [None, 'bad;name'])
def test_block_setting_contract_refuses_invalid_names(name):
    with pytest.raises(M.ManagementError, match='BIND option names'):
        M.Manager.block_setting('zone "example.test" { type master; };', name, 'yes')


def test_forwarding_without_primary_data_produces_no_delegation_writes(lab):
    manager = lab.manager()
    syntax = M.Syntax('zone "corp.test" { type forward; forwarders { 198.51.100.53; }; };')
    assert manager.cuts({'corp.test.': {'kind': 'forward'}}, syntax) == {}


def test_prepared_static_observations_check_journals_before_a_final_candidate(lab):
    manager = lab.manager()
    manager.config = M.Configuration(manager.files)
    manager.live_state = dict(manager.config.contents)
    manager.live_stamps = dict(manager.config.observations)
    manager.assert_state('Unexpected state change')
    journal = lab.put('/var/named/zones/example.test.zone.jnl', 'pending dynamic update')
    with pytest.raises(M.ManagementError, match='Unexpected state change'):
        manager.assert_state('Unexpected state change')
    assert journal.read_text() == 'pending dynamic update'
