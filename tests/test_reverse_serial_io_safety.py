"""Exercise reverse-zone integrity across unsafe inputs and failed writes."""
import os
import struct
from pathlib import Path

import pytest

from test_reverse_serial import module, TestReverseSerial as ReverseTemplate


class WriteFault:
    """Inject the same real stream failure before/after atomic publication."""

    def __init__(self, stream, state):
        self.stream, self.state = stream, state

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def write(self, data):
        self.state['triggered'] = True
        self.stream.write(data[:max(1, len(data) // 2)])
        raise OSError(28, 'Injected disk full')


def test_disk_full_keeps_the_complete_previous_live_zone(tmp_path, monkeypatch):
    """A successful backup elsewhere does not make a truncated live file atomic."""
    path = tmp_path / 'zone'
    previous = ReverseTemplate().render()
    desired = ReverseTemplate().render(value='changed.example.test.')
    path.write_text(desired)
    state = {'triggered': False}
    path_open, fdopen = Path.open, os.fdopen

    def open_path(target, mode='r', *args, **kwargs):
        stream = path_open(target, mode, *args, **kwargs)
        return WriteFault(stream, state) if 'w' in mode else stream

    def open_fd(descriptor, mode='r', *args, **kwargs):
        stream = fdopen(descriptor, mode, *args, **kwargs)
        return WriteFault(stream, state) if 'w' in mode else stream

    monkeypatch.setattr(Path, 'open', open_path)
    monkeypatch.setattr(os, 'fdopen', open_fd)
    with pytest.raises(OSError, match='disk full'):
        module.reconcile(path, previous)
    assert state['triggered']
    assert path.read_text() == desired


@pytest.mark.parametrize('link', ['symbolic', 'hard'])
def test_linked_zone_is_refused_without_changing_its_other_name(tmp_path, link):
    original, alias = tmp_path / 'original', tmp_path / 'alias'
    value = ReverseTemplate().render(value='changed.example.test.')
    original.write_text(value)
    if link == 'symbolic':
        alias.symlink_to(original)
    else:
        os.link(str(original), str(alias))
    with pytest.raises((ValueError, OSError)):
        module.reconcile(alias, ReverseTemplate().render())
    assert original.read_text() == value


def test_commented_fake_soa_does_not_hide_the_real_serial(tmp_path):
    path = tmp_path / 'zone'
    desired = ReverseTemplate().render(value='changed.example.test.')
    previous = ReverseTemplate().render()
    decoy = '; fake serial ( 777 3600 900 1209600 300 )\n'
    # Keep both actual ownership markers intact; only add an ordinary comment.
    desired = desired.replace('$TTL', decoy + '$TTL')
    previous = previous.replace('$TTL', decoy + '$TTL')
    path.write_text(desired)
    assert module.reconcile(path, previous)
    assert decoy in path.read_text()
    assert '( 11 3600' in path.read_text()


def test_serial_publication_preserves_extended_metadata_and_posix_permissions(tmp_path):
    path = tmp_path / 'zone'
    path.write_text(ReverseTemplate().render(value='changed.example.test.'))
    path.chmod(0o640)
    os.setxattr(str(path), 'user.bind9-test', b'owned-zone-metadata')
    before = path.stat()
    assert module.reconcile(path, ReverseTemplate().render())
    after = path.stat()
    assert (after.st_mode, after.st_uid, after.st_gid) == (before.st_mode, before.st_uid, before.st_gid)
    assert os.getxattr(str(path), 'user.bind9-test') == b'owned-zone-metadata'


@pytest.mark.skipif(os.geteuid() != 0, reason='Security-attribute preservation requires Linux root with CAP_SETFCAP')
def test_metadata_is_applied_after_writes_that_can_clear_security_attributes(tmp_path):
    path = tmp_path / 'zone'
    path.write_text(ReverseTemplate().render(value='changed.example.test.'))
    path.chmod(0o640)  # This data file cannot be executed.
    capability = struct.pack('<IIIII', 0x02000001, 1 << 13, 0, 0, 0)
    os.setxattr(str(path), 'security.capability', capability)
    original = os.getxattr(str(path), 'security.capability')
    assert module.reconcile(path, ReverseTemplate().render())
    assert os.getxattr(str(path), 'security.capability') == original
