"""Management YAML integration: real transport, censored results and DNS checks."""
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from native_ansible_runner import NativeFilesystemScope, NativeRecordedBoundary
from native_management_runner import NativeManagementRun
from test_management import CONF, ROOT_ZONE, ZONE
from test_native_directories import named_identity

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')

MANAGE = 'tasks.d/manage/main.yml'
READ = dict(resource='option', action='read', name='minimal-responses')
BATCH = dict(resource='batch', action='read', operations=[
    dict(resource='option', action='update', name='minimal-responses', value='yes')])
SECONDARY_ARGV = ['dig', '-r', '-4', '@127.0.0.1', '+tcp', '+time=2', '+tries=1',
                  '+norecurse', '+noall', '+comments', '+answer', 'example.test.', 'SOA']
SECONDARY_ANSWER = (';; status: NOERROR\n;; flags: qr aa; QUERY: 1, ANSWER: 1\n'
                    'example.test. 300 IN SOA ns. host. 41 3600 900 604800 60\n')


class ManagementWorkflow:
    """Publish owned synthetic input and observe real module effects/results."""

    def __init__(self, folder):
        self.folder, self.sequence = folder, 0
        Path('/var/named/zones').mkdir(parents=True, mode=0o750)
        for name, text in [('/etc/named.conf', CONF), ('/var/named/empty-root.zone', ROOT_ZONE),
                           ('/var/named/zones/example.test.zone', ZONE)]:
            path = Path(name)
            path.write_text(text, encoding='utf-8')
            path.chmod(0o640)
        self.before = {str(path): path.read_bytes() for path in
                       [Path('/etc/named.conf'), *Path('/var/named').rglob('*.zone')]}

    def run(self, request=None, secondary=False, peer_result=None, checks=(), replies=None,
            check=False, **variables):
        values = dict(bind_manage=READ if request is None else request,
            ansible_distribution='RedHat', ansible_distribution_major_version='8',
            bind_primary_host='peer' if secondary else 'localhost',
            bind_secondary_host='localhost' if secondary else 'peer', bind_tsig_key_name='site-transfer')
        values.update(variables)
        hosts = {name: dict(bind_front_ip='192.0.2.' + suffix, bind_back_ip='10.0.0.' + suffix)
                 for name, suffix in [('localhost', '11'), ('peer', '12')]}
        if peer_result is not None:
            hosts['peer']['bind_management_result'] = peer_result
        groups = dict(dns_primary=['peer'] if secondary else ['localhost'],
                      dns_secondary=['localhost'] if secondary else ['peer'])
        folder = self.folder / str(self.sequence)
        folder.mkdir(mode=0o700)
        self.sequence += 1
        with NativeRecordedBoundary(folder, NativeManagementRun, replies or {}) as state:
            status, result = NativeManagementRun(MANAGE, values,
                inventory_hosts=hosts, inventory_groups=groups, checks=checks, check_mode=check).run()
            calls = json.loads(state.read_text(encoding='utf-8'))['calls']
        assert {name: Path(name).read_bytes() for name in self.before} == self.before
        assert not Path('/var/backups/Bind9-Ansible').exists()
        assert not Path('/var/lib/Bind9-Ansible/.operation.lock').exists()
        return status, result, calls


@pytest.fixture
def management(tmp_path):
    with NativeFilesystemScope(NativeManagementRun):
        named_identity()
        yield ManagementWorkflow(tmp_path)


@pytest.mark.parametrize('resource,fields', [
    ('option', dict(name='minimal-responses')), ('zone', dict(name='example.test')),
    ('record', dict(name='www', zone='example.test', type='A'))])
@pytest.mark.parametrize('show', [False, True])
def test_native_management_reads_canonical_module_and_keeps_default_callback_censored(management, resource, fields, show):
    status, result, calls = management.run(dict(resource=resource, action='read', **fields),
        bind_manage_show_values=show, checks=['not bind_management_result.changed',
            'bind_management_result.checksum | length == 64', 'bind_management_result.found'])
    assert status == 0 and not result.failed and not calls
    assert 'Run the private management transaction' in result.ok
    assert 'Report the safe management outcome and revision' in result.ok
    assert ('Show only the selected object\'s values when explicitly requested' in result.ok) is show
    recorded = result.registers['bind_management_result']
    assert 'censored' in recorded and 'checksum' not in recorded and 'values' not in recorded


