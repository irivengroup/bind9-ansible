"""Canonical SELinux task decisions, refusal propagation and login publication.

The real Ansible modules execute in a disposable root. SELinux commands have
exact argv/reply boundaries; these tests make no kernel-label enforcement claim.
"""
import json
import os
from pathlib import Path
import subprocess

import pytest

from native_ansible_runner import NativeFilesystemScope, NativeRecordedBoundary, NativeSecurityRun

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')

LOGIN = Path('/etc/profile.d/bind9-sbin-path.sh')
PATHS = ['/var/log/named', '/var/log/named/default.log', '/var/log/named/security.log', '/var/log/named/xfer.log']
FILE_RULE = '/var/log/named(/.*)?'


def commands(rules='', labels=None, config='system_u:object_r:named_conf_t:s0 /etc/named.conf\n'):
    """Declare only command envelopes authored in the canonical task file."""
    replies = {}
    def put(argv, stdout='', rc=0):
        replies[json.dumps(argv)] = {'rc': rc, 'stdout': stdout}
    put(['semanage', 'fcontext', '-l'], rules)
    for action in ('-a', '-m'):
        put(['semanage', 'fcontext', action, '-f', 'a', '-t', 'named_log_t', FILE_RULE])
    put(['restorecon', '-RF', '/etc/named.conf', '/var/named', '/var/log/named'])
    put(['ls', '-Zd'] + PATHS, '\n'.join('system_u:object_r:named_log_t:s0 ' + path for path in PATHS)
        if labels is None else labels)
    put(['ls', '-Z', '/etc/named.conf'], config)
    return replies


def execute(tmp_path, variables, replies):
    with NativeRecordedBoundary(tmp_path, NativeSecurityRun, replies) as state:
        result = NativeSecurityRun('tasks.d/deploy/security.yml', variables).run()
        return result, json.loads(state.read_text())['calls']


