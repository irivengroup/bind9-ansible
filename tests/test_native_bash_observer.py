"""Native compiler/socket/process integration in an explicit disposable root.

These are integration controls, not in-memory protocol tests or application
coverage artifacts. Each nested controller has its own collector lifecycle.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest

if os.name != 'posix':
    pytest.skip('The native Bash transport requires POSIX', allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/quality'))
from bash_channel import NativeBashChannel
from bash_coverage import NativeBashCollector, NativeBashToolchain
from bash_protocol import NativeBashProfile
from bind9_coverage import InstrumentationError

MANIFEST = os.environ.get('BIND9_NATIVE_BASH_MANIFEST')
pytestmark = pytest.mark.skipif(not MANIFEST or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1' or
    os.geteuid() != 0 or not Path('/.dockerenv').is_file() or
    {path.name for path in Path('/sys/class/net').iterdir()} != {'lo'},
    reason='Requires the explicit disconnected native Bash QA toolchain/root')


def toolchain_copy(folder):
    """Copy only owned build products; never alter a live interpreter."""
    source = Path(MANIFEST)
    folder.mkdir(mode=0o700)
    data = json.loads(source.read_text())
    for name in ('baseline', 'observed'):
        target = folder / data['binaries'][name]['path']
        shutil.copyfile(str(source.parent / data['binaries'][name]['path']), str(target))
        target.chmod(0o500)
    path = folder / 'manifest.json'
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    return path, data


@pytest.mark.parametrize('fault', ['duplicate-key', 'schema', 'archive', 'patch', 'recipe', 'options',
    'compiler', 'missing-reference', 'unsafe-path', 'version', 'binary-hash', 'writable', 'owner', 'hardlink', 'symlink'])
def test_native_bash_toolchain_refuses_incomplete_unreviewed_or_unsafe_build_products(tmp_path, fault):
    path, data = toolchain_copy(tmp_path / 'toolchain')
    binary = path.parent / 'observed-bash'
    if fault == 'schema': data['schema'] = True
    elif fault == 'archive': data['archive_sha256'] = '0' * 64
    elif fault == 'patch': data['patch_sha256'] = '0' * 64
    elif fault == 'recipe': data['recipe_sha256'] = '0' * 64
    elif fault == 'options': data['configure'].append('--unexpected')
    elif fault == 'compiler': data['tools']['gcc'] = ''
    elif fault == 'missing-reference': del data['binaries']['baseline']
    elif fault == 'unsafe-path': data['binaries']['observed']['path'] = '../observed-bash'
    elif fault == 'version': data['binaries']['observed']['version'] = '5.2.37(2)-release'
    elif fault == 'binary-hash': data['binaries']['observed']['sha256'] = '0' * 64
    elif fault == 'writable': binary.chmod(0o777)
    elif fault == 'owner': os.chown(str(binary), 65534, 65534)
    elif fault == 'hardlink': os.link(str(binary), str(path.parent / 'other-owner'))
    elif fault == 'symlink':
        binary.unlink()
        binary.symlink_to(Path(MANIFEST).parent / 'observed-bash')
    raw = json.dumps(data)
    path.write_text('{"schema":1,' + raw[1:] if fault == 'duplicate-key' else raw)
    with pytest.raises((InstrumentationError, OSError)):
        NativeBashToolchain(ROOT, path)


def test_native_bash_toolchain_rejects_a_replacement_after_initial_observation(tmp_path):
    path, _ = toolchain_copy(tmp_path / 'toolchain')
    chain = NativeBashToolchain(ROOT, path)
    binary = path.parent / 'baseline-bash'
    before = binary.read_bytes()
    binary.chmod(0o700)
    binary.write_bytes(before)
    binary.chmod(0o500)
    with pytest.raises(InstrumentationError, match='changed during'):
        chain.check_unchanged()


@pytest.mark.parametrize('variable', ['BASH_ENV', 'ENV', 'SHELLOPTS', 'BASHOPTS', 'LD_PRELOAD', 'LD_LIBRARY_PATH'])
def test_native_bash_profile_refuses_unreviewed_initialization_without_executing_it(variable):
    with pytest.raises(InstrumentationError, match='initialization'):
        NativeBashToolchain.environment({variable: 'synthetic-untrusted-initializer'})


def test_native_bash_profile_refuses_exported_shell_functions():
    with pytest.raises(InstrumentationError, match='initialization'):
        NativeBashToolchain.environment({'BASH_FUNC_injected%%': '() { false; }'})


class TestNativeBashInterruptedTransport:
    """A real handled signal must preserve facts without renewing wait budgets."""

    @staticmethod
    def switches(pid):
        for line in Path('/proc/{}/status'.format(pid)).read_text().splitlines():
            if line.startswith('voluntary_ctxt_switches:'):
                return int(line.split()[1])
        raise AssertionError('Missing kernel scheduling witness')

    @classmethod
    def wait_for_poll(cls, pid, previous=None):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            waiter = Path('/proc/{}/wchan'.format(pid)).read_text().strip()
            switches = cls.switches(pid)
            if 'poll' in waiter and (previous is None or switches > previous):
                return switches
            time.sleep(0.001)
        raise AssertionError('Native sender did not enter its bounded poll wait')

    def observe(self, tmp_path, monkeypatch, signals, exhaust=False):
        chain = NativeBashToolchain(ROOT, MANIFEST)
        source = tmp_path / 'interrupted-native-send.sh'
        source.write_text("printf 'ready\\n'\n" + 'if :; then :; fi\n' * 500, encoding='ascii')
        source.chmod(0o400)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        profile = predicate_profile(chain, source, digest)
        release = threading.Event()
        original = NativeBashChannel.read

        def gated_reader(channel):
            assert release.wait(timeout=12), 'Native transport diagnostic gate expired'
            return original(channel)

        # Only this fresh consumer is held. Its original credential/schema
        # checks resume unchanged once the real native poll has been witnessed.
        with monkeypatch.context() as scope:
            scope.setattr(NativeBashChannel, 'read', gated_reader)
            channel = NativeBashChannel(digest)
        environment = chain.environment(os.environ)
        environment.update(BIND9_NATIVE_BASH_FD=str(channel.descriptor),
            BIND9_NATIVE_BASH_SHA256=digest, BIND9_NATIVE_BASH_EVENT_MODE='sets')
        child = None
        errors = []
        try:
            child = subprocess.Popen([str(chain.binaries['observed']), str(source)],
                env=environment, pass_fds=(channel.descriptor,),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            assert select.select([child.stdout], [], [], 2)[0], 'Missing script readiness witness'
            ready = child.stdout.readline()
            assert ready == b'ready\n'
            self.wait_for_poll(child.pid)
            started = time.monotonic()
            if exhaust:
                # Interrupt after part of the original cumulative budget has
                # elapsed; restarting a fresh five seconds would exceed seven.
                time.sleep(3)
            for _ in range(signals):
                previous = self.switches(child.pid)
                os.kill(child.pid, signal.SIGCHLD)
                self.wait_for_poll(child.pid, previous)
            if exhaust:
                child.wait(timeout=max(0.01, 7 - (time.monotonic() - started)))
                assert time.monotonic() - started >= 4
        finally:
            release.set()
            try:
                if child is not None:
                    try:
                        stdout, stderr = child.communicate(timeout=10)
                    except BaseException:
                        child.kill()
                        child.wait(timeout=3)
                        raise
            finally:
                try:
                    channel.close()
                except InstrumentationError as error:
                    errors.append(error)
        assert child.returncode == 0 and not stdout and not stderr
        if not errors:
            try:
                profile.check_process(list(channel.rows), child.pid, child.returncode)
            except InstrumentationError as error:
                errors.append(error)
        assert not channel.thread.is_alive()
        return errors

    @pytest.mark.parametrize('signals', [0, 1, 3])
    def test_handled_signal_preserves_every_authenticated_native_fact(self, tmp_path, monkeypatch, signals):
        assert self.observe(tmp_path, monkeypatch, signals) == []

    def test_interruption_never_renews_the_cumulative_backpressure_budget(self, tmp_path, monkeypatch):
        errors = self.observe(tmp_path, monkeypatch, 1, exhaust=True)
        assert errors, 'An exhausted or incomplete native stream must remain refused'


@pytest.mark.parametrize('digest', [None, '', 'a' * 63, 'A' * 64])
def test_native_bash_channel_rejects_invalid_source_identity_before_allocating_transport(digest, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('Invalid identity must stop before opening a socket')
    monkeypatch.setattr(socket, 'socketpair', unexpected)
    with pytest.raises(InstrumentationError, match='exact source digest'):
        NativeBashChannel(digest)


def test_native_bash_transport_refuses_a_claimed_pid_that_differs_from_kernel_credentials():
    channel = NativeBashChannel('a' * 64)
    with socket.socket(fileno=os.dup(channel.descriptor)) as writer:
        writer.send(('B1\tH\t' + str(os.getpid() + 1) + '\t' + 'a' * 64 + '\n').encode('ascii'))
    with pytest.raises(InstrumentationError, match='kernel credentials'):
        channel.close()
    assert not channel.thread.is_alive() and channel.rows == []


@pytest.mark.parametrize('uid', [True, -1, 4294967295, '65534', 1.5])
def test_native_bash_channel_refuses_invalid_writer_identity_before_allocating_transport(uid, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('Invalid writer identity must not open a socket')
    monkeypatch.setattr(socket, 'socketpair', unexpected)
    with pytest.raises(InstrumentationError, match='authorized numeric UID'):
        NativeBashChannel('a' * 64, uid)


def test_native_bash_channel_requested_writer_still_refuses_the_wrong_real_sender():
    channel = NativeBashChannel('a' * 64, 65534)
    assert os.fstat(channel.descriptor).st_uid == 65534
    with socket.socket(fileno=os.dup(channel.descriptor)) as writer:
        writer.send(('B1\tH\t' + str(os.getpid()) + '\t' + 'a' * 64 + '\n').encode('ascii'))
    with pytest.raises(InstrumentationError, match='kernel credentials'):
        channel.close()
    assert not channel.thread.is_alive() and channel.rows == []


def test_native_bash_channel_accepts_only_the_requested_real_unprivileged_sender():
    channel = NativeBashChannel('a' * 64, 65534)
    sender = ('import os,socket,sys; '
              's=socket.socket(fileno=int(sys.argv[1])); '
              's.send(("B1\\tH\\t"+str(os.getpid())+"\\t"+"a"*64+"\\n").encode("ascii"))')
    result = subprocess.run([sys.executable, '-c', sender, str(channel.descriptor)],
        pass_fds=(channel.descriptor,), user=65534, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    assert result.returncode == 0 and not result.stdout and not result.stderr
    channel.close()
    assert len(channel.rows) == 1 and not channel.thread.is_alive()


def test_native_bash_writer_uid_preserves_explicit_numeric_and_named_users():
    import pwd
    assert NativeBashCollector.writer_uid(None) == os.geteuid()
    assert NativeBashCollector.writer_uid(65534) == 65534
    assert NativeBashCollector.writer_uid(pwd.getpwuid(os.geteuid()).pw_name) == os.geteuid()
    with pytest.raises(InstrumentationError, match='Unknown native Bash writer'):
        NativeBashCollector.writer_uid('bind9-nonexistent-fixture-user')


def test_an_unprivileged_controller_cannot_assign_a_different_writer_uid():
    source = '''import os, socket, sys
sys.path.insert(0, sys.argv[1])
from bash_channel import NativeBashChannel
from bash_coverage import NativeBashCollector
from bind9_coverage import InstrumentationError
assert os.geteuid() == 65534
def unexpected(*args, **kwargs):
    raise AssertionError('Unauthorized writer opened a transport')
socket.socketpair = unexpected
for invoke in (lambda: NativeBashChannel('a' * 64, 0), lambda: NativeBashCollector.writer_uid(0)):
    try:
        invoke()
    except InstrumentationError:
        continue
    raise AssertionError('Unprivileged controller reassigned a writer identity')
'''
    result = subprocess.run([sys.executable, '-c', source, str(ROOT / 'tools/quality')],
        user=65534, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    assert result.returncode == 0 and not result.stdout and not result.stderr


@pytest.mark.parametrize('uid', [True, -1, 4294967295, 1.5])
def test_native_bash_writer_uid_refuses_nonliteral_or_out_of_range_identity(uid):
    with pytest.raises(InstrumentationError, match='authorized numeric UID'):
        NativeBashCollector.writer_uid(uid)


@pytest.mark.parametrize('forged', [False, True], ids=['native-terminal', 'forged-sender'])
def test_native_terminal_queued_after_timeout_is_drained_with_unchanged_authentication(tmp_path, monkeypatch, forged):
    """Close must drain packets queued between an idle timeout and stop.

    Hold the timed-out receiver until the real Bash child has exited. The
    terminal datagram is then provably queued before close sets its stop flag.
    This ordering reproduces the scheduler race without timing-based retries.
    """
    chain = NativeBashToolchain(ROOT, MANIFEST)
    source = tmp_path / 'queued-terminal.sh'
    source.write_text('printf "complete\\n"\n', encoding='ascii')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    timed_out = threading.Event()
    original = NativeBashChannel.read

    class TimeoutBarrier:
        def __init__(self, reader, stopped):
            self.reader, self.stopped, self.delayed = reader, stopped, False

        def recvmsg(self, *arguments):
            try:
                return self.reader.recvmsg(*arguments)
            except socket.timeout:
                if not self.delayed:
                    self.delayed = True
                    timed_out.set()
                    assert self.stopped.wait(timeout=2), 'The deterministic close barrier expired'
                raise

        def close(self):
            self.reader.close()

    def delayed_timeout(channel):
        channel.reader = TimeoutBarrier(channel.reader, channel.stopped)
        return original(channel)

    with monkeypatch.context() as scope:
        scope.setattr(NativeBashChannel, 'read', delayed_timeout)
        channel = NativeBashChannel(digest)
    try:
        assert timed_out.wait(timeout=2), 'The native socket never reached its idle timeout'
        environment = chain.environment(os.environ)
        environment.update(BIND9_NATIVE_BASH_FD=str(channel.descriptor),
            BIND9_NATIVE_BASH_SHA256=digest, BIND9_NATIVE_BASH_EVENT_MODE='sets')
        with subprocess.Popen([str(chain.binaries['observed']), str(source)],
                env=environment, pass_fds=(channel.descriptor,), stdout=subprocess.PIPE,
                stderr=subprocess.PIPE) as process:
            stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0 and stdout == b'complete\n' and stderr == b''
        if forged:
            packet = ['B1', 'E', str(os.getpid()+1), digest, '0', '0']
            os.write(channel.descriptor, ('\t'.join(packet)+'\n').encode('ascii'))
            with pytest.raises(InstrumentationError, match='kernel credentials'):
                channel.close()
        else:
            channel.close()
        assert not channel.thread.is_alive()
        terminal = [tuple(row) for row in channel.rows if tuple(row[:2]) == ('B1', 'E')]
        assert terminal == [('B1', 'E', str(process.pid), digest, '0', '0')]
    finally:
        if not channel.closed:
            channel.close()


@pytest.mark.parametrize('event', ['T', 'W'])
@pytest.mark.parametrize('fault', ['pid', 'uid'])
def test_terminal_facts_keep_kernel_pid_and_uid_authentication(event, fault):
    channel = NativeBashChannel('a' * 64)
    sender = '''import os, socket, sys
event, fault, descriptor = sys.argv[1:]
if fault == 'uid':
    os.setuid(1000)
pid = os.getpid() + (1 if fault == 'pid' else 0)
row = ['B10', event, str(pid), 'a'*64, '4', '4', 'b'*64, '1', '1']
if event == 'W':
    row.append('0')
with socket.socket(fileno=int(descriptor)) as writer:
    writer.send(('\\t'.join(row)+'\\n').encode('ascii'))
'''
    result = subprocess.run([sys.executable, '-c', sender, event, fault, str(channel.descriptor)],
        pass_fds=(channel.descriptor,), capture_output=True, timeout=10)
    assert result.returncode == 0 and not result.stdout and not result.stderr
    with pytest.raises(InstrumentationError, match='kernel credentials'):
        channel.close()
    assert not channel.thread.is_alive() and channel.rows == []


def predicate_profile(chain, source, digest):
    """Model only this test's authentic syntax; export no production receipt."""
    channel = NativeBashChannel(digest)
    environment = dict(os.environ, BIND9_NATIVE_BASH_FD=str(channel.descriptor),
        BIND9_NATIVE_BASH_SHA256=digest, BIND9_NATIVE_BASH_EVENT_MODE='sets')
    try:
        with subprocess.Popen([str(chain.binaries['observed']), '-n', str(source)],
                env=environment, pass_fds=(channel.descriptor,), stdout=subprocess.PIPE,
                stderr=subprocess.PIPE) as process:
            stdout, stderr = process.communicate(timeout=10)
    finally:
        channel.close()
    assert (process.returncode, stdout, stderr) == (0, b'', b'')
    profile = object.__new__(NativeBashProfile)
    profile.COMPONENT, profile.source = str(source), source
    profile.SOURCE_SHA256, profile.EVENT_MODE = digest, 'sets'
    profile.declarations = {row[:2]+row[4:] for row in channel.rows if profile.is_declaration(row)}
    profile.build_units()
    assert all(not value for value in profile.check_process(
        list(channel.rows), process.pid, 0, syntax=True).values())
    return profile


