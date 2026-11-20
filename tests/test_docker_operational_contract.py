"""Pure contracts for the native boot/static transition, preserving all refusals."""
import copy
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

SPEC = importlib.util.spec_from_file_location('bind9_docker_recipe_contract',
    Path(__file__).resolve().parent / 'docker/controller_recipe.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
Expectation = MODULE.RestartRouteExpectation


class TestDockerCommandTransport:
    """SSH warnings remain evidence, never input to the strict JSON decoder."""

    @staticmethod
    def recipe(tmp_path):
        recipe = MODULE.DockerComponentRecipe.__new__(MODULE.DockerComponentRecipe)
        recipe.out, recipe.events, recipe.env = tmp_path, [], dict(os.environ)
        recipe.hosts = {'primary': '192.0.2.11'}
        recipe.ssh_args = ['ssh', '-o', 'StrictHostKeyChecking=yes']
        return recipe

    @staticmethod
    def local_process(monkeypatch, tmp_path):
        native = subprocess.run

        def execute(argv, **options):
            # Only relocate the controller's fixed cwd; pipes and exit statuses
            # are exercised by an actual child process on every CI platform.
            assert options['cwd'] == '/lab'
            options['cwd'] = str(tmp_path)
            return native(argv, **options)

        monkeypatch.setattr(MODULE.subprocess, 'run', execute)

    def test_machine_json_keeps_real_stderr_separate_and_records_both(self, monkeypatch, tmp_path):
        self.local_process(monkeypatch, tmp_path)
        recipe = self.recipe(tmp_path)
        warning = b'/etc/bashrc: line 12: BASHRCSOURCED: unbound variable\n'
        data = b'[{"dst":"default","dev":"eth2"}]\n'
        output = recipe.run('route-json', [sys.executable, '-c',
            'import sys; sys.stdout.buffer.write(' + repr(data) + '); '
            'sys.stderr.buffer.write(' + repr(warning) + ')'])
        assert json.loads(output) == [{'dst': 'default', 'dev': 'eth2'}]
        assert (tmp_path / 'route-json.log').read_bytes() == data
        assert (tmp_path / 'route-json.stderr.log').read_bytes() == warning
        event = json.loads((tmp_path / 'events.json').read_text())[0]
        assert event['actual_exitcode'] == event['expected_exitcode'] == 0
        assert event['log_sha256'] == hashlib.sha256(data).hexdigest()
        assert event['stderr_log_sha256'] == hashlib.sha256(warning).hexdigest()

    def test_nonzero_process_keeps_both_streams_and_is_still_refused(self, monkeypatch, tmp_path):
        self.local_process(monkeypatch, tmp_path)
        recipe = self.recipe(tmp_path)
        with pytest.raises(AssertionError, match='native-command-refused'):
            recipe.run('denied', [sys.executable, '-c',
                'import sys; print("partial-data"); '
                'print("native-command-refused", file=sys.stderr); sys.exit(17)'])
        assert (tmp_path / 'denied.log').read_bytes().strip() == b'partial-data'
        assert (tmp_path / 'denied.stderr.log').read_bytes().strip() == b'native-command-refused'
        assert recipe.events[0]['actual_exitcode'] == 17
        assert recipe.events[0]['expected_exitcode'] == 0

    def test_machine_ssh_skips_startup_files_and_preserves_strict_shell(self, monkeypatch, tmp_path):
        recipe = self.recipe(tmp_path)
        calls = []

        def execute(label, argv, **options):
            calls.append((label, argv, options))
            return '[]'

        monkeypatch.setattr(recipe, 'run', execute)
        assert recipe.ssh('route', 'primary', 'ip -j -4 route show table main') == '[]'
        label, argv, options = calls.pop()
        assert label == 'route' and options == {'timeout': 90}
        assert argv[:-1] == recipe.ssh_args + ['root@192.0.2.11']
        assert shlex.split(argv[-1]) == ['bash', '--noprofile', '--norc', '-euc',
            'export SYSTEMD_LOG_LEVEL=warning; ip -j -4 route show table main']


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