def test_native_management_unchanged_batch_preview_and_approved_apply_do_not_publish(management):
    for action in ('read', 'apply'):
        request = dict(BATCH, action=action)
        # The independent production planner derives approval from the actual
        # unchanged graph. No private module result is uncensored for a fixture.
        from test_management import M, CONTEXT
        import grp
        context = dict(CONTEXT, named_gid=grp.getgrnam('named').gr_gid,
                       backup_root='/var/backups/Bind9-Ansible/bind')
        planner = M.BatchManager(BATCH, context)
        with planner.operation():
            preview = planner.run()
        request.update(expected_checksum=preview['checksum'], expected_plan_checksum=preview['plan_checksum'])
        status, result, calls = management.run(request, checks=['not bind_management_result.changed',
            "bind_management_result.resource == 'batch'", 'bind_management_result.operations_count == 1'])
        assert status == 0 and not result.failed and not calls
        assert 'censored' in result.registers['bind_management_result']


@pytest.mark.parametrize('envelope,variables', [
    (dict(READ, action='update', value='no'), {}),
    (dict(BATCH, action='apply', expected_checksum='0' * 64), {}),
    (dict(READ, unknown='not-supported'), {}),
    (READ, dict(bind_manage_checksums={'localhost': '0' * 64})),
])
def test_native_management_controlled_refusals_preserve_files_and_censor_private_data(management, envelope, variables):
    # A per-host checksum has no effect on a read; use pagination so the real
    # module rejects that stale selector rather than relying on a fake result.
    if variables:
        envelope = dict(resource='option', action='list')
    status, result, calls = management.run(envelope, **variables)
    assert status == 2 and not calls
    assert result.failed == ['Run the private management transaction',
                             'Report the controlled management refusal without configuration content']
    assert 'censored' in result.registers['bind_management_result']


def test_native_management_secondary_record_skips_module_and_checks_primary_serials(management):
    primary = dict(skipped=False, serials={'example.test.': '41', '.': '7'})
    # The shared boundary records the executable's basename independently of
    # the absolute path used by the unchanged Ansible command task.
    argv = SECONDARY_ARGV
    replies = {json.dumps(argv): {'rc': 0, 'stdout': SECONDARY_ANSWER}}
    status, result, calls = management.run(dict(resource='record', action='read', name='www',
        zone='example.test', type='A'), secondary=True, peer_result=primary, replies=replies,
        bind_manage_show_values=True)
    assert status == 0 and not result.failed and calls == [argv], (status, result.failed, result.registers, calls)
    assert 'Run the private management transaction' in result.skipped
    assert 'Report the safe management outcome and revision' in result.skipped
    assert 'Show only the selected object\'s values when explicitly requested' in result.skipped


@pytest.mark.parametrize('peer', [None, dict(skipped=True)])
def test_native_management_secondary_refuses_missing_or_skipped_primary(management, peer):
    status, result, calls = management.run(dict(resource='record', action='read', name='www',
        zone='example.test', type='A'), secondary=True, peer_result=peer)
    assert status == 2 and not calls
    assert result.failed == ['Require a primary record operation before checking its secondary']


@pytest.mark.parametrize('variables', [
    dict(ansible_distribution='Debian'), dict(ansible_distribution_major_version='7'),
    dict(bind_manage=None), dict(bind_manage=[]), dict(bind_manage={}),
    dict(bind_manage=dict(READ, resource='unknown')), dict(bind_manage=dict(READ, action='apply')),
    dict(bind_manage=dict(BATCH, action='update')), dict(bind_manage_checksums=[]),
    dict(bind_manage_plan_checksums=[]), dict(bind_manage_show_values='yes'),
    dict(bind_operation_lock_enabled='false')])
def test_native_management_rejects_invalid_envelopes_before_module_dispatch(management, variables):
    status, result, calls = management.run(**variables)
    assert status == 2 and not calls
    assert result.failed == ['Validate the management platform and explicit request envelope']
    assert 'Run the private management transaction' not in result.ok
    assert not Path('/var/lib/Bind9-Ansible').exists()


@pytest.mark.parametrize('fault', ['rc', 'status', 'serial'])
def test_native_management_secondary_retries_real_until_operands(management, fault):
    argv, answer = SECONDARY_ARGV, SECONDARY_ANSWER
    valid = dict(rc=0, stdout=answer)
    invalid = copy.deepcopy(valid)
    if fault == 'rc': invalid['rc'] = 17
    elif fault == 'status': invalid['stdout'] = answer.replace('NOERROR', 'SERVFAIL')
    else: invalid['stdout'] = answer.replace(' 41 3600', ' 42 3600')
    status, result, calls = management.run(dict(resource='record', action='read', name='www',
        zone='example.test', type='A'), secondary=True,
        peer_result=dict(skipped=False, serials={'example.test.': '41'}),
        replies={json.dumps(argv): dict(sequence=[invalid, valid])})
    assert status == 0 and not result.failed and calls == [argv, argv]


@pytest.mark.parametrize('check,preflight', [(True, False), (False, True)])
def test_native_management_secondary_preflight_and_check_mode_do_not_query_dns(management, check, preflight):
    status, result, calls = management.run(dict(resource='record', action='read', name='www',
        zone='example.test', type='A'), secondary=True,
        peer_result=dict(skipped=False, serials={'example.test.': '41'}), check=check,
        _bind_manage_preflight=preflight)
    assert status == 0 and not result.failed and not calls
    assert 'Verify the secondary received changed primary zone SOAs' in result.skipped