@pytest.mark.parametrize('shape', ['direct', 'subshell'])
@pytest.mark.parametrize('populated', [False, True])
def test_native_terminal_predicate_uses_its_own_status_without_parent_inference(tmp_path, shape, populated):
    chain = NativeBashToolchain(ROOT, MANIFEST)
    command = '/usr/bin/test -s "$1"'
    if shape == 'subshell':
        command = '( '+command+' )'
    source = tmp_path / 'predicate.sh'
    source.write_text('if '+command+'; then printf yes; else printf no; fi\n', encoding='ascii')
    data = tmp_path / 'input'
    data.write_bytes(b'synthetic data\n' if populated else b'')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    profile = predicate_profile(chain, source, digest)
    baseline = subprocess.run([str(chain.binaries['baseline']), str(source), str(data)],
        capture_output=True, timeout=10)
    channel = NativeBashChannel(digest)
    environment = dict(os.environ, BIND9_NATIVE_BASH_FD=str(channel.descriptor),
        BIND9_NATIVE_BASH_SHA256=digest, BIND9_NATIVE_BASH_EVENT_MODE='sets')
    try:
        with subprocess.Popen([str(chain.binaries['observed']), str(source), str(data)],
                env=environment, pass_fds=(channel.descriptor,), stdout=subprocess.PIPE,
                stderr=subprocess.PIPE) as process:
            stdout, stderr = process.communicate(timeout=10)
    finally:
        channel.close()
    assert (process.returncode, stdout, stderr) == (baseline.returncode, baseline.stdout, baseline.stderr)
    assert stdout == (b'yes' if populated else b'no') and not stderr and process.returncode == 0
    hits = profile.check_process(list(channel.rows), process.pid, process.returncode)
    unit, = profile.statuses
    assert hits['conditions'] == {('status',)+unit+(populated,)}


