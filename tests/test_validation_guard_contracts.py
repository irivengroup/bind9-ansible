"""Native predicate contracts with original YAML positions and explicit inputs.

These checks exercise defensive guards independently. They do not claim that
an unreachable action ran, or replace the complete preflight/rollback tests.
"""
import copy
from pathlib import Path

import pytest
import yaml

from test_ansible_canonical_predicates import CanonicalPredicates
from test_native_preflight import scope


def settings():
    """Reuse actual defaults and the existing public synthetic peer fixture."""
    hosts, _groups = scope()
    root = Path(__file__).resolve().parents[1]
    values = yaml.safe_load((root / 'roles/bind9/defaults/main.yml').read_text())
    values.update(hosts['localhost'])
    values.update(hostvars=hosts, group_names=['dns_primary'], ansible_host='127.0.0.1')
    return values


@pytest.mark.parametrize('field', ['bind_tsig_key_name', 'bind_tsig_algorithm', 'bind_tsig_secret',
                                 'bind_front_ip', 'bind_back_ip'])
def test_mandatory_identity_inputs_must_be_defined(field):
    values = settings()
    values.pop(field)
    CanonicalPredicates('deploy/validate.yml').check('Validate template input types before rendering', values, False)


@pytest.mark.parametrize('field', ['bind_authoritative_zones', 'bind_forward_zones'])
def test_optional_container_type_guard_does_not_imply_the_remaining_preflight_succeeded(field):
    values = settings()
    values.pop(field)
    CanonicalPredicates('deploy/validate.yml').check('Validate template input types before rendering', values, True)


@pytest.mark.parametrize('fault', [
    'no-role', 'both-roles', 'missing-adm', 'missing-local-front', 'missing-local-back', 'adm-is-back', 'adm-is-front', 'same-local-address',
    'short-secret', 'invalid-clients', 'invalid-forward-list', 'invalid-authoritative-list',
    'same-peer-back', 'duplicate-authority', 'authority-forward-collision', 'missing-primary-front',
    'missing-secondary-front', 'invalid-primary-front', 'invalid-secondary-front',
    'invalid-primary-back', 'invalid-secondary-back', 'duplicate-forward',
])
def test_platform_guard_independently_rejects_invalid_inputs(fault):
    values = settings()
    peers = values['hostvars']
    if fault == 'no-role': values['group_names'] = []
    elif fault == 'both-roles': values['group_names'] = ['dns_primary', 'dns_secondary']
    elif fault == 'missing-adm': values.pop('ansible_host')
    elif fault in ('missing-local-front', 'missing-local-back'): values.pop('bind_' + fault.rsplit('-', 1)[1] + '_ip')
    elif fault == 'adm-is-back': values['ansible_host'] = values['bind_back_ip']
    elif fault == 'adm-is-front': values['ansible_host'] = values['bind_front_ip']
    elif fault == 'same-local-address': values['bind_back_ip'] = values['bind_front_ip']
    elif fault == 'short-secret': values['bind_tsig_secret'] = 'short'
    elif fault == 'invalid-clients': values['bind_allowed_clients'] = None
    elif fault == 'invalid-forward-list': values['bind_forward_zones'] = None
    elif fault == 'invalid-authoritative-list': values['bind_authoritative_zones'] = None
    elif fault == 'same-peer-back': peers['peer']['bind_back_ip'] = peers['localhost']['bind_back_ip']
    elif fault == 'duplicate-authority': values['bind_managed_zones'].append(copy.deepcopy(values['bind_managed_zones'][0]))
    elif fault == 'authority-forward-collision': values['bind_forward_zones'][0]['domain'] = 'example.test'
    elif fault.startswith('missing-'):
        peers['localhost' if 'primary' in fault else 'peer'].pop('bind_front_ip')
    elif fault.startswith('invalid-primary-') or fault.startswith('invalid-secondary-'):
        peers['localhost' if 'primary' in fault else 'peer']['bind_' + fault.rsplit('-', 1)[1] + '_ip'] = 'not-an-ip'
    else:
        values['bind_forward_zones'].append(copy.deepcopy(values['bind_forward_zones'][0]))
    CanonicalPredicates('deploy/validate.yml').check('Validate supported platform and mandatory inputs', values, False)


@pytest.mark.parametrize('groups,back,expected', [
    (['dns_primary'], '192.0.2.2', True), (['dns_secondary'], '192.0.2.3', True),
    (['dns_primary'], '192.0.2.3', False), (['dns_secondary'], '192.0.2.2', False),
    ([], '192.0.2.2', False)])
def test_peer_binding_is_role_specific(groups, back, expected):
    values = settings()
    values.update(group_names=groups, bind_back_ip=back)
    CanonicalPredicates('deploy/validate.yml').check('Validate configured peer identity', values, expected)


