"""New resources share revision checks, validation and recovery with existing CRUD."""
import json
import os
from pathlib import Path

import pytest

from test_management import CONTEXT, Lab, M, pytestmark as management_marks

pytestmark = management_marks


@pytest.fixture
def lab(tmp_path):
    return Lab(tmp_path)


@pytest.mark.parametrize('resource,name,first,second', [
    ('acl', 'office', '192.0.2.0/24;', '198.51.100.0/24;'),
    ('logging', 'logging', 'channel discard { null; }; category queries { discard; };',
        'channel discard { null; }; category default { discard; };'),
])
def test_complete_crud_listing_and_idempotence(lab, resource, name, first, second):
    assert lab.manager(resource, 'read', name).run()['found'] is False
    created = lab.write(resource, 'create', name, value=first)
    assert created['changed']
    assert first in lab.manager(resource, 'read', name).run()['value']
    listed = M.Manager(dict(resource=resource, action='list'), CONTEXT, root=lab.root, runner=lab.runner).run()
    assert any(item['name'] == name for item in listed['catalog'])
    assert lab.write(resource, 'update', name, value=first)['changed'] is False
    assert lab.write(resource, 'update', name, value=second)['changed']
    assert second in lab.manager(resource, 'read', name).run()['value']
    lab.write(resource, 'delete', name)
    assert lab.manager(resource, 'read', name).run()['found'] is False
    assert '// operator comment before options' in (lab.root / 'etc/named.conf').read_text()


@pytest.mark.parametrize('resource,name,value', [
    ('acl', 'dns_query_clients', 'any;'),
    ('acl', 'office', '192.0.2.0/24; }; options { recursion yes;'),
    ('acl', 'office', 'none; } extra {'),
    ('acl', 'office', 'include "/etc/foreign";'),
    ('logging', 'logging', 'channel unsafe { file "/etc/named.conf"; };'),
    ('logging', 'logging', 'include "/etc/foreign";'),
])
def test_unsafe_blocks_refused_before_publication(lab, resource, name, value):
    before = (lab.root / 'etc/named.conf').read_bytes()
    with pytest.raises(M.ManagementError):
        lab.write(resource, 'create', name, value=value)
    assert not lab.calls and (lab.root / 'etc/named.conf').read_bytes() == before


def test_mixed_batch_keeps_resource_identity_and_one_commit(lab):
    operations = [dict(resource='acl', action='create', name='minimal-responses', value='192.0.2.0/24;'),
        dict(resource='option', action='update', name='minimal-responses', value='no'),
        dict(resource='logging', action='create', name='logging', value='channel discard { null; }; category default { discard; };')]
    # The equal option/ACL name must reach native validation rather than being
    # incorrectly rejected as a duplicate of the other resource type.
    preview = M.BatchManager(dict(resource='batch', action='read', operations=operations), CONTEXT,
        root=lab.root, runner=lab.runner).run()
    applied = M.BatchManager(dict(resource='batch', action='apply', operations=operations,
        expected_checksum=preview['checksum'], expected_plan_checksum=preview['plan_checksum']), CONTEXT,
        root=lab.root, runner=lab.runner).run()
    assert applied['changed'] and applied['operations_count'] == 3
    assert sum(Path(call[0]).name == 'bind-configtool' for call in lab.calls) == 1
    assert json.loads(Path(applied['backup'], 'manifest.json').read_text())['status'] == 'committed'


@pytest.mark.parametrize('resource,name,value', [
    ('acl', 'office', '192.0.2.0/24;'),
    ('logging', 'logging', 'channel discard { null; };'),
])
def test_block_reload_failure_uses_existing_rollback(lab, resource, name, value):
    before = (lab.root / 'etc/named.conf').read_bytes()
    failed = []
    def fail_once(argv):
        if Path(argv[0]).name == 'rndc' and 'reload' in argv and not failed:
            failed.append(True)
            raise M.ManagementError('Controlled reload failure')
    lab.fail = fail_once
    with pytest.raises(M.ManagementError):
        lab.write(resource, 'create', name, value=value)
    assert failed and (lab.root / 'etc/named.conf').read_bytes() == before