def test_native_bash_channels_are_distinct_and_external_commands_cannot_inherit_the_writer(tmp_path):
    chain = NativeBashToolchain(ROOT, MANIFEST)
    helper = tmp_path / 'provider.py'
    helper.write_text('import os\ntry:\n os.fstat(int(os.environ["BIND9_NATIVE_BASH_FD"]))\n print("INHERITED")\n'
                      'except OSError:\n print("CLOSED")\n', encoding='ascii')
    source = tmp_path / 'native.sh'
    source.write_text('python3 "$1"\nprintf done\n', encoding='ascii')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    first, second = NativeBashChannel(digest), NativeBashChannel(digest)
    try:
        assert first.descriptor != second.descriptor and 64 <= first.descriptor <= 1023 and 64 <= second.descriptor <= 1023
        environment = dict(os.environ, BIND9_NATIVE_BASH_FD=str(first.descriptor),
            BIND9_NATIVE_BASH_SHA256=digest, BIND9_NATIVE_BASH_EVENT_MODE='sets')
        result = subprocess.run([str(chain.binaries['observed']), str(source), str(helper)],
            env=environment, pass_fds=(first.descriptor,), capture_output=True, timeout=15)
        assert result.returncode == 0 and result.stdout == b'CLOSED\ndone' and not result.stderr
    finally:
        first.close()
        second.close()
    assert first.rows and second.rows == [] and not first.thread.is_alive() and not second.thread.is_alive()