@pytest.mark.parametrize('state', [None, 'disabled', 'permissive', 'enabled'])
def test_native_security_publishes_login_path_once_and_uses_selinux_only_when_enabled(tmp_path, state):
    variables = {} if state is None else {'ansible_selinux': {'status': state}}
    with NativeFilesystemScope(NativeSecurityRun):
        result, calls = execute(tmp_path, variables, commands())
        status, observed = result
        assert status == 0 and not observed.failed
        assert LOGIN.stat().st_mode & 0o777 == 0o644 and LOGIN.stat().st_uid == LOGIN.stat().st_gid == 0
        assert LOGIN.read_bytes() == (Path(__file__).resolve().parents[1] /
            'roles/bind9/files/bind9-sbin-path.sh').read_bytes()
        expected = [['restorecon', '-RF', '/etc/named.conf', '/var/named', '/var/log/named'],
                    ['ls', '-Z', '/etc/named.conf']]
        if state == 'enabled':
            expected = [['semanage', 'fcontext', '-l'],
                ['semanage', 'fcontext', '-a', '-f', 'a', '-t', 'named_log_t', FILE_RULE],
                expected[0], ['ls', '-Zd'] + PATHS, expected[1]]
        assert calls == expected
        # Source the actual published script twice, including an empty PATH and
        # a trailing slash-like prefix that must not masquerade as an entry.
        for before, after in [('', '/usr/local/sbin'), ('/bin', '/bin:/usr/local/sbin'),
                              ('/usr/local/sbin:/bin', '/usr/local/sbin:/bin'),
                              ('/usr/local/sbinary', '/usr/local/sbinary:/usr/local/sbin')]:
            actual = subprocess.run(['/bin/bash', '-c', 'source "$1"; source "$1"; printf "%s" "$PATH"',
                'login-path-test', str(LOGIN)], env={'PATH': before}, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, universal_newlines=True, timeout=5)
            assert actual.returncode == 0 and actual.stdout == after
            direct = subprocess.run(['/bin/bash', str(LOGIN)], env={'PATH': before},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            assert direct.returncode == 0 and direct.stdout == direct.stderr == b''
        stamp = LOGIN.stat().st_ino, LOGIN.stat().st_mtime_ns, LOGIN.read_bytes()
        # Reuse the same native boundaries for the repeat; their exact private
        # files belong to the first scope, so place the second set separately.
        repeat = tmp_path / 'repeat'
        repeat.mkdir(mode=0o700)
        (status, observed), repeated = execute(repeat, variables, commands())
        assert status == 0 and not observed.failed and repeated == expected
        assert (LOGIN.stat().st_ino, LOGIN.stat().st_mtime_ns, LOGIN.read_bytes()) == stamp


@pytest.mark.parametrize('rule,action', [
    ('', '-a'),
    (FILE_RULE + ' all files system_u:object_r:var_log_t:s0\n', '-m'),
    (FILE_RULE + ' all files system_u:object_r:named_log_t:s0\n', None),
    ('/var/log/named/.* all files system_u:object_r:named_log_t:s0\n', '-a'),
    (FILE_RULE + ' regular file system_u:object_r:named_log_t:s0\n', '-a')])
def test_native_security_selects_only_the_exact_all_files_rule(tmp_path, rule, action):
    with NativeFilesystemScope(NativeSecurityRun):
        (status, result), calls = execute(tmp_path, {'ansible_selinux': {'status': 'enabled'}}, commands(rule))
        assert status == 0 and not result.failed
        writes = [argv for argv in calls if argv[:2] == ['semanage', 'fcontext'] and argv[2] != '-l']
        assert writes == ([] if action is None else [['semanage', 'fcontext', action, '-f', 'a', '-t', 'named_log_t', FILE_RULE]])


@pytest.mark.parametrize('fault', ['query', 'write', 'restore', 'label-command', 'label-count', 'label-type', 'unlabelled'])
def test_native_security_refuses_each_failed_label_barrier(tmp_path, fault):
    replies = commands()
    labels = '\n'.join('system_u:object_r:named_log_t:s0 ' + path for path in PATHS)
    if fault == 'label-count':
        replies = commands(labels='\n'.join(labels.splitlines()[:3]))
    elif fault == 'label-type':
        replies = commands(labels=labels.replace('named_log_t', 'var_log_t', 1))
    elif fault == 'unlabelled':
        replies = commands(config='system_u:object_r:unlabeled_t:s0 /etc/named.conf\n')
    else:
        argv = {'query': ['semanage', 'fcontext', '-l'],
            'write': ['semanage', 'fcontext', '-a', '-f', 'a', '-t', 'named_log_t', FILE_RULE],
            'restore': ['restorecon', '-RF', '/etc/named.conf', '/var/named', '/var/log/named'],
            'label-command': ['ls', '-Zd'] + PATHS}[fault]
        replies[json.dumps(argv)]['rc'] = 1
    failures = dict(query='Read persistent SELinux file context rules for BIND logging',
        write='Persist the SELinux log context for the directory and all rotated files',
        restore='Restore SELinux contexts', **{'label-command': 'Inspect SELinux labels on the BIND log directory and files',
        'label-count': 'Require named_log_t on all managed log paths',
        'label-type': 'Require named_log_t on all managed log paths',
        'unlabelled': 'Reject unlabelled canonical configuration containing TSIG'})
    with NativeFilesystemScope(NativeSecurityRun):
        (status, result), calls = execute(tmp_path, {'ansible_selinux': {'status': 'enabled'}}, replies)
        assert status == 2 and result.failed == [failures[fault]]
        assert calls and LOGIN.is_file()


@pytest.mark.parametrize('fault', ['duplicate-path', 'unrelated-path', 'embedded-type', 'missing-config-label',
                                 'wrong-config-type', 'wrong-config-path', 'empty-config', 'extra-config'])
def test_native_security_requires_labels_for_each_exact_managed_path(tmp_path, fault):
    """A count or type substring cannot establish the labels of the requested files."""
    labels = '\n'.join('system_u:object_r:named_log_t:s0 ' + path for path in PATHS)
    config = 'system_u:object_r:named_conf_t:s0 /etc/named.conf\n'
    if fault == 'duplicate-path':
        labels = '\n'.join('system_u:object_r:named_log_t:s0 ' + PATHS[0] for _ in PATHS)
    elif fault == 'unrelated-path':
        labels = labels.replace('/var/log/named/xfer.log', '/var/log/other.log')
    elif fault == 'embedded-type':
        labels = labels.replace('system_u:object_r:named_log_t:s0',
                                'system_u:object_r:var_log_t:s0:named_log_t:ignored', 1)
    elif fault == 'missing-config-label':
        config = '? /etc/named.conf\n'
    elif fault == 'wrong-config-type':
        config = config.replace('named_conf_t', 'etc_t')
    elif fault == 'empty-config':
        config = ''
    elif fault == 'extra-config':
        config += config
    else:
        config = config.replace('/etc/named.conf', '/etc/other.conf')
    with NativeFilesystemScope(NativeSecurityRun):
        (status, result), calls = execute(tmp_path, {'ansible_selinux': {'status': 'enabled'}},
                                         commands(labels=labels, config=config))
        expected = ('Require named_log_t on all managed log paths' if fault in (
            'duplicate-path', 'unrelated-path', 'embedded-type') else
            'Reject unlabelled canonical configuration containing TSIG')
        assert status == 2 and result.failed == [expected]
        assert calls and LOGIN.is_file()


@pytest.mark.parametrize('level,separator', [('s0', ' '), ('s0:c0,c4', '\t'), ('s0-s15:c0.c1023', '  ')])
def test_native_security_accepts_exact_paths_with_native_security_levels_and_spacing(tmp_path, level, separator):
    """The invariant is the SELinux type and path, not a fixed user or MLS level."""
    labels = '\n'.join('unconfined_u:object_r:named_log_t:' + level + separator + path for path in reversed(PATHS))
    config = 'system_u:object_r:named_conf_t:' + level + separator + '/etc/named.conf\n'
    with NativeFilesystemScope(NativeSecurityRun):
        (status, result), calls = execute(tmp_path, {'ansible_selinux': {'status': 'enabled'}},
                                         commands(labels=labels, config=config))
        assert status == 0 and not result.failed and calls
