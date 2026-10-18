"""Boundary and recovery scenarios for untrusted or drifting static DNS inputs."""
import os
from pathlib import Path
import sys

import pytest

from test_management import M, Lab, CONF, ZONE, pytestmark as base_pytestmark

pytestmark = base_pytestmark


@pytest.fixture
def lab(tmp_path):
    return Lab(tmp_path)


@pytest.mark.parametrize('source', ['x "bad', 'x yes; /* bad', '} ;', 'options { x yes; }; options { y no; };'])
def test_ambiguous_syntax_refused(source):
    with pytest.raises(M.ManagementError):
        syntax = M.Syntax(source)
        M.Syntax.one(syntax.clauses, 'options')


def test_clause_block_and_scalar_contracts():
    scalar = M.Syntax('x yes;').clauses[0]
    assert not scalar.simple_block()
    with pytest.raises(M.ManagementError):
        scalar.children()
    with pytest.raises(M.ManagementError):
        M.Syntax('x { yes; };').clauses[0].scalar()
    compound = M.Syntax('inet 127.0.0.1 allow { localhost; } keys { key; };').clauses[0]
    assert not compound.simple_block()


@pytest.mark.parametrize('virtual', ['relative.conf', '/etc/../named.conf', '/tmp/named.conf'])
def test_configuration_paths_must_be_canonical_and_owned(lab, virtual):
    with pytest.raises(M.ManagementError):
        M.SafeFiles(lab.root).read(virtual)


def test_unsafe_parent_and_bounded_file_size(lab, monkeypatch):
    (lab.root / 'etc').chmod(0o777)
    with pytest.raises(M.ManagementError):
        lab.manager().run()
    (lab.root / 'etc').chmod(0o755)
    monkeypatch.setattr(M.SafeFiles, 'LIMIT', 10)
    with pytest.raises(M.ManagementError, match='size bound'):
        lab.manager().run()


def test_vendor_crypto_policy_link_is_bounded_and_readable(lab):
    parent = lab.root / 'etc/crypto-policies/back-ends'
    parent.mkdir(parents=True)
    target = lab.root / 'usr/share/crypto-policies/DEFAULT/bind.txt'
    target.parent.mkdir(parents=True)
    target.write_text('disable-algorithms "." { RSAMD5; };\n')
    link = parent / 'bind.config'
    link.symlink_to(target)
    files = M.SafeFiles(lab.root)
    assert files.read('/etc/crypto-policies/back-ends/bind.config') == target.read_bytes()
    target.parent.chmod(0o777)
    with pytest.raises(M.ManagementError, match='ancestor'):
        files.read('/etc/crypto-policies/back-ends/bind.config')
    target.parent.chmod(0o755)
    link.unlink()
    link.symlink_to(lab.root / 'var/named/empty-root.zone')
    with pytest.raises(M.ManagementError, match='link target'):
        files.read('/etc/crypto-policies/back-ends/bind.config')


@pytest.mark.parametrize('change', [
    lambda c: c.replace('/var/named"', '/tmp"'),
    lambda c: c + 'view "other" { };\n',
    lambda c: c.replace('zone "example.test" {', 'zone "example.test" CH {'),
    lambda c: c + 'zone "example.test" { type forward; forwarders { 10.0.0.53; }; };',
    lambda c: c.replace('file "zones/example.test.zone";', ''),
    lambda c: c.replace('allow-update { none; };', 'allow-update { any; };'),
    lambda c: c.replace('allow-update { none; };', 'inline-signing no;'),
    lambda c: c.replace('allow-update { none; };', 'update-policy { grant key zonesub ANY; };'),
])
def test_unsupported_configuration_refuses_even_read(lab, change):
    lab.put('/etc/named.conf', change(CONF))
    with pytest.raises(M.ManagementError):
        lab.manager().run()


def test_zone_journal_refused(lab):
    lab.put('/var/named/zones/example.test.zone.jnl', 'journal')
    with pytest.raises(M.ManagementError, match='journal'):
        lab.manager().run()


def test_recursive_duplicate_and_relative_includes(lab):
    lab.put('/var/named/extra.conf', 'acl external { 192.0.2.0/24; };')
    lab.put('/etc/named.conf', CONF + 'include "extra.conf"; include "extra.conf";')
    assert len(M.Configuration(M.SafeFiles(lab.root)).configs) == 2
    lab.put('/var/named/extra.conf', 'include "/etc/named.conf";')
    with pytest.raises(M.ManagementError, match='Circular'):
        lab.manager().run()


