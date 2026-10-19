"""Regress lifecycle and durability failures identified by the global review."""
import importlib.util
import os
from pathlib import Path
import stat
from types import SimpleNamespace

from jinja2 import Environment, StrictUndefined
import pytest
import yaml

ROLE = Path(__file__).resolve().parents[1] / 'roles/bind9'
spec = importlib.util.spec_from_file_location('dnssec_review', ROLE / 'files/bind-private-dnssec.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def policy(domain):
    return '// Bind9-Ansible DNSSEC-PRIVATE ' + domain + '\nzone "' + domain + '" IN {\n type forward;\n};\n'


class TestGlobalReview:
    """Check observable contracts, with only RNDC and the lock wait substituted."""

    def test_combined_lab_refusal_never_attempts_a_native_service_stop(self, monkeypatch):
        """A refused test launch must have no service-side cleanup effects."""
        from integration_deploy_auto_routes import CombinedDeployment
        lab = CombinedDeployment('primary')
        calls = []
        def refuse():
            raise RuntimeError('Isolation precondition refused')
        monkeypatch.setattr(lab, 'prepare', refuse)
        monkeypatch.setattr(lab, 'command', lambda argv, **kwargs: calls.append(argv))
        with pytest.raises(RuntimeError, match='Isolation precondition refused'):
            lab.execute()
        assert calls == []

    @pytest.mark.parametrize('owned,current,stops', [(None, '50', False), ('50', None, False),
                                                    ('50', '51', False), ('50', '50', True)])
    def test_combined_lab_cleanup_requires_the_recorded_daemon_identity(self, tmp_path, monkeypatch, owned, current, stops):
        """A stale/missing ownership receipt must preserve any other daemon."""
        from integration_deploy_auto_routes import CombinedDeployment
        lab = CombinedDeployment('primary')
        lab.isolated = True
        lab.owned_named, lab.native_named = tmp_path / 'owned.pid', tmp_path / 'actual.pid'
        if owned is not None:
            lab.owned_named.write_text(owned)
        if current is not None:
            lab.native_named.write_text(current)
        calls = []
        monkeypatch.setattr(lab, 'command', lambda argv, **kwargs: calls.append(argv))
        lab.stop_owned_named()
        assert bool(calls) is stops

    @pytest.mark.skipif(os.geteuid() != 0, reason='Requires root-owned policy/state')
    def test_waiting_renewal_reloads_policy_after_acquiring_lock(self, tmp_path, monkeypatch):
        config = tmp_path / 'named.conf'
        config.write_text(policy('old.test'))
        actual_lock = module.fcntl.flock
        def after_wait(descriptor, operation):
            actual_lock(descriptor, operation)
            # A deploy atomically replaced named.conf while this timer waited.
            config.write_text(policy('new.test'))
        monkeypatch.setattr(module.fcntl, 'flock', after_wait)
        helper = module.PrivateDnssec(config, tmp_path / 'state/owned.json',
                                      runner=lambda *args, **kwargs: SimpleNamespace(returncode=0))
        assert helper.reconcile()['renewed'] == ['new.test']
        assert helper.owned() == ['new.test']

    @pytest.mark.skipif(os.geteuid() != 0, reason='Requires root-owned policy/state')
    def test_ownership_directory_is_synced_before_rndc_mutation(self, tmp_path, monkeypatch):
        config = tmp_path / 'named.conf'
        config.write_text(policy('corp.test'))
        events = []
        actual_sync = module.os.fsync
        def sync(descriptor):
            events.append('directory' if stat.S_ISDIR(os.fstat(descriptor).st_mode) else 'file')
            actual_sync(descriptor)
        def runner(*args, **kwargs):
            events.append('rndc')
            return SimpleNamespace(returncode=0)
        monkeypatch.setattr(module.os, 'fsync', sync)
        helper = module.PrivateDnssec(config, tmp_path / 'state/owned.json', runner=runner)
        helper.reconcile()
        assert events == ['file', 'directory', 'rndc', 'file', 'directory']

    def test_startup_command_change_restarts_the_existing_process(self):
        handler = yaml.safe_load((ROLE / 'handlers/main.yml').read_text())[0]['ansible.builtin.systemd']
        assert handler['daemon_reload'] is True
        render = Environment(undefined=StrictUndefined).from_string(handler['state']).render
        assert render(bind_named_override={'changed': True}) == 'restarted'
        assert render(bind_named_runtime_override={'changed': True}) == 'restarted'
        assert render(bind_named_override={'changed': False}) == 'reloaded'
        assert render() == 'reloaded'
        install = yaml.safe_load((ROLE / 'tasks/tasks.d/dnssec_prepare.yml').read_text())
        override = next(task for branch in install for task in branch['block']
                        if task['name'] == 'Apply private exceptions before named start or reload completes')
        assert override['register'] == 'bind_named_override'

    def test_dns_restore_stops_named_and_pauses_renewal_before_extraction(self):
        main = yaml.safe_load((ROLE / 'tasks/tasks.d/deploy/main.yml').read_text())
        rescue = next(task['rescue'] for task in main if 'rescue' in task)
        restore = next(index for index, task in enumerate(rescue) if task['name'] == 'Restore the pre-deployment BIND backup')
        stop = next((index for index, task in enumerate(rescue)
                     if task.get('ansible.builtin.systemd') == {'name': 'named', 'state': 'stopped'}), None)
        assert stop is not None and stop < restore, 'Stop named before extracting its files and journals'
        pause = next((index for index, task in enumerate(rescue)
                      if task['name'] == 'Pause private DNSSEC renewal during restoration'), None)
        assert pause is not None and pause < stop
        assert any(task['name'] == 'Resume previously active private DNSSEC renewal' for task in rescue[restore + 1:])
