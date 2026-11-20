"""Check backup policy and the documented isolated workflow entry point."""
import importlib.util
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'roles/bind9/files/bind-configtool.sh'


def test_backup_is_always_unique_and_not_hourly_reused():
    text = SCRIPT.read_text()
    assert 'BACKUP_REUSED' not in text
    assert 'named-config-$stamp-' in text
    assert 'od -An -N4' in text


def test_retention_is_thirty_days_not_five_archives():
    text = SCRIPT.read_text()
    assert "date -u -d '30 days ago' +%s" in text
    assert 'epoch < cutoff' in text
    assert '"$item.sha256"' in text
    assert '"${sorted[@]:5}"' not in text


@pytest.fixture
def backup_launcher():
    """Import the actual public launcher; obsolete imports must fail this test."""
    path = Path(__file__).with_name('integration_backup_container.py')
    spec = importlib.util.spec_from_file_location('backup_container_launcher', str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('status', [0, 2, 5])
@pytest.mark.parametrize('previous', [None, 'operator-value'])
def test_backup_launcher_preserves_collection_failures_and_caller_scope(backup_launcher, monkeypatch, status, previous):
    """A launcher must propagate collection errors and never retain its opt-in."""
    module = backup_launcher
    guards, calls = [], []
    monkeypatch.setattr(module, '_require_disconnected_root', lambda flag, message: guards.append(flag))
    if previous is None:
        monkeypatch.delenv('BIND9_NATIVE_STATE_LAB', raising=False)
    else:
        monkeypatch.setenv('BIND9_NATIVE_STATE_LAB', previous)

    def run(arguments):
        assert os.environ['BIND9_NATIVE_STATE_LAB'] == '1'
        calls.append(arguments)
        return status

    monkeypatch.setattr(module.pytest, 'main', run)
    assert module.BackupContainerIntegration().run() == status
    assert guards == ['BIND_MANAGEMENT_ISOLATED']
    directory = Path(module.__file__).resolve().parent
    assert calls == [['-p', 'no:cacheprovider', '-q', str(directory / 'test_native_archive_cli.py'),
                      str(directory / 'test_configtool_workflows.py')]]
    assert os.environ.get('BIND9_NATIVE_STATE_LAB') == previous


@pytest.mark.parametrize('previous', [None, 'operator-value'])
def test_backup_launcher_restores_scope_when_the_runner_raises(backup_launcher, monkeypatch, previous):
    module = backup_launcher
    monkeypatch.setattr(module, '_require_disconnected_root', lambda flag, message: None)
    if previous is None:
        monkeypatch.delenv('BIND9_NATIVE_STATE_LAB', raising=False)
    else:
        monkeypatch.setenv('BIND9_NATIVE_STATE_LAB', previous)

    def unavailable(arguments):
        assert os.environ['BIND9_NATIVE_STATE_LAB'] == '1'
        raise RuntimeError('runner unavailable')

    monkeypatch.setattr(module.pytest, 'main', unavailable)
    with pytest.raises(RuntimeError, match='runner unavailable'):
        module.BackupContainerIntegration().run()
    assert os.environ.get('BIND9_NATIVE_STATE_LAB') == previous


def test_backup_launcher_refuses_missing_authorization_before_collecting(backup_launcher, monkeypatch):
    """The real guard must reject an ordinary invocation before fixture work."""
    module = backup_launcher
    monkeypatch.delenv('BIND_MANAGEMENT_ISOLATED', raising=False)
    monkeypatch.delenv('BIND9_NATIVE_STATE_LAB', raising=False)
    calls = []
    monkeypatch.setattr(module.pytest, 'main', lambda arguments: calls.append(arguments))
    with pytest.raises(RuntimeError, match='explicitly authorized disconnected disposable root'):
        module.BackupContainerIntegration().run()
    assert not calls and 'BIND9_NATIVE_STATE_LAB' not in os.environ