def test_native_function_mode_refuses_an_unattributed_dynamic_function_without_changing_its_result(tmp_path):
    chain = NativeBashToolchain(ROOT, MANIFEST)
    source = tmp_path / 'unreviewed-dynamic-function.sh'
    source.write_text("eval 'foreign() { printf executed; }'\nforeign\n", encoding='ascii')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    channel = NativeBashChannel(digest)
    environment = dict(os.environ, BIND9_NATIVE_BASH_FD=str(channel.descriptor),
        BIND9_NATIVE_BASH_SHA256=digest, BIND9_NATIVE_BASH_EVENT_MODE='function_sets')
    try:
        result = subprocess.run([str(chain.binaries['observed']), str(source)],
            env=environment, pass_fds=(channel.descriptor,), capture_output=True, timeout=10)
        assert result.returncode == 0 and result.stdout == b'executed' and result.stderr == b''
    finally:
        with pytest.raises(InstrumentationError, match='source-integrity failure'):
            channel.close()
    assert not any(row[:2] == ('B2', 'F') for row in channel.rows)


@pytest.mark.parametrize('text,lines', [
    ('outer() {\n  inner() { :; }\n  inner\n}\nouter\n', [1, 2]),
    ('shopt -s extdebug\nouter() {\n  inner() { :; }\n  inner\n}\nouter\ndeclare -F outer inner\n', [2, 3]),
    ('outer() {\n  middle() {\n    inner() { printf inner; }\n    inner\n  }\n  middle\n}\nouter\n', [1, 2, 3]),
    ('function outer {\n  function inner { printf inner; }\n  inner\n}\nouter\n', [1, 2]),
    ('a() { :; }\na\nunset -f a\na() {\n b() { :; }\n b\n}\na\nunset -f a b\na() { printf final; }\na\n', [1, 4, 5, 10]),
    (''.join('f() { :; }\nf\nunset -f f\n' for _ in range(1000)), list(range(1, 3000, 3))),
], ids=['nested', 'native-debug', 'three-levels', 'keyword', 'redefinition', 'word-cache'])
def test_native_function_positions_follow_authored_words_and_preserve_native_debug_fields(tmp_path, text, lines):
    """A nested parser must not assign the inner function's line to its parent.

    Native declaration metadata and runtime body entries are checked separately;
    native extdebug output must remain byte-identical to unmodified GNU Bash.
    Repeated definitions also exercise copied words and allocation-cache reuse.
    """
    chain = NativeBashToolchain(ROOT, MANIFEST)
    source = tmp_path / 'authored-functions.sh'
    source.write_text(text, encoding='ascii')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    baseline = subprocess.run([str(chain.binaries['baseline']), str(source)],
        capture_output=True, timeout=30)
    for syntax in (True, False):
        channel = NativeBashChannel(digest)
        environment = dict(os.environ, BIND9_NATIVE_BASH_FD=str(channel.descriptor),
            BIND9_NATIVE_BASH_SHA256=digest, BIND9_NATIVE_BASH_EVENT_MODE='function_sets')
        try:
            result = subprocess.run([str(chain.binaries['observed'])] + (['-n'] if syntax else []) + [str(source)],
                env=environment, pass_fds=(channel.descriptor,), capture_output=True, timeout=30)
        finally:
            channel.close()
        declarations = [int(row[4]) for row in channel.rows if row[:2] == ('B2', 'I')]
        entries = {int(row[4]) for row in channel.rows if row[:2] == ('B2', 'F')}
        assert declarations == lines
        assert entries == (set() if syntax else set(lines))
        if syntax:
            assert result.returncode == 0 and result.stdout == result.stderr == b''
        else:
            assert (result.returncode, result.stdout, result.stderr) == (
                baseline.returncode, baseline.stdout, baseline.stderr)


