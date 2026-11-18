"""Regress lifecycle and durability failures identified by the global review."""
import importlib.util
import hashlib
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


def completed_route_fixture(folder):
    """Build independent synthetic evidence for the integration verifier contract."""
    directory = folder / '.front-routes.retired.12345678-1234-4234-8234-123456789abc.AbCd1234'
    directory.mkdir(mode=0o700)
    identity = ('23456789-1234-4234-8234-123456789abc', 'front0', '198.51.100.1',
                '198.51.100.2', '10.0.0.2', '192.0.2.2', '100')
    operations = ['P|203.0.113.0/24|198.51.100.1|100', 'R|203.0.113.0/24|198.51.100.1|100']
    data = {name: b'baseline\n' for name in ('profile', 'kernel', 'addresses', 'rules')}
    data.update(meta=('12345678-1234-4234-8234-123456789abc|' + '|'.join(identity) + '\n').encode(),
                operations=('\n'.join(operations) + '\n').encode(), clients=b'203.0.113.0/24\n',
                progress=b'2 done\n')
    for name, value in data.items():
        path = directory / name
        path.write_bytes(value)
        path.chmod(0o600)
    refresh_completed_checksums(directory)
    return directory, identity, operations, ['203.0.113.0/24']


def refresh_completed_checksums(directory):
    """Keep integrity valid when exercising a separate semantic refusal."""
    names = ('meta', 'operations', 'profile', 'kernel', 'addresses', 'rules', 'clients')
    path = directory / 'checksums'
    path.write_text(''.join(hashlib.sha256((directory / name).read_bytes()).hexdigest() +
                            '  ' + name + '\n' for name in names))
    path.chmod(0o600)


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

    @pytest.mark.skipif(os.name != 'posix' or os.geteuid() != 0, reason='Requires native root metadata')
    def test_completed_journal_verifier_admits_only_the_complete_immutable_evidence(self, tmp_path):
        from integration_deploy_auto_routes import CombinedDeployment
        directory, identity, operations, clients = completed_route_fixture(tmp_path)
        before = {str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_ino,
            p.stat().st_mtime_ns, p.stat().st_mode, p.stat().st_uid, p.stat().st_gid) for p in directory.iterdir()}
        assert len(before) == 9
        assert CombinedDeployment.completed_route_snapshot(directory, identity, operations, clients) == before

    @pytest.mark.skipif(os.name != 'posix' or os.geteuid() != 0, reason='Requires native root metadata')
    @pytest.mark.parametrize('damage', ['missing', 'extra', 'checksum', 'unfinished', 'meta',
        'operations', 'clients', 'directory-mode', 'file-mode', 'file-symlink', 'file-hardlink',
        'owner', 'directory-symlink'])
    def test_completed_journal_verifier_rejects_unsafe_or_unrelated_additions(self, tmp_path, damage):
        from integration_deploy_auto_routes import CombinedDeployment
        directory, identity, operations, clients = completed_route_fixture(tmp_path)
        operator = tmp_path / 'operator-evidence'
        operator.write_bytes(b'operator bytes\n')
        operator.chmod(0o600)
        if damage == 'missing':
            (directory / 'meta').unlink()
        elif damage == 'extra':
            (directory / 'operator-file').write_bytes(b'foreign bytes\n')
        elif damage == 'checksum':
            (directory / 'kernel').write_bytes(b'changed baseline\n')
        elif damage == 'unfinished':
            (directory / 'progress').write_bytes(b'1 intent\n')
        elif damage in ('meta', 'operations', 'clients'):
            (directory / damage).write_bytes(b'foreign semantic record\n')
            refresh_completed_checksums(directory)
        elif damage == 'directory-mode':
            directory.chmod(0o755)
        elif damage == 'file-mode':
            (directory / 'meta').chmod(0o644)
        elif damage in ('file-symlink', 'file-hardlink'):
            (directory / 'meta').unlink()
            if damage == 'file-symlink':
                (directory / 'meta').symlink_to(operator)
            else:
                os.link(str(operator), str(directory / 'meta'))
        elif damage == 'owner':
            os.chown(str(directory / 'meta'), 1000, 1000)
        else:
            actual = directory.with_name('operator-directory')
            directory.rename(actual)
            directory.symlink_to(actual, target_is_directory=True)
        metadata = operator.lstat()
        operator_before = operator.read_bytes(), (metadata.st_dev, metadata.st_ino, metadata.st_mode,
            metadata.st_uid, metadata.st_gid, metadata.st_nlink, metadata.st_mtime_ns, metadata.st_ctime_ns)
        with pytest.raises(AssertionError):
            CombinedDeployment.completed_route_snapshot(directory, identity, operations, clients)
        assert operator.read_bytes() == operator_before[0]
        metadata = operator.lstat()
        assert (metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_uid, metadata.st_gid,
                metadata.st_nlink, metadata.st_mtime_ns, metadata.st_ctime_ns) == operator_before[1]

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