def test_site_transfer_key_cannot_be_the_administrative_key():
    values = settings()
    values['bind_tsig_key_name'] = 'RNDC-KEY'
    CanonicalPredicates('deploy/validate.yml').check('Keep the administrative RNDC key separate from site transfers', values, False)


@pytest.mark.parametrize('task,item', [
    ('Validate each conditional forwarding zone', {'forwarders': [{'address': '192.0.2.53'}]}),
    ('Validate each conditional forwarding zone', {'domain': 'upstream.test'}),
    ('Validate each forwarding destination', [{}, {}]),
    ('Validate each forwarding destination', [{}, {'address': ''}]),
    ('Reject malformed DNS suffixes', {'domain': 'bad/name.test'}),
    ('Validate authoritative zone definitions', {'domain': 'example.test'}),
    ('Validate authoritative resource records', [{}, {'name': 'www', 'type': 'A'}]),
    ('Reject explicit reverse masks in authoritative records', [{}, {'name': 'www', 'mask': 24}]),
])
def test_zone_guards_refuse_missing_or_malformed_fields(task, item):
    values = settings()
    values['item'] = item
    CanonicalPredicates('deploy/validate.yml').check(task, values, False)


@pytest.mark.parametrize('domain', [None, 17, '*.example.test', 'bad/name.test'])
def test_authoritative_names_are_refused_before_rendering_zone_files(domain):
    values = settings()
    values['item'] = dict(domain=domain, serial=1, records=[])
    CanonicalPredicates('deploy/validate.yml').check('Validate authoritative zone definitions', values, False)


def test_final_topology_guard_independently_rejects_an_authority_forward_collision():
    from ansible.errors import AnsibleError
    values = settings()
    values['bind_forward_zones'] = [dict(domain='example.test', forwarders=[{'address': '192.0.2.53'}])]
    # This filter reports the precise topology error instead of returning False.
    with pytest.raises((ValueError, AnsibleError), match='zone names must be unique') as caught:
        CanonicalPredicates('deploy/validate.yml').check(
            'Validate DNS topology and private forwarding delegation cuts', values, False)
    # Ansible 2.9 wraps conditional failures; retain the exact validator cause
    # rather than accepting any unrelated loader or templating error.
    error = caught.value
    assert type(error) in (ValueError, AnsibleError)
    cause = error.__context__ if type(error) is AnsibleError else error
    assert type(cause) is ValueError and 'zone names must be unique' in str(cause)


def test_topology_assertion_refuses_a_false_result_from_its_filter_provider(monkeypatch):
    """Keep the consumer fail-closed if a plugin resolves to a false provider.

    The production validator retains its True-or-ValueError contract. This
    fault exercises the Ansible assertion boundary, without changing that
    validator or inventing an observation for its Python implementation.
    """
    import test_ansible_canonical_predicates as predicates
    values = settings()
    native_templar = predicates.Templar
    calls = []

    def failed_provider(authoritative, forwarded):
        calls.append((authoritative, forwarded))
        return False

    def templar_with_failed_provider(*args, **kwargs):
        templar = native_templar(*args, **kwargs)
        if hasattr(templar, '_get_filters'):
            # EL8's Ansible 2.9 copies this per-instance cache into its render
            # environment. Populate the real cache before replacing one entry.
            filters = templar._get_filters()
            assert callable(filters['dns_topology_valid'])
            filters['dns_topology_valid'] = failed_provider
            templar._filters = filters
            return templar
        # Ansible's per-environment registry leaves the global loader intact.
        # Resolve the lazy entry first, so its initial lookup cannot replace
        # this deliberately failing provider with the production function.
        assert callable(templar.environment.filters['dns_topology_valid'])
        templar.environment.filters['dns_topology_valid'] = failed_provider
        return templar

    monkeypatch.setattr(predicates, 'Templar', templar_with_failed_provider)
    CanonicalPredicates('deploy/validate.yml').check(
        'Validate DNS topology and private forwarding delegation cuts', values, False)
    assert calls == [(values['bind_managed_zones'], values['bind_forward_zones'])]


@pytest.mark.parametrize('kind', ['directory', 'file'])
def test_completion_seal_state_requires_an_actual_directory(kind):
    values = {'item': {'item': '/var/lib/Bind9-Ansible', 'stat': {
        'exists': True, 'islnk': False, 'uid': 0, 'isdir': kind == 'directory', 'mode': '0700'}}}
    CanonicalPredicates('deploy/guard.yml').check(
        'Reject unsafe deployment seal paths regardless of the requested policy', values, kind == 'directory')


@pytest.mark.parametrize('tags,expected', [(['hibernate'], True), (['preflight'], False)])
def test_hibernation_guard_does_not_infer_dispatch_from_a_tag(tags, expected):
    CanonicalPredicates('../main.yml').check('Hibernate only the existing deployment without provisioning',
        {'ansible_run_tags': tags}, expected, key='when')