def test_native_bash_nested_controller_keeps_baseline_separate_and_counts_only_observed_entries(tmp_path):
    worker = '''import json,subprocess,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1]+'/tools/quality')
from bash_coverage import NativeBashCollector
root,manifest,output=sys.argv[1:]
script=root+'/roles/bind9/files/bind-healthcheck.sh'
for mode in ('baseline','observed'):
    collector=NativeBashCollector(root,manifest,mode)
    collector.install()
    try:
        result=subprocess.run(['/bin/bash',script],capture_output=True,text=True,timeout=10)
        assert result.returncode==2 and result.stdout==''
        assert result.stderr=='usage: bind-healthcheck LOCAL_BACK_IP [ZONE...]\\n'
        assert result.args==['/bin/bash',script]
    finally:
        collector.close()
    collector.save(Path(output)/(mode+'.json'))
baseline=json.loads((Path(output)/'baseline.json').read_text())
observed=json.loads((Path(output)/'observed.json').read_text())
assert baseline['errors']==[], ('baseline collector errors', baseline['errors'])
assert observed['errors']==[], ('observed collector errors', observed['errors'])
name='roles/bind9/files/bind-healthcheck.sh'
profile,hits=NativeBashCollector.checked_pair(root,manifest,baseline,observed)[name]
assert len(baseline['profiles'][name]['processes'])==len(observed['profiles'][name]['processes'])==1
assert 'rows' not in baseline['profiles'][name]['processes'][0]
assert {key:len(value)for key,value in hits.items()}==dict(statements=7,branches=1,functions=0,lines=4,conditions=1)
assert ('6','4','d3627ff5abc8be01e02652b54042b165ec2c1b0978b849b55ad7958f73c0a29c') in hits['statements']
assert 6 in hits['lines']
print('independent baseline and partial native facts verified')
'''
    result = subprocess.run([sys.executable, '-c', worker, str(ROOT), MANIFEST, str(tmp_path)],
        capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'independent baseline and partial native facts verified\n'
    baseline = json.loads((tmp_path / 'baseline.json').read_text())
    observed = json.loads((tmp_path / 'observed.json').read_text())
    for receipt in (baseline, observed):
        assert len(receipt['profiles']['roles/bind9/files/bind-healthcheck.sh']['processes']) == 1
    assert len(observed['profiles']['roles/bind9/files/bind-healthcheck.sh']['processes'][0]['rows']) > 0
    assert baseline['toolchain_sha256'] == observed['toolchain_sha256']


def test_native_bash_collector_admits_both_reviewed_shebangs_and_keeps_profiles_separate(tmp_path):
    worker = '''import json,subprocess,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1]+'/tools/quality')
from bash_coverage import NativeBashCollector
root,manifest,output=sys.argv[1:]
cases=[('bind-healthcheck.sh',[],2),('bind-front-routes.sh',['--help'],0),('bind-configtool.sh',[],2),
       ('bind9-sbin-path.sh',[],0)]
for mode in ('baseline','observed'):
    collector=NativeBashCollector(root,manifest,mode)
    collector.install()
    try:
        for name,args,expected in cases:
            command=['/bin/bash',root+'/roles/bind9/files/'+name]+args
            result=subprocess.run(command,capture_output=True,text=True,timeout=10)
            assert result.returncode==expected and result.args==command
    finally:
        collector.close()
    collector.save(Path(output)/(mode+'.json'))
    assert collector.errors==[],collector.errors
    assert {name:len(values)for name,values in collector.processes.items()}=={
        'roles/bind9/files/'+name:1 for name,_,_ in cases},collector.processes
baseline=json.loads((Path(output)/'baseline.json').read_text())
observed=json.loads((Path(output)/'observed.json').read_text())
profiles=NativeBashCollector.checked_pair(root,manifest,baseline,observed)
front,hits=profiles['roles/bind9/files/bind-front-routes.sh']
assert len(front.units['functions'])==40 and len(hits['functions'])==2
assert len(front.units['statements'])==1247 and len(front.units['lines'])==690
assert hits['statements'] and hits['statements']<front.units['statements']
assert hits['lines'] and hits['lines']<front.units['lines']
# EL7 empty-array guards remain real parameter choices in both inventories.
assert len(front.units['branches'])==947 and len(front.units['conditions'])==930
assert all(hits[key] and hits[key]<front.units[key]for key in ('branches','conditions'))
tool,hits=profiles['roles/bind9/files/bind-configtool.sh']
assert len(tool.units['functions'])==18 and len(hits['functions'])==1
assert len(tool.units['statements'])==907 and len(tool.units['lines'])==435
assert hits['statements'] and hits['statements']<tool.units['statements']
assert hits['lines'] and hits['lines']<tool.units['lines']
assert len(tool.units['branches'])==644 and len(tool.units['conditions'])==652
assert all(hits[key] and hits[key]<tool.units[key]for key in ('branches','conditions'))
path,hits=profiles['roles/bind9/files/bind9-sbin-path.sh']
assert {key:len(value)for key,value in path.units.items()}==dict(statements=3,branches=6,functions=0,lines=2,conditions=6)
print('four independent profiles verified')
'''
    result = subprocess.run([sys.executable, '-c', worker, str(ROOT), MANIFEST, str(tmp_path)],
        capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'four independent profiles verified\n'


def test_native_path_parameter_and_case_choices_are_measured_without_rewriting_the_script(tmp_path):
    """Real interpreter choices cover the exact asset; functional source tests
    separately check the installed file, its final PATH and repeat behavior.
    """
    from bash_protocol import NativeLoginPathProfile
    chain = NativeBashToolchain(ROOT, MANIFEST)
    profile = NativeLoginPathProfile(ROOT)
    totals = {name: set() for name in profile.METRICS}
    case_choices = []
    for value in ('', '/bin', '/usr/local/sbin:/bin', '/usr/local/sbinary'):
        environment = dict(os.environ, PATH=value)
        baseline = subprocess.run([str(chain.binaries['baseline']), '-x', str(profile.source)],
            env=environment, capture_output=True, timeout=10)
        channel = NativeBashChannel(profile.SOURCE_SHA256)
        environment.update(BIND9_NATIVE_BASH_FD=str(channel.descriptor),
            BIND9_NATIVE_BASH_SHA256=profile.SOURCE_SHA256, BIND9_NATIVE_BASH_EVENT_MODE='sets')
        try:
            observed = subprocess.run([str(chain.binaries['observed']), '-x', str(profile.source)],
                env=environment, pass_fds=(channel.descriptor,), capture_output=True, timeout=10)
        finally:
            channel.close()
        assert (observed.returncode, observed.stdout, observed.stderr) == (
            baseline.returncode, baseline.stdout, baseline.stderr)
        assert observed.returncode == 0 and observed.stdout == b''
        facts = profile.check_process(list(channel.rows), int(channel.rows[0][2]), observed.returncode)
        for name in profile.METRICS:
            totals[name].update(facts[name])
        case_choices.append({row[6] for row in channel.rows if row[:2] == ('B4', 'A')})
        # An already-present entry short-circuits the assignment's expansion.
        if value == '/usr/local/sbin:/bin':
            assert not any(row[0] == 'B0' and row[1] in ('C', 'R') and row[5] == '302' for row in channel.rows)
    assert totals == profile.units
    assert len(case_choices[0]) == len(case_choices[2]) == 1 and case_choices[0] != case_choices[2]


@pytest.mark.parametrize('prefix,pattern,constant', [
    ('', '*', True), ('', "'*'", False), ('', '"*"', False),
    ('', '\\*', False), ('pattern="*"; ', '$pattern', False),
])
def test_native_case_default_metadata_distinguishes_unconditional_quoted_and_expanded_stars(tmp_path, prefix, pattern, constant):
    """A quoted or expanded star must retain its native boolean denominator."""
    chain = NativeBashToolchain(ROOT, MANIFEST)
    source = tmp_path / 'native-case.sh'
    source.write_text(prefix + 'case "$1" in ' + pattern + ') printf match;; esac\n', encoding='ascii')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    outcomes = set()
    for argument in ('*', 'ordinary'):
        baseline = subprocess.run([str(chain.binaries['baseline']), str(source), argument],
            capture_output=True, timeout=10)
        channel = NativeBashChannel(digest)
        environment = dict(os.environ, BIND9_NATIVE_BASH_FD=str(channel.descriptor),
            BIND9_NATIVE_BASH_SHA256=digest, BIND9_NATIVE_BASH_EVENT_MODE='sets')
        try:
            observed = subprocess.run([str(chain.binaries['observed']), str(source), argument],
                env=environment, pass_fds=(channel.descriptor,), capture_output=True, timeout=10)
        finally:
            channel.close()
        assert (observed.returncode, observed.stdout, observed.stderr) == (
            baseline.returncode, baseline.stdout, baseline.stderr)
        metadata = [row for row in channel.rows if row[:2] == ('B6', 'P')]
        assert len(metadata) == 1 and metadata[0][-1] == str(int(constant))
        predicates = [row for row in channel.rows if row[:2] == ('B4', 'C')]
        assert len(predicates) == 1 and predicates[0][4:7] == metadata[0][4:7]
        outcomes.add(predicates[0][-1])
    assert outcomes == ({'1'} if constant or prefix else {'0', '1'})


@pytest.mark.parametrize('fault', ['wrong-mode', 'foreign-source', 'toolchain', 'missing-process', 'duplicate-pid',
    'bad-signature', 'result', 'unsupported', 'unexpected-field', 'missing-profile', 'extra-profile', 'old-schema'])
def test_native_bash_paired_receipt_forgeries_are_refused_after_actual_process_execution(tmp_path, fault):
    # Reuse the genuine integration artifact generator, never fabricate a
    # successful application run to make the verifier accept invented hits.
    test_native_bash_nested_controller_keeps_baseline_separate_and_counts_only_observed_entries(tmp_path)
    from bash_coverage import NativeBashCollector
    baseline = json.loads((tmp_path / 'baseline.json').read_text())
    observed = json.loads((tmp_path / 'observed.json').read_text())
    altered = copy.deepcopy(observed)
    profile = altered['profiles']['roles/bind9/files/bind-healthcheck.sh']
    if fault == 'wrong-mode': altered['mode'] = 'baseline'
    elif fault == 'foreign-source': profile['source_sha256'] = '0' * 64
    elif fault == 'toolchain': altered['toolchain_sha256'] = '0' * 64
    elif fault == 'missing-process': profile['processes'] = []
    elif fault == 'duplicate-pid': profile['processes'].append(copy.deepcopy(profile['processes'][0]))
    elif fault == 'bad-signature': profile['processes'][0]['signature'] = 'x' * 64
    elif fault == 'result': profile['processes'][0]['returncode'] = True
    elif fault == 'unsupported': altered['errors'] = ['native unsupported source']
    elif fault == 'missing-profile': del altered['profiles']['roles/bind9/files/bind-front-routes.sh']
    elif fault == 'extra-profile': altered['profiles']['unreviewed.sh'] = copy.deepcopy(profile)
    elif fault == 'old-schema': altered['schema'] = 1
    else: altered['invented'] = True
    with pytest.raises(InstrumentationError):
        NativeBashCollector.checked_pair(ROOT, MANIFEST, baseline, altered)


@pytest.mark.parametrize('execute_health', [False, True])
def test_native_bash_gate_commits_valid_languages_atomically_and_refuses_corrupt_bash_facts(tmp_path, execute_health):
    from coverage_gate import CoverageGate
    from run_coverage import PairedRun
    root = tmp_path / 'project'
    role = root / 'roles/bind9/files'
    role.mkdir(parents=True)
    (root / 'playbooks').mkdir()
    (root / 'tests').mkdir()
    shutil.copytree(str(ROOT / 'tools/quality'), str(root / 'tools/quality'),
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copyfile(str(ROOT / 'tests/conftest.py'), str(root / 'tests/conftest.py'))
    shutil.copyfile(str(ROOT / 'roles/bind9/files/bind-healthcheck.sh'), str(role / 'bind-healthcheck.sh'))
    for name in ('bind-front-routes.sh', 'bind-configtool.sh'):
        shutil.copyfile(str(ROOT / 'roles/bind9/files' / name), str(role / name))
    (role / 'decision.py').write_text('def select(value):\n    if value:\n        return 1\n    return 2\n', encoding='utf-8')
    smoke = '''import subprocess
from pathlib import Path
from roles.bind9.files.decision import select
def test_native_python_branches():
    assert select(True)==1 and select(False)==2
def test_native_health_argument_refusal():
    source=Path(__file__).resolve().parents[1]/'roles/bind9/files/bind-healthcheck.sh'
    if not EXECUTE_HEALTH:
        assert source.is_file()
        return
    result=subprocess.run(['/bin/bash',str(source)],capture_output=True,text=True,timeout=10)
    assert result.returncode==2 and result.stdout==''
    assert result.stderr=='usage: bind-healthcheck LOCAL_BACK_IP [ZONE...]\\n'
'''
    (root / 'tests/test_smoke.py').write_text(smoke.replace('EXECUTE_HEALTH', repr(execute_health)), encoding='utf-8')
    output = tmp_path / 'pair'
    assert PairedRun(root, output, ['tests/test_smoke.py']).run() == 2
    receipt = output / 'run.json'
    good = CoverageGate(root)
    good.add(receipt)
    assert len(good.runs) == 1 and not good.errors
    report = good.report()
    health = report['components']['roles/bind9/files/bind-healthcheck.sh']['metrics']
    assert (health['statements']['hit'], health['statements']['total']) == (7 if execute_health else 0, 72)
    assert (health['functions']['hit'], health['functions']['total']) == (0, 3)
    assert report['accepted'] is False
    for name, functions, statements, lines, branches, conditions in [('bind-front-routes.sh', 40, 1247, 690, 947, 930),
                                               ('bind-configtool.sh', 18, 907, 435, 644, 652)]:
        metrics = report['components']['roles/bind9/files/' + name]['metrics']
        assert metrics['functions']['hit'] == 0 and metrics['functions']['total'] == functions
        assert metrics['functions']['status'] == 'ÉCHOUÉ'
        assert (metrics['statements']['hit'], metrics['statements']['total']) == (0, statements)
        assert (metrics['lines']['hit'], metrics['lines']['total']) == (0, lines)
        assert all(metrics[key]['status'] == 'ÉCHOUÉ' for key in ('statements', 'lines'))
        assert (metrics['branches']['hit'], metrics['branches']['total']) == (0, branches)
        assert (metrics['conditions']['hit'], metrics['conditions']['total']) == (0, conditions)
        assert all(metrics[key]['status'] == 'ÉCHOUÉ' for key in ('branches', 'conditions'))
    # Every known denominator remains present even when its source never ran.
    assert report['project_totals']['functions']['total'] == 62
    assert report['project_totals']['functions']['hit'] == 1
    assert report['project_totals']['functions']['status'] == 'ÉCHOUÉ'
    for metric in ('statements', 'branches', 'lines', 'conditions'):
        assert report['project_totals'][metric]['status'] == 'ÉCHOUÉ'
        assert report['project_totals'][metric]['total'] == sum(
            component['metrics'][metric]['total'] for component in report['components'].values())
    assert all(value['status'] in ('EXÉCUTÉ', 'NON APPLICABLE')
        for value in report['components']['roles/bind9/files/decision.py']['metrics'].values())
    data = json.loads(receipt.read_text())
    proof = output / data['artifacts']['bash_observed']['path']
    original = json.loads(proof.read_text())
    altered = copy.deepcopy(original)
    profile = altered['profiles']['roles/bind9/files/bind-healthcheck.sh']
    # Rebind the corrupted artifact's checksum to test semantic admission.
    # Even a wholly unexecuted Bash component retains its complete inventory.
    if execute_health:
        rows = profile['processes'][0]['rows']
        row = next(value for value in rows if value[:2] == ['B2', 'S'])
        row[6] = '0' * 64
    else:
        del profile['static']['rows'][2]
    bad = output / 'bash-corrupt.json'
    bad.write_text(json.dumps(altered))
    data['artifacts']['bash_observed'] = {'path': bad.name, 'sha256': hashlib.sha256(bad.read_bytes()).hexdigest()}
    corrupted = output / 'run-corrupt.json'
    corrupted.write_text(json.dumps(data))
    refused = CoverageGate(root)
    with pytest.raises(ValueError, match='Unknown|misclassified|complete units'):
        refused.add(corrupted)
    assert refused.runs == [] and refused.native_units == {} and refused.bash_units == {}
    assert all(not values for metrics in refused.hits.values() for values in metrics.values())
