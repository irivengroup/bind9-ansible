"""DNS numeric aliases cannot evade ownership checks or duplicate RRset guards."""
import csv
from hashlib import sha256
import io
import os
from pathlib import Path
import re

import pytest

from test_management import CONTEXT, Lab, M, ZONE


def test_record_type_identities_match_the_pinned_independent_iana_registry():
    raw = (Path(__file__).parent / 'fixtures/iana_dns_types_20261123.csv').read_bytes()
    assert sha256(raw).hexdigest() == '52a1b8d3a42dae6a223772b687dc775a4c289fb9fb3004dfb54da13dfd36a568'
    expected = {row['TYPE']: int(row['Value']) for row in csv.DictReader(io.StringIO(raw.decode()))
                if row['Value'].isdigit() and re.fullmatch('[A-Z][A-Z0-9-]*', row['TYPE'])}
    expected['ANY'] = 255
    assert M.RecordType.CODES == expected
    for name, code in expected.items():
        assert M.RecordType.identity(name.lower()) == 'TYPE' + str(code)
        assert M.RecordType.identity('TYPE' + str(code)) == 'TYPE' + str(code)


@pytest.mark.parametrize('value,expected', [('TYPE00001', 'TYPE1'), ('type065280', 'TYPE65280'),
    ('TYPE65534', 'TYPE65534'), ('unknown-private-name', 'UNKNOWN-PRIVATE-NAME')])
def test_valid_numeric_padding_private_types_and_unknown_names_keep_native_presentation(value, expected):
    assert M.RecordType.identity(value) == expected


@pytest.mark.parametrize('value', [None, True, 1, 'TYPE0', 'TYPE000', 'TYPE65535', 'TYPE65536', 'TYPE100000'])
def test_type_identification_rejects_confused_values_and_reserved_or_out_of_range_numbers(value):
    with pytest.raises(M.ManagementError):
        M.RecordType.identity(value)


def test_numeric_padding_is_bounded_before_integer_conversion():
    assert M.RecordType.identity('TYPE' + '0' * 10000 + '48') == 'TYPE48'
    with pytest.raises(M.ManagementError):
        M.RecordType.identity('TYPE' + '9' * 10000)


@pytest.mark.parametrize('code', [6, 46, 47, 48, 50, 51, 59, 60])
@pytest.mark.parametrize('action', ['create', 'update', 'delete'])
def test_numeric_owned_types_are_refused_by_the_same_management_write_guard(code, action):
    request = dict(resource='record', action=action, zone='example.test', name='key', type='TYPE00' + str(code))
    if action != 'delete':
        request['values'] = ['opaque presentation']
    manager = M.Manager(request, CONTEXT)
    with pytest.raises(M.ManagementError):
        manager.record_identity('example.test.')
    read = M.Manager(dict(resource='record', action='read', zone='example.test', name='key', type='TYPE' + str(code)), CONTEXT)
    assert read.record_identity('example.test.') == ('key.example.test.', 'TYPE' + str(code))


def test_batch_cannot_target_one_rrset_through_its_textual_and_numeric_names():
    operations = [dict(resource='record', action='update', zone='example.test', name='app', type=kind,
                       values=['192.0.2.80']) for kind in ['A', 'TYPE0001']]
    with pytest.raises(M.ManagementError, match='same resource'):
        M.BatchManager(dict(resource='batch', action='read', operations=operations), CONTEXT)
    with pytest.raises(M.ManagementError, match='same resource'):
        M.DeploymentResources(dict(resource='batch', action='create', operations=operations), CONTEXT)


def test_zone_rrset_alias_updates_preserve_unrelated_records_and_requested_type_syntax():
    document = M.ZoneDocument(ZONE, 'example.test.')
    assert document.rrset('www.example.test.', 'TYPE1')[0]['tokens'][0].text == '192.0.2.80'
    text = document.change('update', 'www.example.test.', 'TYPE0001', ['192.0.2.99'], bump_serial=False)
    assert ' IN TYPE0001 192.0.2.99' in text
    assert 'txt IN TXT "a; b" "two strings"' in text
    final = M.ZoneDocument(text, 'example.test.')
    assert final.rrset('www.example.test.', 'A')[0]['tokens'][0].text == '192.0.2.99'
    assert final.serial().text == '41'


@pytest.mark.skipif(os.name != 'posix' or os.geteuid() != 0, reason='Requires private root-owned static files')
def test_numeric_dnskey_in_existing_data_still_blocks_an_unsigned_static_edit(tmp_path):
    lab = Lab(tmp_path)
    lab.put('/var/named/zones/example.test.zone', ZONE + '@ IN TYPE48 257 3 8 AA==\n')
    manager = M.Manager(dict(resource='record', action='update', zone='example.test', name='www',
                            type='A', values=['192.0.2.99']), CONTEXT, root=tmp_path, runner=lab.runner)
    manager.config = M.Configuration(manager.files)
    manager.plan()
    with pytest.raises(M.ManagementError, match='Signed primary data'):
        manager.normalize_serials()
    assert lab.calls == []
    assert (tmp_path / 'var/named/zones/example.test.zone').read_text() == ZONE + '@ IN TYPE48 257 3 8 AA==\n'
