"""Native connected routes keep src metadata and may serve a secondary /32."""
import copy

import pytest

from test_front_kernel_gateway import lab_revision, pytestmark as native_marks

pytestmark = native_marks


@pytest.mark.parametrize('clients', [['198.51.100.0/24'], ['198.51.100.25']])
def test_normal_kernel_preferred_source_does_not_conflict_with_direct_clients(tmp_path, clients):
    lab = lab_revision(tmp_path)
    lab.data['runtime'][1]['prefsrc'] = '198.51.100.2'
    before = copy.deepcopy(lab.data)
    result = lab.run(clients=clients)
    assert result.returncode == 0, result.stdout
    assert lab.data == before and not (lab.state / 'front-routes.pending').exists()


@pytest.mark.parametrize('clients,changed', [(['203.0.113.25'], True), (['198.51.100.25'], False),
                                           (['198.51.100.0/24'], False)])
def test_secondary_host_address_uses_the_same_devices_primary_connected_subnet(tmp_path, clients, changed):
    lab = lab_revision(tmp_path)
    lab.data['addresses'][0]['addr_info'] = [
        {'family': 'inet', 'local': '198.51.100.10', 'prefixlen': 24},
        {'family': 'inet', 'local': '198.51.100.2', 'prefixlen': 32, 'secondary': True}]
    lab.data['runtime'][1]['prefsrc'] = '198.51.100.10'
    before = copy.deepcopy(lab.data)
    result = lab.run(clients=clients)
    assert result.returncode == (2 if changed else 0), result.stdout
    if changed:
        assert len(lab.data['persistent']) == 1
        again = copy.deepcopy(lab.data)
        assert lab.run(clients=clients).returncode == 0
        assert lab.data == again
    else:
        assert lab.data == before and not (lab.state / 'front-routes.pending').exists()


@pytest.mark.parametrize('source', ['10.0.0.2', '198.51.100.254', '198.51.100.999'])
def test_unknown_or_other_device_source_is_not_accepted_as_a_native_connected_route(tmp_path, source):
    lab = lab_revision(tmp_path)
    lab.data['runtime'][1]['prefsrc'] = source
    before = copy.deepcopy(lab.data)
    result = lab.run(clients=['198.51.100.0/24'])
    assert result.returncode == 1 and 'Conflicting R route' in result.stdout
    assert lab.data == before and not (lab.state / 'front-routes.pending').exists()


@pytest.mark.parametrize('active', [False, True])
def test_secondary_address_preserves_an_automatic_profile_metric(tmp_path, active):
    lab = lab_revision(tmp_path)
    lab.data['addresses'][0]['addr_info'] = [
        {'family': 'inet', 'local': '198.51.100.10', 'prefixlen': 24},
        {'family': 'inet', 'local': '198.51.100.2', 'prefixlen': 32, 'secondary': True}]
    lab.data['runtime'][1].update(prefsrc='198.51.100.10', metric=120)
    desired = {'network': '203.0.113.0/24', 'gateway': '198.51.100.1', 'metric': -1}
    lab.data.update(route_metric='-1', persistent=[copy.deepcopy(desired)])
    if active:
        lab.data['runtime'].append(dict(dst=desired['network'], gateway=desired['gateway'], dev='front0', metric=120))
    result = lab.run()
    assert result.returncode == (0 if active else 2), result.stdout
    assert lab.data['persistent'] == [desired]
    assert lab.data['runtime'][-1]['metric'] == 120
    assert not (lab.state / 'front-routes.pending').exists()


def test_contradictory_metrics_covering_front_are_not_guessed_for_an_automatic_profile(tmp_path):
    lab = lab_revision(tmp_path)
    lab.data['addresses'][0]['addr_info'].append({'family': 'inet', 'local': '198.51.100.10', 'prefixlen': 25})
    lab.data['runtime'][1]['metric'] = 120
    lab.data['runtime'].append(dict(dst='198.51.100.0/25', dev='front0', protocol='kernel', scope='link', metric=200))
    lab.data.update(route_metric='-1', persistent=[{'network': '203.0.113.0/24', 'gateway': '198.51.100.1', 'metric': -1}])
    before = copy.deepcopy(lab.data)
    result = lab.run()
    assert result.returncode == 1 and 'Ambiguous effective FRONT metric' in result.stdout
    assert lab.data == before and not (lab.state / 'front-routes.pending').exists()


def require_stable_automatic_metric(tmp_path, subnet, address, device, metric):
    """Only connected FRONT ranges containing its address establish its metric."""
    lab = lab_revision(tmp_path)
    if device == 'front0':
        lab.data['addresses'][0]['addr_info'].append(
            {'family': 'inet', 'local': address, 'prefixlen': int(subnet.split('/')[1])})
    lab.data['runtime'][1]['metric'] = 120
    lab.data['runtime'].append(dict(dst=subnet, dev=device, protocol='kernel', scope='link', metric=metric))
    lab.data.update(route_metric='-1', persistent=[{'network': '203.0.113.0/24', 'gateway': '198.51.100.1', 'metric': -1}])
    baseline = copy.deepcopy(lab.data['runtime'])
    result = lab.run()
    assert result.returncode == 2, result.stdout
    assert lab.data['runtime'][:-1] == baseline
    assert lab.data['runtime'][-1]['metric'] == 120
    assert lab.data['persistent'][0]['metric'] == -1
    again = copy.deepcopy(lab.data)
    assert lab.run().returncode == 0
    assert lab.data == again and not (lab.state / 'front-routes.pending').exists()


def test_an_unrelated_connected_subnet_does_not_change_fronts_automatic_metric(tmp_path):
    require_stable_automatic_metric(tmp_path, '198.51.101.0/24', '198.51.101.2', 'front0', 200)


@pytest.mark.parametrize('subnet,address,device,metric', [
    ('198.51.99.0/24', '198.51.99.2', 'front0', 200),
    ('10.0.0.0/24', '10.0.0.2', 'back0', 200),
    ('198.51.100.0/25', '198.51.100.10', 'front0', 120),
], ids=['lower-unrelated-front-range', 'other-device-connected-range', 'consistent-nested-front-range'])
def test_automatic_metric_ignores_unrelated_ranges_and_accepts_consistent_nested_ranges(
        tmp_path, subnet, address, device, metric):
    require_stable_automatic_metric(tmp_path, subnet, address, device, metric)