def test_include_count_bound(lab):
    config = M.Configuration(M.SafeFiles(lab.root))
    config.configs.update({str(n): None for n in range(128)})
    with pytest.raises(M.ManagementError, match='Too many'):
        config.load('/etc/next.conf', set())


@pytest.mark.parametrize('name', ['', None, 'bad name', 'bad\\name', 'a' * 64 + '.test', 'a..test'])
def test_invalid_dns_name_refused(name):
    with pytest.raises(M.ManagementError):
        M.canonical_name(name)


@pytest.mark.parametrize('text', ['$ORIGIN\n', '$GENERATE 1-10 host$ A 192.0.2.$\n', '  IN A 192.0.2.1\n', 'missingtype\n', 'x IN TXT "unclosed', 'x IN SOA ( foo\n'])
def test_zone_syntax_refuses_ambiguous_or_incomplete_data(text):
    with pytest.raises(M.ManagementError):
        M.ZoneDocument(text, 'example.test.')


def test_origin_blank_comments_escaped_text_and_final_line():
    doc = M.ZoneDocument('; comment\n\n$ORIGIN sub.example.test.\n$TTL 1h\n@ IN TXT "a\\\"b; ()"\nnext IN A 192.0.2.1', 'example.test.')
    assert doc.records[0]['name'] == 'sub.example.test.'
    assert doc.records[1]['name'] == 'next.sub.example.test.'


@pytest.mark.parametrize('text', [ZONE.replace(' SOA ', ' TXT '), ZONE.replace('41 3600', 'bad 3600'), ZONE.replace('41 3600', '4294967296 3600')])
def test_invalid_soa_refused(text):
    with pytest.raises(M.ManagementError):
        M.ZoneDocument(text, 'example.test.').serial()


@pytest.mark.parametrize('action,name,values', [('create', 'www.example.test.', ['192.0.2.1']), ('delete', 'absent.example.test.', None), ('update', 'www.example.test.', [True]), ('update', 'www.example.test.', ['192.0.2.1 ( extra )'])])
def test_rrset_conflicts_and_unsafe_data_refused(action, name, values):
    with pytest.raises(M.ManagementError):
        M.ZoneDocument(ZONE, 'example.test.').change(action, name, 'A', values)


def test_noop_rrset_and_missing_final_newline(lab):
    assert not lab.write('record', 'update', 'www', zone='example.test', type='A', values=['192.0.2.80'])['changed']
    source = ZONE.rstrip('\n')
    changed = M.ZoneDocument(source, 'example.test.').change('create', 'new.example.test.', 'A', ['192.0.2.90'])
    assert '\nnew.example.test.' in changed


@pytest.mark.parametrize('payload', [dict(resource='bad', action='read'), dict(resource='option', action='patch'), dict(resource='option', action='read', extra='bad'), dict(resource='zone', action='delete', purge_data='yes')])
def test_public_request_contract_rejected(payload):
    with pytest.raises(M.ManagementError):
        M.Manager(payload, {})


def test_native_command_status_and_output():
    assert M.Manager.command(['/usr/bin/printf', 'ok']) == 'ok'
    with pytest.raises(M.ManagementError, match=Path(sys.executable).name) as failure:
        M.Manager.command([sys.executable, '-c', 'import sys; print("sensitive"); sys.exit(3)'])
    assert 'sensitive' not in str(failure.value)


def test_nonroot_and_missing_record_zone_refused(lab, monkeypatch):
    monkeypatch.setattr(os, 'geteuid', lambda: 123)
    with pytest.raises(M.ManagementError, match='root'):
        lab.manager().run()
    monkeypatch.undo()
    with pytest.raises(M.ManagementError, match='not declared'):
        lab.write('record', 'create', 'www', zone='absent.test', type='A', values=['192.0.2.1'])
    with pytest.raises(M.ManagementError, match='primary static'):
        lab.manager('record', 'read', 'www', zone='absent.test', type='A').run()
    with pytest.raises(M.ManagementError, match='Invalid record type'):
        lab.write('record', 'create', 'www', zone='example.test', type=42, values=['192.0.2.1'])
    with pytest.raises(M.ManagementError, match='non-native'):
        lab.write('record', 'create', 'www', zone='.', type='A', values=['192.0.2.1'])