@pytest.mark.parametrize('enabled,clients,deferred,standalone,protected', [
    (False, [], False, False, False), (False, [], True, False, False),
    (False, ['203.0.113.0/24'], False, False, False), (False, ['203.0.113.0/24'], True, False, False),
    (True, [], False, False, False), (True, [], True, False, False),
    (True, ['203.0.113.0/24'], False, True, False), (True, ['203.0.113.0/24'], True, False, True),
])
def test_route_dispatch_keeps_standalone_and_protected_paths_distinct(enabled, clients, deferred, standalone, protected):
    values = dict(bind_front_routes_enabled=enabled, bind_cloud_clients=clients, bind_front_routes_defer_commit=deferred)
    guards = CanonicalPredicates('deploy/main.yml')
    guards.check('Add only return routes when explicitly running the routes tag', values, standalone, key='when')
    guards.check('Add return routes as part of the protected DNS deployment', values, protected, key='when')


@pytest.mark.parametrize('value,expected', [(True, True), (False, True), ('true', False), (1, False), (None, False)])
def test_route_management_setting_requires_a_real_boolean(value, expected):
    CanonicalPredicates('deploy/main.yml').check('Require explicit boolean route management setting',
        {'bind_front_routes_enabled': value}, expected)


@pytest.mark.parametrize('enabled', [True, False])
def test_monitoring_and_disabled_route_guards_have_independent_outcomes(enabled):
    guards = CanonicalPredicates('deploy/main.yml')
    guards.check('Configure periodic DNS health monitoring', {'bind_monitoring_enabled': enabled}, enabled, key='when')
    guards.check('Report disabled FRONT route management', {'bind_front_routes_enabled': enabled}, not enabled, key='when')


@pytest.mark.parametrize('pending,attempted,expected', [(True, True, False), (True, False, True), (False, False, False)])
def test_route_rollback_is_owned_and_not_repeated(pending, attempted, expected):
    CanonicalPredicates('deploy/main.yml').check('Roll back owned FRONT route additions',
        dict(bind_front_route_transaction_pending=pending, bind_front_route_rollback_attempted=attempted), expected, key='when')


@pytest.mark.parametrize('ready', [True, False])
def test_runtime_restoration_requires_its_own_snapshot(ready):
    CanonicalPredicates('deploy/main.yml').check('Restore managed runtime files from before the failed deployment',
        {'bind_runtime_snapshot_ready': ready}, ready, key='when')


@pytest.mark.parametrize('failed,incomplete,active,restart,resume', [
    (False, False, 0, True, True), (False, False, 3, True, False),
    (False, True, 0, False, False), (True, False, 0, False, False)])
def test_services_stay_stopped_after_incomplete_or_failed_recovery(failed, incomplete, active, restart, resume):
    values = dict(bind_rollback={'failed': failed}, bind_runtime_restore_incomplete=incomplete,
                  bind_dnssec_timer_active={'rc': active})
    guards = CanonicalPredicates('deploy/main.yml')
    guards.check('Restore pre-deployment BIND service state', values, restart, key='when')
    guards.check('Resume previously active private DNSSEC renewal', values, resume, key='when')


@pytest.mark.parametrize('secondary,populated,expected', [(False, False, False), (False, True, False),
                                                        (True, False, False), (True, True, True)])
def test_primary_readiness_requires_a_secondary_with_managed_zones(secondary, populated, expected):
    CanonicalPredicates('deploy/main.yml').check('Verify local primary before secondary deployment',
        dict(bind_secondary_group='dns_secondary', group_names=['dns_secondary'] if secondary else ['dns_primary'],
             bind_managed_zones=[{'domain': 'example.test'}] if populated else []), expected, key='when')


@pytest.mark.parametrize('primary', [True, False])
def test_reverse_file_catalog_runs_only_on_the_primary(primary):
    CanonicalPredicates('deploy/main.yml').check('Find existing reverse zone files before deployment',
        dict(bind_primary_group='dns_primary', group_names=['dns_primary'] if primary else ['dns_secondary']), primary, key='when')


@pytest.mark.parametrize('domains', [
    ['upstream.test', 'upstream.test'], ['upstream.test', 'child.upstream.test']])
def test_suffix_overlap_guard_rejects_duplicate_or_nested_forwarding_in_isolation(domains):
    values = settings()
    values['bind_forward_zones'] = [dict(domain=domain, forwarders=[{'address': '192.0.2.53'}])
                                    for domain in domains]
    values['item'] = values['bind_forward_zones'][0]
    CanonicalPredicates('deploy/validate.yml').check('Reject overlapping conditional DNS suffixes', values, False)