@pytest.mark.parametrize('resource,name,body', [
    ('acl', 'office', '192.0.2.0/24;'),
    ('logging', 'logging', 'channel discard { null; };'),
])
def test_included_blocks_are_readable_but_not_overwritten(lab, resource, name, body):
    declaration = ('acl "office"' if resource == 'acl' else 'logging') + ' {' + body + '};\n'
    include = lab.put('/etc/operator.conf', declaration)
    main = lab.root / 'etc/named.conf'
    main.write_text(main.read_text() + '\ninclude "/etc/operator.conf";\n')
    before = main.read_bytes(), include.read_bytes()
    read = lab.manager(resource, 'read', name).run()
    assert read['found'] and read['source'] == '/etc/operator.conf'
    with pytest.raises(M.ManagementError, match='external/vendor'):
        lab.write(resource, 'delete', name)
    assert (main.read_bytes(), include.read_bytes()) == before and not lab.calls


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo', 'directory'])
def test_logging_refuses_unsafe_existing_log_destination(lab, kind):
    directory = lab.root / 'var/log/named'
    directory.mkdir(parents=True)
    target = directory / 'custom.log'
    foreign = lab.put('/etc/operator.conf', '// must remain untouched\n')
    if kind == 'symlink':
        target.symlink_to(foreign)
    elif kind == 'hardlink':
        os.link(str(foreign), str(target))
    elif kind == 'fifo':
        os.mkfifo(str(target))
    else:
        target.mkdir()
    with pytest.raises(M.ManagementError, match='Unsafe existing logging file'):
        lab.write('logging', 'create', 'logging', value='channel custom { file "/var/log/named/custom.log"; };')
    assert foreign.read_text() == '// must remain untouched\n' and not lab.calls


def test_logging_file_requires_deployed_directory(lab):
    with pytest.raises(M.ManagementError, match='existing deployed log directory'):
        lab.write('logging', 'create', 'logging', value='channel custom { file "/var/log/named/custom.log"; };')
    assert not lab.calls


@pytest.mark.parametrize('kind', ['symlink', 'file'])
def test_logging_parent_cannot_redirect_the_daemon(lab, kind):
    parent = lab.root / 'var/log'
    parent.mkdir()
    directory = parent / 'named'
    if kind == 'symlink':
        directory.symlink_to(lab.root / 'etc', target_is_directory=True)
    else:
        directory.write_text('operator file\n')
    before = (lab.root / 'etc/named.conf').read_bytes()
    with pytest.raises(M.ManagementError, match='existing deployed log directory'):
        lab.write('logging', 'create', 'logging', value='channel custom { file "/var/log/named/custom.log"; };')
    assert (lab.root / 'etc/named.conf').read_bytes() == before and not lab.calls


def test_new_log_preview_preserves_absence_and_creates_no_backup(lab):
    directory = lab.root / 'var/log/named'
    directory.mkdir(parents=True)
    checksum = lab.manager().run()['checksum']
    manager = lab.manager('logging', 'create', 'logging', expected_checksum=checksum,
        value='channel custom { file "/var/log/named/custom.log"; };')
    manager.check = True
    result = manager.run()
    assert result['check_mode'] and result['paths'] == ['/etc/named.conf']
    assert not (directory / 'custom.log').exists()
    assert not (lab.root / 'var/backups').exists()


@pytest.mark.skipif(not os.environ.get('BIND_MANAGEMENT_CHECKCONF'), reason='Requires real BIND checker')
@pytest.mark.parametrize('resource,name,value', [
    ('acl', 'office', 'not_an_existing_acl;'),
    ('acl', 'office', 'office;'),
    ('logging', 'logging', 'category queries { missing_channel; };'),
])
def test_native_semantic_refusal_preserves_live_bytes(lab, resource, name, value):
    before = (lab.root / 'etc/named.conf').read_bytes()
    with pytest.raises(M.ManagementError, match='Native checker rejected'):
        lab.write(resource, 'create', name, value=value)
    assert (lab.root / 'etc/named.conf').read_bytes() == before
    assert not any(Path(call[0]).name in ('bind-configtool', 'rndc') for call in lab.calls)


@pytest.mark.skipif(not os.environ.get('BIND_MANAGEMENT_CHECKCONF'), reason='Requires real BIND checker')
def test_native_refuses_deleting_acl_still_used_by_another_acl(lab):
    lab.write('acl', 'create', 'office', value='192.0.2.0/24;')
    lab.write('acl', 'create', 'clients', value='office;')
    before = (lab.root / 'etc/named.conf').read_bytes()
    with pytest.raises(M.ManagementError, match='Native checker rejected'):
        lab.write('acl', 'delete', 'office')
    assert (lab.root / 'etc/named.conf').read_bytes() == before