@pytest.mark.parametrize('action,name,fields', [
    ('create', 'example.test', {'kind': 'authoritative'}),
    ('delete', 'absent.test', {}),
    ('create', 'absent.test', {'kind': 'stub'}),
    ('update', 'example.test', {}),
    ('update', 'example.test', {'kind': 'forward'}),
    ('update', 'example.test', {'settings': {'file': '"/tmp/x"'}}),
])
def test_zone_contract_refusals(lab, action, name, fields):
    with pytest.raises(M.ManagementError):
        lab.write('zone', action, name, **fields)


def test_zone_owned_by_include_is_preserved(lab):
    prefix, zone = CONF.split('zone "example.test"', 1)
    lab.put('/etc/named.conf', prefix + 'include "/etc/zones.conf";\n')
    lab.put('/etc/zones.conf', 'zone "example.test"' + zone)
    with pytest.raises(M.ManagementError, match='include'):
        lab.write('zone', 'delete', 'example.test')


@pytest.mark.parametrize('name,value', [('bad; name', 'yes'), ('x', None), ('x', 'yes; y no')])
def test_zone_setting_injection_refused(name, value):
    with pytest.raises(M.ManagementError):
        M.Manager.block_setting('zone "test" { type master; };', name, value)


@pytest.mark.parametrize('forwarders', [None, [], [True], [{'address': '10.0.0.53', 'extra': 1}]])
def test_invalid_forwarder_shapes(lab, forwarders):
    with pytest.raises(M.ManagementError):
        lab.write('zone', 'create', 'forward.test', kind='forward', forwarders=forwarders)


@pytest.mark.parametrize('context,fields', [
    ({'tsig_key_name': 'rndc-key'}, {}),
    ({'tsig_key_name': 'missing-key'}, {}),
    ({'primary_back': 'bad'}, {}),
    ({}, {'serial': True}),
    ({}, {'ttl': 0}),
])
def test_authority_requires_existing_key_peers_and_valid_metadata(lab, context, fields):
    manager = lab.manager('zone', 'create', 'new.test', expected_checksum=lab.manager().run()['checksum'], kind='authoritative', **fields)
    manager.context.update(context)
    with pytest.raises(M.ManagementError):
        manager.run()


def test_secondary_authoritative_declaration_and_purge_refusal(lab):
    manager = lab.manager('zone', 'create', 'new.test', expected_checksum=lab.manager().run()['checksum'], kind='authoritative')
    manager.context['primary'] = False
    manager.run()
    config = (lab.root / 'etc/named.conf').read_text()
    assert 'type slave;' in config and 'masters { 10.0.0.11 key "site-transfer";' in config
    with pytest.raises(M.ManagementError, match='Only primary data'):
        lab.write('zone', 'delete', 'new.test', purge_data=True)


def test_nested_forward_and_masking_authority_refused(lab):
    lab.write('zone', 'create', 'forward.test', kind='forward', forwarders=[{'address': '10.10.0.53'}])
    for name in ('sub.forward.test', 'test'):
        with pytest.raises(M.ManagementError):
            lab.write('zone', 'create', name, kind='forward', forwarders=[{'address': '10.10.0.53'}])


def test_dnssec_marker_create_update_delete_and_helper_required(lab):
    lab.put('/etc/named.conf', CONF.replace('dnssec-validation no', 'dnssec-validation auto'))
    request = dict(kind='forward', forwarders=[{'address': '10.10.0.53'}], dnssec_validate=False)
    with pytest.raises(FileNotFoundError):
        lab.write('zone', 'create', 'private.test', **request)
    helper = lab.put('/usr/local/sbin/bind-private-dnssec', '# helper\n', 0o700)
    lab.write('zone', 'create', 'private.test', **request)
    assert '// Bind9-Ansible DNSSEC-PRIVATE private.test\n' in (lab.root / 'etc/named.conf').read_text()
    assert any(str(helper) in call for call in lab.calls)
    lab.write('zone', 'update', 'private.test', forwarders=request['forwarders'], dnssec_validate=True)
    assert 'DNSSEC-PRIVATE private.test' not in (lab.root / 'etc/named.conf').read_text()
    with pytest.raises(M.ManagementError, match='YAML boolean'):
        lab.write('zone', 'update', 'private.test', forwarders=request['forwarders'], dnssec_validate='false')


