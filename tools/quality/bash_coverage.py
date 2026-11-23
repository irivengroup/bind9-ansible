"""Observe approved Bash source through paired native GNU interpreters.

No production word, expression, command, trap or source byte is rewritten.
Each source profile names its independently measured metrics. Unsupported
metrics remain explicitly unknown, even when another metric is complete.
"""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import threading

from bash_channel import NativeBashChannel
from bash_protocol import native_profiles
from bind9_coverage import InstrumentationError
from native_bash.build import NativeBashBuild

ORIGINAL_POPEN = subprocess.Popen


def unique_object(pairs):
    """Reject duplicate JSON metadata keys before they can replace evidence."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise InstrumentationError('Duplicate native Bash receipt field')
        result[key] = value
    return result


class NativeBashToolchain:
    """Bind executable bytes, the build recipe, native parser and source profile."""

    def __init__(self, root, manifest):
        self.root = Path(root).resolve()
        self.manifest = Path(manifest).absolute()
        self.parent = self.manifest.parent
        if self.parent.is_symlink() or self.parent.stat().st_dev != Path('/').stat().st_dev:
            raise InstrumentationError('Native Bash executables require a private native filesystem')
        raw, self.manifest_stamp = self.read_private(self.manifest, 131072)
        self.digest = hashlib.sha256(raw).hexdigest()
        self.data = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object)
        expected = {'schema', 'archive_sha256', 'patch_sha256', 'recipe_sha256', 'configure', 'tools', 'binaries'}
        if (not isinstance(self.data, dict) or set(self.data) != expected or
                type(self.data['schema']) is not int or self.data['schema'] != 1 or
                self.data['archive_sha256'] != NativeBashBuild.ARCHIVE_SHA256 or
                self.data['patch_sha256'] != NativeBashBuild.PATCH_SHA256 or
                self.data['configure'] != list(NativeBashBuild.CONFIGURE)):
            raise InstrumentationError('Native Bash manifest differs from the inspected build contract')
        recipe = self.root / 'tools/quality/native_bash/build.py'
        patch = self.root / 'tools/quality/native_bash/gnu-bash-5.2.37-observer.patch'
        if (self.data['recipe_sha256'] != hashlib.sha256(recipe.read_bytes()).hexdigest() or
                self.data['patch_sha256'] != hashlib.sha256(patch.read_bytes()).hexdigest()):
            raise InstrumentationError('Native Bash build sources differ from the current QA revision')
        tools = self.data['tools']
        if (not isinstance(tools, dict) or set(tools) != {'gcc', 'make', 'patch'} or
                any(not isinstance(value, str) or not value or len(value) > 4096 or
                    any(character in value for character in '\r\n\x00') for value in tools.values())):
            raise InstrumentationError('Missing native Bash compiler provenance')
        if not isinstance(self.data['binaries'], dict) or set(self.data['binaries']) != {'baseline', 'observed'}:
            raise InstrumentationError('Both native Bash interpreters are required')
        self.binaries, self.stamps = {}, {}
        for mode, information in self.data['binaries'].items():
            if (not isinstance(information, dict) or set(information) != {'path', 'sha256', 'version'} or
                    information['path'] != mode + '-bash' or information['version'] != NativeBashBuild.VERSION):
                raise InstrumentationError('Invalid native Bash binary identity')
            binary = self.parent / information['path']
            content, stamp = self.read_private(binary, 16777216, executable=True)
            if hashlib.sha256(content).hexdigest() != information['sha256']:
                raise InstrumentationError('Native Bash executable checksum differs from its build')
            self.binaries[mode], self.stamps[mode] = binary, stamp
            environment = self.environment(os.environ)
            with ORIGINAL_POPEN([str(binary), '-c', 'printf "%s" "$BASH_VERSION"'],
                    env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
                stdout, stderr = process.communicate(timeout=10)
            if process.returncode != 0 or stdout != NativeBashBuild.VERSION.encode('ascii') or stderr:
                raise InstrumentationError('Native Bash interpreter version differs from the paired contract')
        if self.data['binaries']['baseline']['sha256'] == self.data['binaries']['observed']['sha256']:
            raise InstrumentationError('The native Bash reference must be independently uninstrumented')

    @staticmethod
    def stamp(info):
        return (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    @classmethod
    def read_private(cls, path, limit, executable=False):
        descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or
                    info.st_mode & 0o7022 or not 0 < info.st_size <= limit or
                    (executable and not info.st_mode & stat.S_IXUSR)):
                raise InstrumentationError('Unsafe native Bash toolchain file metadata')
            chunks, total = [], 0
            while True:
                value = os.read(descriptor, min(65536, limit + 1 - total))
                if not value:
                    break
                chunks.append(value)
                total += len(value)
                if total > limit:
                    raise InstrumentationError('Excessive native Bash toolchain input')
            stamp = cls.stamp(info)
            if stamp != cls.stamp(os.fstat(descriptor)) or stamp != cls.stamp(path.lstat()) or total != info.st_size:
                raise InstrumentationError('Native Bash toolchain file changed while reading')
            return b''.join(chunks), stamp
        finally:
            os.close(descriptor)

    @staticmethod
    def environment(values):
        result = dict(values)
        if (any(result.get(key) for key in ('BASH_ENV', 'ENV', 'SHELLOPTS', 'BASHOPTS', 'LD_PRELOAD', 'LD_LIBRARY_PATH')) or
                any(key.startswith('BASH_FUNC_') for key in result)):
            raise InstrumentationError('Native Bash profile refuses unreviewed shell or loader initialization')
        return result

    def check_unchanged(self):
        if self.manifest_stamp != self.stamp(self.manifest.lstat()) or any(
                self.stamps[mode] != self.stamp(path.lstat()) for mode, path in self.binaries.items()):
            raise InstrumentationError('Native Bash toolchain changed during test execution')

    def static_profile(self, profile):
        """Demand the complete syntax inventory, with no executable hit credit."""
        self.check_unchanged()
        channel = NativeBashChannel(profile.SOURCE_SHA256)
        environment = self.environment(os.environ)
        environment.update(BIND9_NATIVE_BASH_FD=str(channel.descriptor),
            BIND9_NATIVE_BASH_SHA256=profile.SOURCE_SHA256, BIND9_NATIVE_BASH_EVENT_MODE=profile.EVENT_MODE)
        try:
            with ORIGINAL_POPEN([str(self.binaries['observed']), '-n', str(profile.source)],
                    env=environment, pass_fds=(channel.descriptor,), stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
                stdout, stderr = process.communicate(timeout=30)
        finally:
            channel.close()
        if process.returncode != 0 or stdout or stderr:
            raise InstrumentationError('The native Bash syntax inventory did not complete cleanly')
        profile.check_process(list(channel.rows), process.pid, process.returncode, syntax=True)
        self.check_unchanged()
        return {'pid': process.pid, 'returncode': process.returncode, 'rows': channel.rows}


class NativeBashCollector:
    """Capture only approved script bytes while preserving ordinary Popen calls."""

    MAX_ACTIVE = 8
    MAX_PROCESSES = 4096
    MAX_RECEIPT_BYTES = 16777216
    MAX_TOTAL_ROWS = 100000
    owner = None

    def __init__(self, root, manifest, mode):
        if mode not in ('baseline', 'observed'):
            raise InstrumentationError('Unknown native Bash observation mode')
        self.root, self.mode = Path(root).resolve(), mode
        self.toolchain = NativeBashToolchain(self.root, manifest)
        self.profiles = native_profiles(self.root)
        self.digests = {profile.SOURCE_SHA256: profile for profile in self.profiles.values()}
        self.static = {name: self.toolchain.static_profile(profile) for name, profile in self.profiles.items()}
        self.lock = threading.Lock()
        self.active, self.processes, self.errors = set(), {name: [] for name in self.profiles}, []
        self.attempts = 0
        self.total_rows = 0
        self.receipt_bytes = len(json.dumps(self.static, separators=(',', ':')).encode('ascii')) + 65536
        self.original, self.adapter = None, None

    def invocation(self, arguments, options):
        """Select direct Bash/file invocations, including unchanged native copies."""
        if not isinstance(arguments, (list, tuple)) or not arguments or options.get('shell'):
            return None
        if not all(isinstance(value, (str, os.PathLike)) for value in arguments):
            return None
        arguments = [os.fspath(value) for value in arguments]
        direct = len(arguments) >= 2 and Path(arguments[0]).name == 'bash'
        index = 1 if direct else 0
        if arguments[index].startswith('-'):
            return None
        path = Path(arguments[index])
        path = path if path.is_absolute() else Path(options.get('cwd') or os.getcwd()) / path
        try:
            info = path.lstat()
        except (FileNotFoundError, NotADirectoryError):
            return None
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4194304:
            return None
        with path.open('rb') as stream:
            # Avoid reading native ELF executables for ordinary subprocesses.
            # Reviewed scripts use either native Bash shebang. Exact source
            # hashes below still determine eligibility, including copied files.
            prefix = stream.read(20)
            if not prefix.startswith((b'#!/usr/bin/env bash\n', b'#!/bin/bash\n')):
                return None
            raw = prefix + stream.read(4194305 - len(prefix))
        profile = self.digests.get(hashlib.sha256(raw).hexdigest())
        if profile is None:
            return None
        if options.get('executable') not in (None, arguments[0]):
            return None
        if options.get('close_fds') is False:
            raise InstrumentationError('The native Bash profile requires closed unrelated descriptors')
        if options.get('preexec_fn') is not None:
            raise InstrumentationError('Native Bash observation refuses child code before interpreter execution')
        self.toolchain.check_unchanged()
        environment = self.toolchain.environment(options.get('env') if options.get('env') is not None else os.environ)
        if any(key in environment for key in ('BIND9_NATIVE_BASH_FD', 'BIND9_NATIVE_BASH_SHA256', 'BIND9_NATIVE_BASH_EVENT_MODE')):
            raise InstrumentationError('Native Bash observation refuses a preexisting writer configuration')
        writer_uid = self.writer_uid(options.get('user'))
        signature = hashlib.sha256(json.dumps({'arguments': arguments[index + 1:], 'uid': writer_uid}, ensure_ascii=True,
            separators=(',', ':')).encode('ascii')).hexdigest()
        return arguments, index, environment, signature, profile, writer_uid

    @staticmethod
    def writer_uid(requested):
        """Resolve Popen's declarative user without executing a child hook."""
        if requested is None:
            return os.geteuid()
        if isinstance(requested, str):
            import pwd
            try:
                requested = pwd.getpwnam(requested).pw_uid
            except KeyError as error:
                raise InstrumentationError('Unknown native Bash writer user') from error
        if (type(requested) is not int or not 0 <= requested < 4294967295 or
                (requested != os.geteuid() and os.geteuid() != 0)):
            raise InstrumentationError('Native Bash writer requires an authorized numeric UID')
        return requested

    def install(self):
        if self.original is not None or NativeBashCollector.owner is not None:
            raise InstrumentationError('Native Bash observer is already installed')
        self.original = subprocess.Popen
        collector, original = self, self.original
        class ObservedPopen(original):
            """Keep the public Popen interface and original requested arguments."""
            def __init__(self, arguments, *positional, **options):
                self.native_bash = None
                selected = collector.invocation(arguments, options)
                requested = arguments
                if selected is not None:
                    args, index, environment, signature, profile, writer_uid = selected
                    with collector.lock:
                        if len(collector.active) >= collector.MAX_ACTIVE or collector.attempts >= collector.MAX_PROCESSES:
                            raise InstrumentationError('Native Bash process admission limit exceeded')
                        collector.attempts += 1
                        collector.active.add(self)
                    channel = None
                    try:
                        if collector.mode == 'observed':
                            channel = NativeBashChannel(profile.SOURCE_SHA256, writer_uid)
                            environment.update(BIND9_NATIVE_BASH_FD=str(channel.descriptor),
                                BIND9_NATIVE_BASH_SHA256=profile.SOURCE_SHA256, BIND9_NATIVE_BASH_EVENT_MODE=profile.EVENT_MODE)
                            options['pass_fds'] = tuple(sorted(set(options.get('pass_fds', ())) | {channel.descriptor}))
                        options['env'] = environment
                        options['executable'] = str(collector.toolchain.binaries[collector.mode])
                        arguments = args if index == 1 else ['bash'] + args
                        self.native_bash = {'channel': channel, 'signature': signature, 'finished': False, 'profile': profile}
                    except BaseException:
                        with collector.lock:
                            collector.active.remove(self)
                            collector.error('Native Bash observation setup failed')
                        if channel is not None:
                            channel.close()
                        raise
                try:
                    super().__init__(arguments, *positional, **options)
                    self.args = requested
                except BaseException:
                    self.finish_native_bash(startup_failure=True)
                    raise

            def finish_native_bash(self, startup_failure=False):
                observation = self.native_bash
                if observation is None or observation['finished'] or (not startup_failure and self.returncode is None):
                    return
                observation['finished'] = True
                try:
                    channel = observation['channel']
                    if channel is not None:
                        channel.close()
                    if startup_failure:
                        raise InstrumentationError('Native Bash process failed before its native receipt')
                    if channel is not None:
                        observation['profile'].check_process(list(channel.rows), self.pid, self.returncode)
                    collector.toolchain.check_unchanged()
                    value = {'pid': self.pid, 'returncode': self.returncode, 'signature': observation['signature']}
                    if channel is not None:
                        value['rows'] = channel.rows
                    size = len(json.dumps(value, separators=(',', ':')).encode('ascii'))
                    with collector.lock:
                        if collector.receipt_bytes + size > collector.MAX_RECEIPT_BYTES:
                            raise InstrumentationError('Native Bash receipt byte limit exceeded')
                        count = len(channel.rows) if channel is not None else 0
                        if collector.total_rows + count > collector.MAX_TOTAL_ROWS:
                            raise InstrumentationError('Native Bash session event limit exceeded')
                        collector.receipt_bytes += size
                        collector.total_rows += count
                        collector.processes[observation['profile'].COMPONENT].append(value)
                except (InstrumentationError, OSError) as error:
                    with collector.lock:
                        collector.error(str(error))
                finally:
                    with collector.lock:
                        collector.active.remove(self)

            def wait(self, *args, **kwargs):
                result = super().wait(*args, **kwargs)
                self.finish_native_bash()
                return result

            def communicate(self, *args, **kwargs):
                result = super().communicate(*args, **kwargs)
                self.finish_native_bash()
                return result

            def __exit__(self, *args):
                try:
                    return super().__exit__(*args)
                finally:
                    self.finish_native_bash()
        self.adapter = ObservedPopen
        subprocess.Popen = self.adapter
        NativeBashCollector.owner = self

    def error(self, message):
        """Preserve initial refusal causes without an unbounded error queue."""
        if len(self.errors) < 10:
            self.errors.append(message[:2048])

    def close(self):
        if self.original is not None:
            if subprocess.Popen is not self.adapter:
                raise InstrumentationError('Native Bash process adapter was replaced before shutdown')
            subprocess.Popen = self.original
            self.original = None
            NativeBashCollector.owner = None
        for process in list(self.active):
            self.error('Native Bash subprocess was not collected by its caller')
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            process.finish_native_bash()
        self.toolchain.check_unchanged()

    def save(self, path):
        value = {'schema': 2, 'mode': self.mode, 'toolchain_sha256': self.toolchain.digest,
            'profiles': {name: {'source_sha256': profile.SOURCE_SHA256,
                'static': self.static[name], 'processes': self.processes[name]}
                for name, profile in self.profiles.items()}, 'errors': self.errors}
        raw = (json.dumps(value, separators=(',', ':')) + '\n').encode('ascii')
        if len(raw) > self.MAX_RECEIPT_BYTES:
            raise InstrumentationError('Native Bash receipt byte limit exceeded')
        with Path(path).open('xb') as stream:
            stream.write(raw)

    @classmethod
    def checked_pair(cls, root, manifest, baseline, observed):
        """Stage facts from a source-matched pair; callers commit atomically."""
        toolchain = NativeBashToolchain(root, manifest)
        profiles = native_profiles(root)
        signatures = {name: [] for name in profiles}
        hits = {name: {metric: set() for metric in profile.METRICS} for name, profile in profiles.items()}
        for expected_mode, value in (('baseline', baseline), ('observed', observed)):
            if (not isinstance(value, dict) or set(value) != {'schema', 'mode',
                    'toolchain_sha256', 'profiles', 'errors'} or type(value['schema']) is not int or
                    value['schema'] != 2 or value['mode'] != expected_mode or
                    value['toolchain_sha256'] != toolchain.digest or value['errors'] != [] or
                    not isinstance(value['profiles'], dict) or set(value['profiles']) != set(profiles)):
                raise InstrumentationError('Native Bash receipt differs from its complete paired contract')
            pids, total_rows, count = set(), 0, 0
            for name, profile in profiles.items():
                record = value['profiles'][name]
                if (not isinstance(record, dict) or set(record) != {'source_sha256', 'static', 'processes'} or
                        record['source_sha256'] != profile.SOURCE_SHA256):
                    raise InstrumentationError('Native Bash component differs from its reviewed profile')
                static = record['static']
                if not isinstance(static, dict) or set(static) != {'pid', 'returncode', 'rows'}:
                    raise InstrumentationError('Missing complete native Bash syntax receipt')
                profile.check_process(static['rows'], static['pid'], static['returncode'], syntax=True)
                total_rows += len(static['rows'])
                processes = record['processes']
                if not isinstance(processes, list):
                    raise InstrumentationError('Invalid native Bash process receipts')
                count += len(processes)
                if count > cls.MAX_PROCESSES:
                    raise InstrumentationError('Excessive native Bash process receipts')
                keys = []
                for process in processes:
                    fields = {'pid', 'returncode', 'signature'} | ({'rows'} if expected_mode == 'observed' else set())
                    if (not isinstance(process, dict) or set(process) != fields or
                            type(process['pid']) is not int or process['pid'] <= 0 or process['pid'] in pids or
                            type(process['returncode']) is not int or not 0 <= process['returncode'] <= 255 or
                            not isinstance(process['signature'], str)):
                        raise InstrumentationError('Invalid or duplicate native Bash process receipt')
                    if len(process['signature']) != 64 or any(character not in '0123456789abcdef' for character in process['signature']):
                        raise InstrumentationError('Invalid native Bash invocation digest')
                    pids.add(process['pid'])
                    keys.append((process['signature'], process['returncode']))
                    if expected_mode == 'observed':
                        if not isinstance(process['rows'], list):
                            raise InstrumentationError('Missing native Bash process facts')
                        total_rows += len(process['rows'])
                        facts = profile.check_process(process['rows'], process['pid'], process['returncode'])
                        for metric, values in facts.items():
                            hits[name][metric].update(values)
                if total_rows > cls.MAX_TOTAL_ROWS:
                    raise InstrumentationError('Native Bash session event limit exceeded')
                signatures[name].append(Counter(keys))
        if any(pair[0] != pair[1] for pair in signatures.values()):
            raise InstrumentationError('Native and observed Bash invocation/results differ')
        toolchain.check_unchanged()
        return {name: (profile, hits[name]) for name, profile in profiles.items()}