@pytest.mark.parametrize('fault', ['missing', 'resource', 'action', 'digest'])
def test_native_management_secondary_batch_requires_the_matching_primary_phase(management, fault):
    envelope = dict(resource='batch', action='read', operations=[dict(resource='record', action='update',
        name='www', zone='example.test', type='A', values=['192.0.2.80'])])
    digest = hashlib.sha256(json.dumps(envelope['operations'], sort_keys=True).encode('utf-8')).hexdigest()
    primary = dict(resource='batch', action='read', batch_digest=digest)
    if fault == 'missing': primary = None
    elif fault == 'resource': primary['resource'] = 'record'
    elif fault == 'action': primary['action'] = 'apply'
    else: primary['batch_digest'] = '0' * 64
    status, result, calls = management.run(envelope, secondary=True, peer_result=primary)
    assert status == 2 and not calls
    assert result.failed == ['Require the primary batch phase before a secondary with record operations']
    assert not Path('/var/lib/Bind9-Ansible').exists()


def test_native_management_real_wrapper_resolves_site_peers_before_reading(management):
    # Populate the real inventory groups and host facts through the shared
    # runner rather than copying/importing the canonical tasks under new marks.
    hosts = {name: dict(bind_environment='test', bind_site_name='site', bind_front_ip='192.0.2.' + suffix,
                       bind_back_ip='10.0.0.' + suffix) for name, suffix in [('localhost', '11'), ('peer', '12')]}
    folder = management.folder / 'wrapper'
    folder.mkdir(mode=0o700)
    with NativeRecordedBoundary(folder, NativeManagementRun, {}):
        status, result = NativeManagementRun('manage.yml', dict(bind_manage=READ,
            ansible_distribution='RedHat', ansible_distribution_major_version='8',
            bind_environment='test', bind_site_name='site', bind_tsig_key_name='site-transfer'), inventory_hosts=hosts,
            inventory_groups=dict(site=['localhost', 'peer'], dns_primary=['localhost'], dns_secondary=['peer']),
            checks=['not bind_management_result.changed', 'bind_management_result.found']).run()
    assert status == 0 and not result.failed, (status, result.failed, result.facts, result.registers)
    assert result.facts['bind_primary_host'] == 'localhost' and result.facts['bind_secondary_host'] == 'peer'
    assert Path('/etc/named.conf').read_bytes() == management.before['/etc/named.conf']


def test_native_management_secondary_option_batch_without_records_needs_no_primary_batch(management):
    status, result, calls = management.run(BATCH, secondary=True,
        checks=['not bind_management_result.changed', "bind_management_result.resource == 'batch'"])
    assert status == 0 and not result.failed and not calls
    assert 'Require the primary batch phase before a secondary with record operations' in result.skipped
    assert 'Run the private management transaction' in result.ok


def test_native_management_matching_primary_batch_cannot_bypass_module_record_safety(management):
    envelope = dict(resource='batch', action='read', operations=[dict(resource='record', action='update',
        name='www', zone='example.test', type='SOA', values=['192.0.2.80'])])
    digest = hashlib.sha256(json.dumps(envelope['operations'], sort_keys=True).encode('utf-8')).hexdigest()
    status, result, calls = management.run(envelope, secondary=True,
        peer_result=dict(resource='batch', action='read', batch_digest=digest))
    assert status == 2 and not calls
    assert 'Require the primary batch phase before a secondary with record operations' in result.ok
    assert result.failed == ['Run the private management transaction',
                             'Report the controlled management refusal without configuration content']
    assert 'censored' in result.registers['bind_management_result']


@pytest.mark.parametrize('resource,groups', [('option', ['dns_secondary']), ('zone', ['dns_primary'])])
def test_native_management_replication_guard_refuses_nonreplication_contexts(resource, groups):
    """Execute the source-attributed guard, independently of module serial output.

    These native Conditional contract cases provide predicate outcomes only;
    they cannot provide action dispatch, module success or replication credit.
    """
    from test_ansible_canonical_predicates import CanonicalPredicates
    CanonicalPredicates('manage/main.yml').check('Verify the secondary received changed primary zone SOAs',
        dict(ansible_check_mode=False, bind_manage=dict(resource=resource), group_names=groups,
             bind_secondary_group='dns_secondary'), False, key='when')


def test_native_management_envelope_guard_refuses_an_undefined_request():
    from test_ansible_canonical_predicates import CanonicalPredicates
    CanonicalPredicates('manage/main.yml').check('Validate the management platform and explicit request envelope',
        dict(ansible_distribution='RedHat', ansible_distribution_major_version='8'), False)