def test_unsafe_backup_tool_or_invalid_archive_result_prevents_publication(lab):
    tool = lab.root / 'usr/local/sbin/bind-configtool'
    tool.chmod(0o755)
    with pytest.raises(M.ManagementError, match='backup tool'):
        lab.write(value='no')
    tool.chmod(0o700)
    runner = lab.runner
    def bad_archive(argv):
        return 'not an archive' if Path(argv[0]).name == 'bind-configtool' else runner(argv)
    manager = lab.manager(action='update', value='no', expected_checksum=lab.manager().run()['checksum'])
    manager.runner = bad_archive
    with pytest.raises(M.ManagementError, match='recovery archive'):
        manager.run()
    assert (lab.root / 'etc/named.conf').read_text() == CONF


def test_health_failure_rolls_back(lab, monkeypatch):
    runner = lab.runner
    monkeypatch.setattr(M.time, 'sleep', lambda seconds: None)
    def unhealthy(argv):
        return ';; status: SERVFAIL' if Path(argv[0]).name == 'dig' else runner(argv)
    manager = lab.manager(action='update', value='no', expected_checksum=lab.manager().run()['checksum'])
    manager.runner = unhealthy
    with pytest.raises(M.ManagementError, match='rolled-back'):
        manager.run()
    assert (lab.root / 'etc/named.conf').read_text() == CONF


def test_unsafe_state_directory_is_never_repaired_silently(lab):
    path = lab.root / 'var/lib/Bind9-Ansible'
    path.mkdir(parents=True, mode=0o755)
    with pytest.raises(M.ManagementError, match='0700'):
        with lab.manager().lease():
            pytest.fail('unsafe state accepted')
    path.chmod(0o777)
    with pytest.raises(M.ManagementError, match='Unsafe'):
        with lab.manager().lease():
            pytest.fail('unsafe state accepted')


def test_read_primary_serial_and_option_existence_contract(lab):
    assert lab.manager('zone', 'read', 'example.test').run()['serial'] == '41'
    for action, name in [('create', 'minimal-responses'), ('update', 'absent-option')]:
        with pytest.raises(M.ManagementError, match='Option'):
            lab.write(action=action, name=name, value='yes')


def test_publication_failure_before_replace_leaves_original_intact(lab, monkeypatch):
    monkeypatch.setattr(M.Manager, 'publish', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('disk full before rename')))
    with pytest.raises(M.ManagementError, match='rolled-back'):
        lab.write(value='no')
    assert (lab.root / 'etc/named.conf').read_text() == CONF


def test_failed_reload_after_rollback_retains_recovery_lease(lab):
    def reject(argv):
        if Path(argv[0]).name == 'rndc':
            raise M.ManagementError('RNDC unavailable')
    lab.fail = reject
    with pytest.raises(M.ManagementError, match='recovery-required'):
        lab.write(value='no')
    assert (lab.root / 'etc/named.conf').read_text() == CONF
    assert (lab.root / 'var/lib/Bind9-Ansible/.operation.lock').exists()


def test_unsafe_dnssec_helper_refused_before_publish(lab):
    lab.put('/etc/named.conf', CONF.replace('dnssec-validation no', 'dnssec-validation auto'))
    lab.put('/usr/local/sbin/bind-private-dnssec', '# helper\n', 0o777)
    with pytest.raises(M.ManagementError, match='unsafe private DNSSEC helper'):
        lab.write('zone', 'create', 'private.test', kind='forward', forwarders=[{'address': '10.10.0.53'}], dnssec_validate=False)
    assert not lab.manager('zone', 'read', 'private.test').run()['found']


@pytest.mark.parametrize('resource,action,fields', [
    ('option', 'delete', {'value': 'yes'}), ('option', 'read', {'value': 'yes'}),
    ('record', 'delete', {'zone': 'example.test', 'type': 'A', 'values': ['192.0.2.80']}),
    ('zone', 'read', {'kind': 'forward'}),
])
def test_fields_unused_by_requested_action_are_refused(lab, resource, action, fields):
    with pytest.raises(M.ManagementError, match='Unknown request fields'):
        lab.manager(resource, action, 'example.test', **fields)


