"""Keep ownership recoverable when private journal I/O or lock inputs fail."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from types import SimpleNamespace

import pytest

from test_private_dnssec import policy
from test_tsig_publication_integrity import FaultyWrite


SCRIPT = Path(__file__).resolve().parents[1] / 'roles/bind9/files/bind-private-dnssec.py'
pytestmark = pytest.mark.skipif(os.name != 'posix' or os.geteuid() != 0,
                                reason='Requires isolated root-owned DNSSEC state')


@pytest.fixture
def dnssec():
    spec = importlib.util.spec_from_file_location('dnssec_integrity', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('fault', ['short', 'altered'])
def test_journal_is_verified_before_any_rndc_mutation(tmp_path, monkeypatch, dnssec, fault):
    config, state = tmp_path / 'named.conf', tmp_path / 'owned.json'
    config.write_text(policy('new.example.test'), encoding='utf-8')
    state.write_text('{"version": 1, "domains": ["old.example.test"]}\n', encoding='utf-8')
    state.chmod(0o600)
    before = state.read_bytes()
    calls = []
    def runner(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0)
    helper = dnssec.PrivateDnssec(config, state, runner=runner)
    original = dnssec.tempfile.NamedTemporaryFile

    def damaged_private_file(*args, **kwargs):
        return FaultyWrite(original(*args, **kwargs), fault)

    monkeypatch.setattr(dnssec.tempfile, 'NamedTemporaryFile', damaged_private_file)
    with pytest.raises(OSError, match='DNSSEC.*(write|verification)'):
        helper.reconcile()
    assert calls == []
    assert state.read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ['dnssec.lock', 'named.conf', 'owned.json']


@pytest.mark.parametrize('content', [
    '{"version": 1, "version": 1, "domains": []}',
    '{"version": 1, "domains": ["old.test"], "domains": []}',
])
def test_duplicate_ownership_fields_fail_before_mutation(tmp_path, dnssec, content):
    state = tmp_path / 'owned.json'
    state.write_text(content, encoding='utf-8')
    with pytest.raises(ValueError, match='Duplicate DNSSEC'):
        dnssec.PrivateDnssec(state=state).owned()
    assert state.read_text(encoding='utf-8') == content


def test_cli_check_executes_the_real_entry_point_without_mutation(tmp_path, dnssec, monkeypatch, capsys):
    config, state = tmp_path / 'named.conf', tmp_path / 'absent/owned.json'
    config.write_text(policy(), encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', [str(SCRIPT), '--config', str(config), '--state', str(state), '--check'])
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(SCRIPT), run_name='__main__')
    assert result.value.code == 0
    assert json.loads(capsys.readouterr().out) == {'check': True, 'renew': ['corp.example.test'], 'remove': []}
    assert not state.parent.exists()


@pytest.mark.parametrize('port', ['0', '65536'])
def test_both_port_bounds_are_refused_by_the_real_cli(monkeypatch, dnssec, port):
    monkeypatch.setattr(sys, 'argv', [str(SCRIPT), '--port', port])
    with pytest.raises(SystemExit) as result:
        dnssec.main()
    assert result.value.code == 2


@pytest.mark.parametrize('stderr,stdout,expected', [
    ('native refused', 'secondary diagnostic', 'native refused'),
    ('', 'native stdout', 'native stdout'),
    ('', '', 'no RNDC diagnostic'),
])
def test_rndc_preserves_the_available_bounded_diagnostic(dnssec, stderr, stdout, expected):
    helper = dnssec.PrivateDnssec(runner=lambda *a, **kw: SimpleNamespace(returncode=1, stderr=stderr, stdout=stdout))
    with pytest.raises(RuntimeError, match=expected):
        helper.command(['-remove', 'old.test'])


@pytest.mark.parametrize('kind', ['fifo', 'owner'])
def test_lock_and_policy_must_be_root_owned_regular_files(tmp_path, dnssec, kind):
    config = tmp_path / 'named.conf'
    config.write_text('options {};\n', encoding='utf-8')
    lock = tmp_path / 'dnssec.lock'
    if kind == 'fifo':
        os.mkfifo(lock, 0o600)
        with pytest.raises(ValueError, match='lock permissions'):
            dnssec.PrivateDnssec(config, tmp_path / 'owned.json').reconcile()
        with pytest.raises(ValueError, match='policy/state permissions'):
            dnssec.PrivateDnssec.read(lock)
    else:
        lock.write_bytes(b'')
        lock.chmod(0o600)
        os.chown(lock, 1, -1)
        with pytest.raises(ValueError, match='lock permissions'):
            dnssec.PrivateDnssec(config, tmp_path / 'owned.json').reconcile()
    assert not (tmp_path / 'owned.json').exists()


def test_contended_lock_exits_within_the_configured_deadline(tmp_path, dnssec):
    config, state = tmp_path / 'named.conf', tmp_path / 'owned.json'
    config.write_text('options {};\n', encoding='utf-8')
    with (tmp_path / 'dnssec.lock').open('w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX)
        # The component must refuse before writing its journal or invoking
        # RNDC; retain the separate public CLI deadline assertion below.
        helper = dnssec.PrivateDnssec(config, state, lock_timeout=0,
            runner=lambda *args, **kwargs: pytest.fail('RNDC ran without the DNSSEC lock'))
        with pytest.raises(ValueError, match='DNSSEC lock timed out'):
            helper.reconcile()
        assert not state.exists()
        result = subprocess.run([sys.executable, str(SCRIPT), '--config', str(config),
                                 '--state', str(state), '--lock-timeout', '0.05'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True, timeout=2)
    assert result.returncode == 1
    assert 'lock timed out' in result.stderr
    assert not state.exists()


@pytest.mark.parametrize('timeout', [float('nan'), float('inf'), -float('inf'), -1, True, None, '30', 10 ** 400])
def test_invalid_lock_deadlines_fail_before_any_side_effect(tmp_path, dnssec, timeout):
    with pytest.raises(ValueError, match='finite nonnegative'):
        dnssec.PrivateDnssec(state=tmp_path / 'state/owned.json', lock_timeout=timeout)
    assert list(tmp_path.iterdir()) == []


def test_brief_contention_retries_then_keeps_normal_reconciliation(tmp_path, monkeypatch, dnssec):
    config = tmp_path / 'named.conf'
    config.write_text('options {};\n', encoding='utf-8')
    original = dnssec.fcntl.flock
    flags_seen = []

    def contend_once(descriptor, flags):
        flags_seen.append(flags)
        if len(flags_seen) == 1:
            raise BlockingIOError('Another reconciler is completing')
        return original(descriptor, flags)

    monkeypatch.setattr(dnssec.fcntl, 'flock', contend_once)
    helper = dnssec.PrivateDnssec(config, tmp_path / 'owned.json', lock_timeout=1)
    assert helper.reconcile() == {'renewed': [], 'removed': []}
    assert len(flags_seen) == 2
    assert all(flags & fcntl.LOCK_NB for flags in flags_seen)


def test_private_temporary_allocation_failure_keeps_the_old_journal(tmp_path, monkeypatch, dnssec):
    helper = dnssec.PrivateDnssec(state=tmp_path / 'owned.json')
    helper.save(['old.test'])
    before = helper.state.read_bytes()

    def exhausted(*args, **kwargs):
        raise OSError('No private staging space')

    monkeypatch.setattr(dnssec.tempfile, 'NamedTemporaryFile', exhausted)
    with pytest.raises(OSError, match='staging space'):
        helper.save(['new.test'])
    assert helper.state.read_bytes() == before
    assert list(tmp_path.iterdir()) == [helper.state]


@pytest.mark.parametrize('kind', ['directory-owner', 'directory-link-race', 'lock-hardlink'])
def test_state_boundary_rejects_untrusted_ownership_and_aliases(tmp_path, monkeypatch, dnssec, kind):
    config = tmp_path / 'named.conf'
    config.write_text('options {};\n', encoding='utf-8')
    state = tmp_path / 'state/owned.json'
    state.parent.mkdir(mode=0o700)
    if kind == 'directory-owner':
        os.chown(state.parent, 1, -1)
    elif kind == 'directory-link-race':
        original = Path.mkdir
        def redirected(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path == state.parent:
                path.rename(tmp_path / 'old-state')
                path.symlink_to(tmp_path / 'old-state', target_is_directory=True)
            return result
        monkeypatch.setattr(Path, 'mkdir', redirected)
    else:
        lock = state.parent / 'dnssec.lock'
        lock.write_bytes(b'')
        lock.chmod(0o600)
        os.link(lock, tmp_path / 'alias.lock')
    with pytest.raises(ValueError, match='Unsafe DNSSEC'):
        dnssec.PrivateDnssec(config, state).reconcile()
    assert not state.exists()