@pytest.mark.skipif(not os.environ.get('BIND_MANAGEMENT_CHECKCONF'), reason='Requires real BIND checker')
def test_native_batch_resolves_acl_reference_created_later_in_same_candidate(lab):
    operations = [dict(resource='acl', action='create', name='clients', value='office;'),
        dict(resource='acl', action='create', name='office', value='192.0.2.0/24;')]
    planner = M.BatchManager(dict(resource='batch', action='read', operations=operations), CONTEXT,
        root=lab.root, runner=lab.runner)
    preview = planner.run()
    result = M.BatchManager(dict(resource='batch', action='apply', operations=operations,
        expected_checksum=preview['checksum'], expected_plan_checksum=preview['plan_checksum']), CONTEXT,
        root=lab.root, runner=lab.runner).run()
    assert result['status'] == 'committed'
    assert lab.manager('acl', 'read', 'clients').run()['found']
    assert lab.manager('acl', 'read', 'office').run()['found']


@pytest.mark.parametrize('resource,name', [
    ('acl', 'office.example'), ('acl', 'x' * 64), ('acl', 'quoted"name'),
    ('logging', 'audit'),
])
def test_invalid_block_identity_cannot_enter_a_transaction(lab, resource, name):
    with pytest.raises(M.ManagementError):
        lab.manager(resource, 'create', name, value='none;')
    assert not lab.calls


@pytest.mark.parametrize('resource,name', [('acl', 'office'), ('logging', 'logging')])
@pytest.mark.parametrize('value', [None, '', ' \n '])
def test_block_body_is_required_before_any_external_command(lab, resource, name, value):
    before = (lab.root / 'etc/named.conf').read_bytes()
    with pytest.raises(M.ManagementError, match='nonempty BIND block body'):
        lab.write(resource, 'create', name, value=value)
    assert (lab.root / 'etc/named.conf').read_bytes() == before and not lab.calls


@pytest.mark.parametrize('resource,name,body', [
    ('acl', 'office', '192.0.2.0/24;'),
    ('logging', 'logging', 'channel discard { null; };'),
])
def test_block_existence_is_strict_for_all_write_actions(lab, resource, name, body):
    for action in ('update', 'delete'):
        with pytest.raises(M.ManagementError, match='does not exist'):
            lab.write(resource, action, name, **({'value': body} if action == 'update' else {}))
    lab.write(resource, 'create', name, value=body)
    before = (lab.root / 'etc/named.conf').read_bytes()
    with pytest.raises(M.ManagementError, match='already exists'):
        lab.write(resource, 'create', name, value=body)
    assert (lab.root / 'etc/named.conf').read_bytes() == before


@pytest.mark.parametrize('declaration', [
    'acl office extra { none; };',
    'acl office { none; }; acl OFFICE { any; };',
])
def test_ambiguous_or_unsupported_live_acl_cannot_be_adopted(lab, declaration):
    path = lab.root / 'etc/named.conf'
    path.write_text(path.read_text() + '\n' + declaration)
    before = path.read_bytes()
    with pytest.raises(M.ManagementError):
        lab.manager('acl', 'read', 'office').run()
    assert path.read_bytes() == before and not lab.calls


def test_acl_list_paginates_without_disclosing_bodies_in_catalog(lab):
    lab.write('acl', 'create', 'office_b', value='192.0.2.0/24;')
    lab.write('acl', 'create', 'office_a', value='198.51.100.0/24;')
    result = M.Manager(dict(resource='acl', action='list', offset=2, limit=1), CONTEXT,
        root=lab.root, runner=lab.runner).run()
    assert result['count'] == 1 and result['next_offset'] == 3 and result['total'] == 4
    assert result['items'][0]['name'] == 'office_a'
    assert result['catalog'] == [dict(name='office_a', source='/etc/named.conf')]


def test_logging_preserves_existing_log_contents(lab):
    directory = lab.root / 'var/log/named'
    directory.mkdir(parents=True)
    log = directory / 'managed.log'
    log.write_bytes(b'operator audit history\n')
    result = lab.write('logging', 'create', 'logging', value='channel managed { file "/var/log/named/managed.log"; };')
    assert result['changed'] and log.read_bytes() == b'operator audit history\n'


def test_batch_refuses_repeated_acl_identity_before_observing_the_server(lab):
    operations = [dict(resource='acl', action='create', name=name, value='192.0.2.0/24;')
        for name in ('office', 'OFFICE')]
    with pytest.raises(M.ManagementError, match='same resource'):
        M.BatchManager(dict(resource='batch', action='read', operations=operations), CONTEXT,
            root=lab.root, runner=lab.runner)
    assert not lab.calls