@pytest.mark.parametrize('action,name,fields', [
    ('create', 'new.test', {'kind': 'forward', 'forwarders': [{'address': '10.0.0.53'}], 'ttl': 300}),
    ('create', 'new.test', {'kind': 'authoritative', 'dnssec_validate': False}),
    ('update', 'example.test', {'forwarders': [{'address': '10.0.0.53'}]}),
])
def test_fields_for_another_zone_kind_are_refused(lab, action, name, fields):
    with pytest.raises(M.ManagementError):
        lab.write('zone', action, name, **fields)


def test_forward_update_cannot_smuggle_other_zone_settings(lab):
    lab.write('zone', 'create', 'forward.test', kind='forward', forwarders=[{'address': '10.0.0.53'}])
    with pytest.raises(M.ManagementError, match='Use forwarders'):
        lab.write('zone', 'update', 'forward.test', settings={'notify': 'yes'})


@pytest.mark.parametrize('root', ['relative', '/var/../tmp', '/var/backups/'])
def test_noncanonical_backup_root_refused(lab, root):
    manager = lab.manager(action='update', value='no', expected_checksum=lab.manager().run()['checksum'])
    manager.context['backup_root'] = root
    with pytest.raises(M.ManagementError, match='backup root'):
        manager.run()


def test_manual_target_change_during_health_prevents_false_success(lab):
    runner = lab.runner
    def intervene(argv):
        result = runner(argv)
        if Path(argv[0]).name == 'dig':
            lab.put('/etc/named.conf', CONF + '// manual edit during successful health\n')
        return result
    manager = lab.manager(action='update', value='no', expected_checksum=lab.manager().run()['checksum'])
    manager.runner = intervene
    with manager.lease():
        with pytest.raises(M.ManagementError, match='recovery-required'):
            manager.run()
    assert 'manual edit during' in (lab.root / 'etc/named.conf').read_text()
    assert (lab.root / 'var/lib/Bind9-Ansible/.operation.lock').is_dir()


def test_static_signed_zone_data_is_not_modified_without_its_signer(lab):
    source = ZONE + '@ IN DNSKEY 257 3 13 AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==\n'
    lab.put('/var/named/zones/example.test.zone', source)
    with pytest.raises(M.ManagementError, match='signing workflow'):
        lab.write('record', 'update', 'www', zone='example.test', type='A', values=['192.0.2.99'])
    assert (lab.root / 'var/named/zones/example.test.zone').read_text() == source
    assert not lab.calls


def test_read_soa_remains_available_while_writes_are_protected(lab):
    result = lab.manager('record', 'read', '@', zone='example.test', type='SOA').run()
    assert result['found'] and result['records'][0]['ttl'] == '300'
    assert ' 41 3600 ' in result['values'][0]


def test_explicit_trust_anchor_is_in_the_static_revision(lab):
    lab.put('/etc/named.conf', CONF.replace('minimal-responses yes;', 'minimal-responses yes; bindkeys-file "/etc/named.root.key";'))
    lab.put('/etc/named.root.key', '// native anchor fixture\n')
    first = lab.manager().run()['checksum']
    config = M.Configuration(M.SafeFiles(lab.root))
    assert config.contents['/etc/named.root.key'] == b'// native anchor fixture\n'
    lab.put('/etc/named.root.key', '// changed native anchor fixture\n')
    assert lab.manager().run()['checksum'] != first


def test_recreated_secondary_cannot_serve_a_retained_stale_serial(lab):
    lab.put('/var/named/slaves/new.test.zone', ZONE.replace('41 3600', '999 3600'))
    manager = lab.manager('zone', 'create', 'new.test', kind='authoritative', expected_checksum=lab.manager().run()['checksum'])
    manager.context['primary'] = False
    with pytest.raises(M.ManagementError, match='pre-existing secondary'):
        manager.run()
    assert '999 3600' in (lab.root / 'var/named/slaves/new.test.zone').read_text()
    assert not lab.calls


def test_escaped_rdata_semicolon_is_not_misread_as_a_comment(lab):
    lab.put('/var/named/zones/example.test.zone', ZONE + 'plain IN TXT a\\;b\n')
    result = lab.manager('record', 'read', 'plain', zone='example.test', type='TXT').run()
    assert result['values'] == ['a\\;b']
    lab.write('record', 'update', 'www', zone='example.test', type='A', values=['192.0.2.99'])
    assert 'plain IN TXT a\\;b\n' in (lab.root / 'var/named/zones/example.test.zone').read_text()
