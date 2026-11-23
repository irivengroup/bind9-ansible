#!/usr/bin/env python3
"""Inject one owned utility failure; delegate every other call unchanged."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys


class RouteUtilityFault:
    """Keep the nonce, prepared-journal and cleanup failures independent."""

    @staticmethod
    def release_consumed_facade(state, name):
        """Return a consumed one-shot boundary to its exact native delegate.

        The error is already durable in the private spec. Later healthy calls
        need no interpreter, particularly the many identity reads in rollback.
        Replace only the owned fixture entry; the running invocation still
        returns its selected output/status. Stateful multi-fault scenarios keep
        their facade and are never released here.
        """
        command = Path(state['script']).parent / 'bin' / name
        if (command.is_symlink() or not command.is_file() or
                command.read_bytes() != Path(__file__).read_bytes()):
            raise ValueError('The consumed rule must own its exact utility facade')
        delegate = Path(state['delegates'][name])
        if not delegate.is_absolute() or not delegate.is_file() or delegate == command:
            raise ValueError('The healthy utility delegate must remain independent')
        replacement = command.with_name(command.name + '.healthy')
        replacement.symlink_to(delegate)
        os.replace(str(replacement), str(command))

    @staticmethod
    def selected_failure(state, path, pending, name, args):
        """Affect one selected native call, optionally at a durable stage.

        Unmatched calls retain the native delegate. A one-shot error permits
        the same process's guarded rollback to use healthy observations again.
        """
        rule = state.get('rule')
        if not rule or not state.get('armed', True) or state['triggered'] or name != rule['command']:
            return None
        paths = {'parent': pending.parent.parent, 'state': pending.parent,
                 'lock': pending.parent / 'operations.flock', 'fd': Path('/proc/self/fd/9'),
                 'meta': pending / 'meta', 'progress': pending / 'progress', 'pending': pending}
        target = rule.get('target')
        matched = (('args' not in rule or args == rule['args']) and
            args[:len(rule.get('prefix', []))] == rule.get('prefix', []) and
            (target is None or (args and
             ((target == 'out' and args[-1].endswith('/out')) or
              (target == 'retirement' and Path(args[-1]).parent == pending.parent and
               Path(args[-1]).name.startswith('.front-routes.retired.')) or
              (target in paths and args[-1] == str(paths[target]))))))
        if 'progress' in rule:
            matched = matched and (pending / 'progress').is_file() and (
                pending / 'progress').read_text() == rule['progress'] + '\n'
        if not matched:
            return None
        state['matches'] = state.get('matches', 0) + 1
        if state['matches'] <= rule.get('skip_matches', 0):
            path.write_text(json.dumps(state))
            return None
        if 'signal' in rule:
            if name != 'sync' or rule['signal'] not in ('SIGHUP', 'SIGINT', 'SIGTERM'):
                raise ValueError('Signal injection requires one reviewed sync boundary')
            result = subprocess.run([state['delegates'][name]] + args, check=False, timeout=10)
            if result.returncode:
                return result.returncode
            parent = os.getppid()
            argv = Path('/proc/%d/cmdline' % parent).read_bytes().split(b'\0')
            if (len(argv) < 2 or argv[1] != state['script'].encode() or
                    not Path(argv[0].decode()).name.endswith('bash')):
                raise ValueError('Signal target must be the exact owned Bash process')
            state['triggered'] = True
            state['trigger_args'] = args
            path.write_text(json.dumps(state))
            RouteUtilityFault.release_consumed_facade(state, name)
            os.kill(parent, getattr(signal, rule['signal']))
            return 0
        state['triggered'] = True
        state['trigger_args'] = args
        path.write_text(json.dumps(state))
        RouteUtilityFault.release_consumed_facade(state, name)
        result = rule.get('returncode', 2)
        if rule.get('drain_stdin', False):
            # A successful sort observation consumes its input before replying;
            # otherwise a race with the writer could manufacture a SIGPIPE.
            if name != 'sort' or result != 0:
                raise ValueError('Input draining requires a successful sort observation')
            if len(sys.stdin.buffer.read(1048577)) > 1048576:
                raise ValueError('Excessive isolated sort input')
        sys.stdout.write(rule.get('stdout', ''))
        if result:
            print('Injected selected utility failure', file=sys.stderr)
        return result

    @staticmethod
    def replace_after_move(state, target, pending):
        """Model one operator replacement; retain the complete owned inode nearby."""
        if state['transition_stage'] != 'released':
            held = pending.parent / '.operator-held-journal'
            if held.exists() or held.is_symlink():
                raise ValueError('The synthetic journal holding path must be absent')
            state['held_journal'] = {p.name: [p.stat().st_ino, p.read_bytes().hex()] for p in target.iterdir()}
            target.rename(held)
            state['held_path'] = str(held)
        shape = state['foreign_shape']
        if shape == 'file':
            target.write_bytes(b'operator transition bytes\n')
            target.chmod(0o600)
        elif shape == 'directory':
            target.mkdir(mode=0o700)
            (target / 'operator-evidence').write_bytes(b'operator transition bytes\n')
        elif shape in ('symlink', 'dangling-symlink'):
            target.symlink_to(state['foreign_target'])
        else:
            raise ValueError('Unsupported operator transition shape')
        identity = target.lstat()
        state.update(triggered=True, foreign_path=str(target), foreign_identity=[identity.st_dev, identity.st_ino])

    def run(self):
        path = Path(os.environ['BIND_ROUTE_UTILITY_SPEC'])
        state = json.loads(path.read_text())
        name, args = Path(sys.argv[0]).name, sys.argv[1:]
        pending = Path(os.environ['BIND_ROUTE_UTILITY_PENDING'])
        selected = self.selected_failure(state, path, pending, name, args)
        if selected is not None:
            return selected
        # Exercise one acknowledged native cleanup step followed by a failed
        # command status. Keep the same public failure across the old unlink
        # boundary and its atomic directory-retirement replacement.
        retiring_move = (name == 'mv' and len(args) == 5 and args[:3] == ['-T', '-n', '--'] and
                         args[3] == str(pending) and
                         args[4].startswith(str(pending.parent / '.front-routes.retired.')))
        if (state['fault'] == 'retirement-aliased-identities' and not state['triggered'] and
                name == 'stat' and len(args) == 4 and args[:3] == ['-c', '%d:%i', '--'] and
                Path(args[-1]).parent == pending.parent and
                Path(args[-1]).name.startswith('.front-routes.retired.')):
            target = Path(args[-1])
            before = pending.stat()
            state['journal_identity'] = [before.st_dev, before.st_ino]
            names = ('meta', 'operations', 'profile', 'kernel', 'addresses', 'rules', 'clients', 'checksums')
            state['immutable_journal_before_alias'] = {n: [(pending / n).stat().st_ino,
                (pending / n).read_bytes().hex()] for n in names}
            mounted = subprocess.run([state['mount_delegate'], '--bind', str(pending), str(target)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            if mounted.returncode:
                raise ValueError('Private alias mount failed')
            try:
                alias = target.stat()
                state['alias_identity'] = [alias.st_dev, alias.st_ino]
                observed = subprocess.run([state['delegates'][name]] + args, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, universal_newlines=True, timeout=5)
            finally:
                released = subprocess.run([state['unmount_delegate'], str(target)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
                if released.returncode:
                    raise ValueError('Private alias unmount failed')
            if observed.returncode or observed.stderr:
                raise ValueError('Actual stat failed on the private bind alias')
            after = target.stat()
            state.update(triggered=True, alias_unmounted=True,
                reservation_after_unmount_identity=[after.st_dev, after.st_ino], native_alias_stat=observed.stdout)
            path.write_text(json.dumps(state))
            # The real alias has been observed and unmounted. Its one-shot
            # fault is complete; rollback must use native stat directly.
            self.release_consumed_facade(state, name)
            sys.stdout.write(observed.stdout)
            return observed.returncode
        if state['fault'] == 'retirement-foreign-transition' and not state['triggered'] and name == 'mv':
            returning_move = (len(args) == 5 and args[:3] == ['-T', '-n', '--'] and args[-1] == str(pending) and
                Path(args[3]).parent == pending.parent and Path(args[3]).name.startswith('.front-routes.retired.'))
            if retiring_move and not state.get('outbound_seen', False):
                original = pending.stat()
                state['original_identity'] = [original.st_dev, original.st_ino]
                state['original_journal'] = {p.name: [p.stat().st_ino, p.read_bytes().hex()] for p in pending.iterdir()}
                result = subprocess.run([state['delegates'][name]] + args, check=False, timeout=10)
                if result.returncode:
                    return result.returncode
                state['outbound_seen'] = True
                if state['transition_stage'] == 'outbound':
                    self.replace_after_move(state, Path(args[-1]), pending)
                path.write_text(json.dumps(state))
                return 0 if state['transition_stage'] == 'outbound' else 1
            if returning_move and state.get('outbound_seen', False):
                result = subprocess.run([state['delegates'][name]] + args, check=False, timeout=10)
                if result.returncode:
                    return result.returncode
                target = pending if state['transition_stage'] == 'restored' else Path(args[3])
                self.replace_after_move(state, target, pending)
                path.write_text(json.dumps(state))
                return 0
        if (state['fault'] == 'retirement-empty-reservation-error' and not state['triggered'] and
                name == 'rmdir' and len(args) == 2 and args[0] == '--' and
                Path(args[-1]).parent == pending.parent and Path(args[-1]).name.startswith('.front-routes.retired.')):
            state['triggered'] = True
            path.write_text(json.dumps(state))
            return 1
        if state['fault'] == 'retirement-foreign-destination' and not state['triggered'] and retiring_move:
            target = Path(args[-1])
            shape = state['foreign_shape']
            if shape == 'file':
                target.write_bytes(b'operator destination bytes\n')
            elif shape == 'directory':
                target.mkdir(mode=0o700)
            elif shape in ('symlink', 'dangling-symlink'):
                target.symlink_to(state['foreign_target'])
            else:
                raise ValueError('Unsupported foreign retirement shape')
            identity = target.lstat()
            result = subprocess.run([state['delegates'][name]] + args, check=False, timeout=10)
            state.update(triggered=True, native_retirement_returncode=result.returncode,
                         retirement_destination=str(target), foreign_identity=[identity.st_dev, identity.st_ino])
            path.write_text(json.dumps(state))
            return result.returncode
        if (state['fault'] == 'retirement-back-rename-error' and state['triggered'] and
                not state['cleanup_triggered'] and name == 'mv' and len(args) == 5 and
                args[:3] == ['-T', '-n', '--'] and args[-1] == str(pending) and
                Path(args[3]).parent == pending.parent and Path(args[3]).name.startswith('.front-routes.retired.')):
            state['cleanup_triggered'] = True
            path.write_text(json.dumps(state))
            print('Injected failure of the first compensation rename', file=sys.stderr)
            return 1
        if (state['fault'] in ('retirement-after-success', 'retirement-foreign-active', 'retirement-back-rename-error') and not state['triggered'] and
                ((name == 'rm' and args == ['--', str(pending / 'meta')]) or retiring_move)):
            network = json.loads(Path(os.environ['BIND_NETWORK_DOUBLE_STATE']).read_text())
            if any(not operation['persistent'] and operation['action'] == '+ipv4.routes'
                   for operation in network['operations']):
                result = subprocess.run([state['delegates'][name]] + args, check=False, timeout=10)
                if result.returncode:
                    return result.returncode
                state['triggered'] = True
                state['completed_cleanup_command'] = [name] + args
                if state['fault'] == 'retirement-foreign-active' and retiring_move:
                    retired = Path(args[-1]).stat()
                    shape = state.get('foreign_active_shape', 'directory')
                    if shape == 'directory':
                        pending.mkdir(mode=0o700)
                        (pending / 'operator-evidence').write_bytes(b'operator active-path bytes\n')
                    elif shape == 'file':
                        pending.write_bytes(b'operator active-path bytes\n')
                    elif shape in ('symlink', 'dangling-symlink'):
                        pending.symlink_to(state['foreign_target'])
                    else:
                        raise ValueError('Unsupported foreign active-path shape')
                    foreign = pending.lstat()
                    state.update(retired_identity=[retired.st_dev, retired.st_ino],
                                 foreign_identity=[foreign.st_dev, foreign.st_ino])
                if state['fault'] == 'retirement-foreign-active' and state.get('retirement_report_success', False):
                    state['retirement_native_returncode'] = result.returncode
                    path.write_text(json.dumps(state))
                    return result.returncode
                path.write_text(json.dumps(state))
                print('Injected error after successful journal retirement step', file=sys.stderr)
                return 1
        if (state['fault'] == 'retirement-allocation-error' and not state['triggered'] and
                name == 'mktemp' and len(args) == 2 and args[0] == '-d' and
                Path(args[-1]).parent == pending.parent and Path(args[-1]).name.startswith('.front-routes.retired.')):
            state['triggered'] = True
            path.write_text(json.dumps(state))
            return 1
        if (state['fault'] == 'retirement-compensation-sync-error' and name == 'sync' and
                args == ['--', str(pending.parent)] and state.get('state_sync_errors', 0) < 2):
            state['triggered'] = True
            state['state_sync_errors'] = state.get('state_sync_errors', 0) + 1
            path.write_text(json.dumps(state))
            return 1
        if (state['fault'] == 'retirement-unexpected-entry' and not state['triggered'] and
                name == 'stat' and args == ['-c', '%u:%a:%h', '--', str(pending / 'progress')] and
                (pending / 'progress').read_text() == '2 done\n'):
            result = subprocess.run([state['delegates'][name]] + args, check=False, timeout=10,
                                    stdout=subprocess.PIPE)
            if result.returncode:
                return result.returncode
            state['journal_before_foreign_entry'] = {p.name: [p.stat().st_ino, p.read_bytes().hex()]
                                                      for p in pending.iterdir()}
            foreign = pending / 'operator-evidence'
            foreign.write_bytes(b'operator late journal bytes\n')
            foreign.chmod(0o600)
            state.update(triggered=True, foreign_identity=[foreign.stat().st_dev, foreign.stat().st_ino])
            path.write_text(json.dumps(state))
            sys.stdout.buffer.write(result.stdout)
            return 0
        reason = None
        if (state['fault'] == 'nonce' and name == 'cat' and
                args == ['/proc/sys/kernel/random/uuid'] and not state['triggered']):
            reason = 'nonce'
        elif (state['fault'] in ('prepared', 'prepared-cleanup') and name == 'stat' and
              args[:2] == ['-c', '%s'] and args[-1].endswith('/out') and
              (pending / 'progress').is_file() and not state['triggered']):
            reason = 'prepared-observation'
        elif (state['fault'] == 'prepared-cleanup' and not state['cleanup_triggered'] and
              ((name == 'rm' and args == ['--', str(pending / 'meta')]) or retiring_move)):
            reason = 'prepared-cleanup'
        elif (state['fault'] in ('after-apply-cleanup', 'commit-journal-cleanup') and
              name in ('rm', 'mv') and not state['triggered'] and
              ((state['fault'] == 'after-apply-cleanup' and name == 'rm' and len(args) == 4 and
                args[:2] == ['-f', '--'] and args[2].endswith('/out') and args[3].endswith('/err')) or
               (state['fault'] == 'commit-journal-cleanup' and
                ((name == 'rm' and args == ['--', str(pending / 'meta')]) or retiring_move)))):
            network = json.loads(Path(os.environ['BIND_NETWORK_DOUBLE_STATE']).read_text())
            if any(not operation['persistent'] and operation['action'] == '+ipv4.routes'
                   for operation in network['operations']):
                reason = state['fault']
        if reason:
            state['cleanup_triggered' if reason == 'prepared-cleanup' else 'triggered'] = True
            path.write_text(json.dumps(state))
            print('Injected route utility failure: ' + reason, file=sys.stderr)
            return 1 if reason in ('prepared-cleanup', 'after-apply-cleanup', 'commit-journal-cleanup') else 2
        delegate = state['delegates'][name]
        os.execv(delegate, [delegate] + args)


if __name__ == '__main__':
    raise SystemExit(RouteUtilityFault().run())
