"""Inject short/corrupt I/O before any configuration is allowed to go live."""
import os
import errno
from pathlib import Path
import sys
import subprocess

import pytest

from test_management import CONF, Lab, M, pytestmark as base_pytestmark

pytestmark = base_pytestmark


@pytest.fixture
def lab(tmp_path):
    return Lab(tmp_path)


class Fault:
    """One low-level write fault, shared by pathlib and descriptor-based I/O."""

    def __init__(self, phase, mode):
        self.phase, self.mode, self.triggered = phase, mode, False

    def selected(self, path):
        if self.phase == 'candidate':
            return '/bind9-candidate-' in path and path.endswith('/etc/named.conf')
        if self.phase == 'backup':
            return '/transaction-' in path and path.endswith('/etc/named.conf')
        if self.phase == 'journal':
            return '/.manifest-' in path
        return '/etc/.bind9-' in path

    def wrap(self, stream, path):
        return FaultStream(stream, self) if self.selected(str(path)) else stream


class FaultStream:
    """Expose actual filesystem effects, including a lying successful write count."""

    def __init__(self, stream, fault):
        self.stream, self.fault = stream, fault

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def write(self, data):
        if self.fault.triggered:
            return self.stream.write(data)
        self.fault.triggered = True
        if self.fault.mode == 'enospc':
            self.stream.write(data[:1])
            raise OSError(errno.ENOSPC, 'Injected disk full')
        if self.fault.mode == 'corrupt':
            value = (' ' if isinstance(data, str) else b' ') + data[1:]
        else:
            value = data[:max(1, len(data) // 2)]
        count = self.stream.write(value)
        return count if self.fault.mode == 'short' else len(data)


def instrument(monkeypatch, fault):
    """Fault injection follows the stream, independent of implementation helpers."""
    fdopen, path_open = M.os.fdopen, Path.open
    def open_fd(fd, *args, **kwargs):
        path = os.readlink('/proc/self/fd/' + str(fd))
        return fault.wrap(fdopen(fd, *args, **kwargs), path)
    def open_path(path, mode='r', *args, **kwargs):
        stream = path_open(path, mode, *args, **kwargs)
        return fault.wrap(stream, path) if 'w' in mode or 'x' in mode else stream
    monkeypatch.setattr(M.os, 'fdopen', open_fd)
    monkeypatch.setattr(Path, 'open', open_path)


@pytest.mark.parametrize('phase', ['candidate', 'backup', 'publication', 'journal'])
@pytest.mark.parametrize('mode', ['short', 'lying', 'corrupt', 'enospc'])
def test_incomplete_or_corrupt_write_is_refused_before_live_publication(lab, monkeypatch, phase, mode):
    fault = Fault(phase, mode)
    instrument(monkeypatch, fault)
    with pytest.raises((M.ManagementError, OSError)):
        lab.write(value='no')
    assert fault.triggered
    assert (lab.root / 'etc/named.conf').read_text() == CONF
    assert not any(call[-1] == 'reload' for call in lab.calls)


def test_short_read_cannot_become_a_valid_static_snapshot(lab, monkeypatch):
    fdopen = M.os.fdopen
    class ShortRead:
        def __init__(self, stream):
            self.stream = stream
        def __getattr__(self, name):
            return getattr(self.stream, name)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return self.stream.__exit__(*args)
        def read(self, count=-1):
            data = self.stream.read(count)
            return data[:len(data) // 2]
    def intervene(fd, *args, **kwargs):
        stream = fdopen(fd, *args, **kwargs)
        return ShortRead(stream) if os.readlink('/proc/self/fd/' + str(fd)).endswith('/etc/named.conf') else stream
    monkeypatch.setattr(M.os, 'fdopen', intervene)
    with pytest.raises(M.ManagementError):
        M.SafeFiles(lab.root).read('/etc/named.conf')


def test_native_command_output_is_refused_instead_of_silently_unbounded():
    with pytest.raises(M.ManagementError):
        M.Manager.command([sys.executable, '-c', 'import sys; sys.stdout.write("x" * (1024 * 1024 + 1))'])


def test_invalid_native_output_encoding_has_a_controlled_refusal():
    with pytest.raises(M.ManagementError):
        M.Manager.command([sys.executable, '-c', 'import os; os.write(1, b"\\xff")'])


@pytest.mark.parametrize('response', [
    ';; status: NOERROR\n;; flags: qr aa tc; QUERY: 1, ANSWER: 1\nlocalhost. 60 IN A 127.0.0.1\n',
    ';; status: NOERROR\n;; flags: qr aa; QUERY: 1, ANSWER: 2\nlocalhost. 60 IN A 127.0.0.1\n',
    ';; status: SERVFAIL\n;; status: NOERROR\nlocalhost. 60 IN A 127.0.0.1\n',
])
def test_truncated_or_contradictory_dns_output_never_proves_health(response):
    assert not M.DnsReply(response).matches('localhost.', 'A', '127.0.0.1')


def test_single_request_is_bounded_before_parsing_or_writing(lab):
    with pytest.raises(M.ManagementError):
        lab.manager(action='create', name='version', value='"' + 'x' * (256 * 1024) + '"')
    assert not lab.calls


@pytest.mark.parametrize('kind', ['open-pipe', 'closed-pipe', 'immediate'])
def test_command_deadline_terminates_its_process(lab, monkeypatch, kind):
    monkeypatch.setattr(M.NativeCommand, 'TIMEOUT', 0 if kind == 'immediate' else 0.2)
    source = 'import time; time.sleep(20)'
    if kind == 'closed-pipe':
        source = 'import os; os.close(1); os.close(2); ' + source
    with pytest.raises(M.ManagementError, match='timed out'):
        M.Manager.command([sys.executable, '-c', source])


def test_command_timeout_stops_descendants_too(tmp_path, monkeypatch):
    monkeypatch.setattr(M.NativeCommand, 'TIMEOUT', 0.4)
    marker = tmp_path / 'descendant.pid'
    source = ('import subprocess,sys,time; from pathlib import Path; '
              'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
              'Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(30)')
    with pytest.raises(M.ManagementError, match='timed out'):
        M.Manager.command([sys.executable, '-c', source, str(marker)])
    assert marker.is_file()
    process = Path('/proc') / marker.read_text() / 'stat'
    if process.exists():
        assert process.read_text().split()[2] == 'Z'  # Terminated, awaiting namespace reaper.


@pytest.mark.parametrize('payload', [float('nan'), object()])
def test_non_json_request_data_is_refused(lab, payload):
    with pytest.raises(M.ManagementError, match='ordinary JSON'):
        lab.manager(action='update', value=payload)
    assert not lab.calls


def test_caller_cannot_mutate_an_already_validated_request(lab):
    request = dict(resource='option', action='read', name='minimal-responses')
    manager = M.Manager(request, {}, root=lab.root, runner=lab.runner)
    request.update(action='delete', name='directory')
    assert manager.run()['value'] == 'yes'
    assert not lab.calls


def test_oversized_candidate_is_rejected_before_publication(lab, monkeypatch):
    monkeypatch.setattr(M.SafeFiles, 'LIMIT', len(CONF.encode()) + 10)
    with pytest.raises(M.ManagementError, match='size bound'):
        lab.write(action='create', name='version', value='"' + 'x' * 100 + '"')
    assert not lab.calls
    assert (lab.root / 'etc/named.conf').read_text() == CONF


@pytest.mark.parametrize('data', [b'\xff', b'options {};\x00'])
def test_corrupt_static_text_is_not_returned_as_a_snapshot(lab, data):
    (lab.root / 'etc/named.conf').write_bytes(data)
    with pytest.raises(M.ManagementError):
        M.SafeFiles(lab.root).read('/etc/named.conf')
    assert not lab.calls


def test_deeply_nested_syntax_has_a_controlled_refusal(monkeypatch):
    monkeypatch.setattr(M.Syntax, 'MAX_NESTING', 2)
    with pytest.raises(M.ManagementError, match='nesting'):
        M.Syntax('x { y { z { yes; }; }; };')


def test_zone_catalog_never_silently_truncates_a_record_limit(lab, monkeypatch):
    monkeypatch.setattr(M.ZoneDocument, 'MAX_RECORDS', 2)
    manager = M.Manager(dict(resource='record', action='list', zone='example.test'),
                        {}, root=lab.root, runner=lab.runner)
    with pytest.raises(M.ManagementError, match='record count'):
        manager.run()
    assert not lab.calls


def test_exact_command_byte_limit_and_utf8_roundtrip(monkeypatch):
    monkeypatch.setattr(M.NativeCommand, 'LIMIT', 1024)
    source = 'import os; os.write(1, ("é" * 512).encode("utf-8"))'
    assert M.Manager.command([sys.executable, '-c', source]) == 'é' * 512


def test_empty_successful_command_output_is_not_invented():
    assert M.Manager.command(['/usr/bin/true']) == ''


def test_archive_sync_failure_never_publishes_or_reloads(lab):
    def failure(argv):
        if Path(argv[0]).name == 'sync':
            raise M.ManagementError('Injected archive sync failure')
    lab.fail = failure
    with pytest.raises(M.ManagementError, match='sync'):
        lab.write(value='no')
    assert (lab.root / 'etc/named.conf').read_text() == CONF
    assert not any(call[-1] == 'reload' for call in lab.calls)


def test_dense_syntax_cannot_allocate_an_unbounded_token_array(monkeypatch):
    monkeypatch.setattr(M.Syntax, 'MAX_TOKENS', 4)
    with pytest.raises(M.ManagementError, match='token count'):
        M.Syntax('one yes; two no;')


def test_total_configuration_tokens_are_bounded_across_includes(lab, monkeypatch):
    root_tokens = M.Syntax(CONF).token_count
    lab.put('/etc/extra.conf', 'acl extra { 192.0.2.0/24; };\n')
    lab.put('/etc/named.conf', CONF + 'include "/etc/extra.conf";\n')
    monkeypatch.setattr(M.Configuration, 'MAX_TOKENS', root_tokens + 3)
    with pytest.raises(M.ManagementError, match='configuration token count'):
        lab.manager().run()
    assert not lab.calls


@pytest.mark.parametrize('mapping', [
    'resource: option\n  action: create\n  action: delete\n  name: version',
    'resource: batch\n  action: read\n  operations: []\n  operations: []',
])
def test_ambiguous_yaml_is_rejected_before_ansible_can_run_tasks(tmp_path, mapping):
    root = Path(__file__).resolve().parents[1]
    variables = tmp_path / 'request.yml'
    variables.write_text('bind_manage:\n  ' + mapping + '\n')
    playbook = tmp_path / 'read-only-syntax.yml'
    playbook.write_text('- hosts: all\n  gather_facts: false\n  tasks: []\n')
    env = dict(os.environ, ANSIBLE_CONFIG=str(root / 'ansible.cfg'))
    env.pop('ANSIBLE_DUPLICATE_YAML_DICT_KEY', None)
    result = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(playbook),
                             '-e', '@' + str(variables), '--syntax-check', '-vvv'], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            universal_newlines=True, timeout=20)
    assert result.returncode != 0, result.stdout
    # Legacy 2.9 misformats ConstructorError in the concise diagnostic; the
    # detailed synthetic-only trace must still prove the duplicate-key cause.
    assert 'duplicate' in result.stdout.lower()


def test_health_probe_disables_personal_digrc_before_other_options(lab):
    def verify(argv):
        if Path(argv[0]).name == 'dig':
            assert argv[1] == '-r'
            assert '+ignore' in argv
    lab.fail = verify
    assert lab.write(value='no')['status'] == 'committed'


@pytest.mark.parametrize('code,symbol', [(28, 'ENOSPC'), (5, 'EIO')])
def test_transport_reports_io_code_without_exposing_sensitive_os_details(tmp_path, code, symbol):
    from test_management_ansible import Runner
    runner = Runner(tmp_path, False)
    utility = runner.role / 'module_utils/bind9_management.py'
    utility.write_text(utility.read_text().replace(
        'self.config = Configuration(self.files)',
        "raise OSError(" + str(code) + ", 'sensitive-runtime-details')"))
    tasks = [{'block': [runner.role_task('tasks.d/manage/main.yml'),
                        {'fail': {'msg': 'Injected I/O failure was accepted'}}],
              'rescue': [{'assert': {'that': [
                  repr(symbol) + ' in ansible_failed_result.msg',
                  "'sensitive-runtime-details' not in ansible_failed_result.msg"]}}]}]
    output = runner.run(tasks, {'bind_manage': dict(resource='option', action='read', name='minimal-responses')})
    assert 'sensitive-runtime-details' not in output
    assert (runner.lab.root / 'etc/named.conf').read_text() == CONF
