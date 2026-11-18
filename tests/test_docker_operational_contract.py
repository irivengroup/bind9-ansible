"""Pure contracts for the native boot/static transition, preserving all refusals."""
import copy
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

SPEC = importlib.util.spec_from_file_location('bind9_docker_recipe_contract',
    Path(__file__).resolve().parent / 'docker/controller_recipe.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
Expectation = MODULE.RestartRouteExpectation


class TestDockerRestartExpectation:
    """Observed protocol provenance cannot conceal lost or altered routes."""

    @staticmethod
    def snapshots():
        seed = {'dst':'203.0.113.0/24','gateway':'192.0.2.1','dev':'eth1',
                'protocol':'static','metric':70,'flags':[]}
        initial = {'primary':{'routes':[copy.deepcopy(seed)]}}
        routes = [copy.deepcopy(seed)] + [dict(dst=target,gateway='192.0.2.1',
            dev='eth1',metric=100,flags=[]) for target in Expectation.TARGETS]
        before = {'primary':dict(routes=routes,profile='exact persistent routes',
                                 files_and_profiles='exact protected bytes and identities')}
        after = copy.deepcopy(before)
        for row in after['primary']['routes'][1:]:
            row['protocol'] = 'static'
        return before, after, initial

    @pytest.mark.parametrize('origin', [None, 'boot', 'static'])
    def test_only_recorded_native_origin_transition_is_expected(self, origin):
        before, after, initial = self.snapshots()
        if origin is not None:
            for row in before['primary']['routes'][1:]:
                row['protocol'] = origin
        assert before != after if origin != 'static' else before == after
        Expectation.verify(before, after, initial)

    @pytest.mark.parametrize('changes', [
        {'gateway':'192.0.2.254'}, {'dev':'eth2'}, {'metric':101},
        {'protocol':'dhcp'}, {'prefsrc':'192.0.2.99'}, {'flags':['onlink']}])
    def test_changed_owned_route_property_stays_refused(self, changes):
        before, after, initial = self.snapshots()
        after['primary']['routes'][1].update(changes)
        with pytest.raises(AssertionError):
            Expectation.verify(before, after, initial)

    @pytest.mark.parametrize('mutation', ['missing','duplicate','extra'])
    def test_missing_or_additional_routes_stay_refused(self, mutation):
        before, after, initial = self.snapshots()
        routes = after['primary']['routes']
        if mutation == 'missing':
            routes.pop()
        elif mutation == 'duplicate':
            routes.append(copy.deepcopy(routes[-1]))
        else:
            routes.append(dict(dst='198.51.100.1',dev='eth2',gateway='10.154.0.1'))
        with pytest.raises(AssertionError):
            Expectation.verify(before, after, initial)

    def test_existing_route_protocol_is_never_normalized(self):
        before, after, initial = self.snapshots()
        after['primary']['routes'][0]['protocol'] = 'boot'
        with pytest.raises(AssertionError):
            Expectation.verify(before, after, initial)

    def test_unknown_origin_before_boot_is_not_hidden(self):
        before, after, initial = self.snapshots()
        before['primary']['routes'][1]['protocol'] = 'dhcp'
        with pytest.raises(AssertionError):
            Expectation.verify(before, after, initial)

    @pytest.mark.parametrize('field', ['profile','files_and_profiles'])
    def test_configuration_or_identity_drift_stays_refused(self, field):
        before, after, initial = self.snapshots()
        after['primary'][field] = 'changed'
        with pytest.raises(AssertionError):
            Expectation.verify(before, after, initial)

    def test_expectation_does_not_modify_native_observations(self):
        before, after, initial = self.snapshots()
        protected = copy.deepcopy((before, after, initial))
        Expectation.verify(before, after, initial)
        assert (before, after, initial) == protected


@pytest.mark.parametrize('script', ['recipe.py','controller_recipe.py','packet_probe.py'])
@pytest.mark.parametrize('optimization', ['-O','-OO'])
def test_optimized_recipe_entrypoints_refuse_before_privileged_work(script, optimization):
    """No disabled assertion may authorize cleanup, export or packet acceptance."""
    entrypoint = Path(__file__).resolve().parent / 'docker' / script
    result = subprocess.run([sys.executable, '-B', optimization, str(entrypoint), '--help'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    assert result.returncode != 0
    assert b'Optimized Python is forbidden for the Docker validation recipe' in result.stderr
