#!/usr/bin/env python3
"""CLI facade for the existing unit double; native Ansible modules stay intact.

This is a disposable-container command boundary, never a systemd/daemon proof.
Only the exact operations used by the inspected role are supported.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import sys

from systemd_contract import UnitContract


class NativeUnitCli:
    """Translate inspected systemctl calls into the shared unit state model."""

    UNITS = {'named', 'named-setup-rndc.service', 'named-private-dnssec.service',
             'named-private-dnssec.timer', 'named-healthcheck.service', 'named-healthcheck.timer'}

    def __init__(self, state, arguments):
        if (os.environ.get('BIND9_NATIVE_STATE_LAB') != '1' or os.geteuid() != 0 or
                not Path('/.dockerenv').is_file() or
                {path.name for path in Path('/sys/class/net').iterdir()} != {'lo'}):
            raise ValueError('Native unit CLI requires an explicitly isolated disposable root')
        self.path, self.arguments = Path(state), list(arguments)
        if (self.path.is_symlink() or not self.path.is_file() or
                not str(self.path.resolve()).startswith('/tmp/') or self.path.stat().st_size > 65536):
            raise ValueError('Native unit CLI refuses an unsafe state fixture')
        self.data = json.loads(self.path.read_text(encoding='utf-8'))

    def legacy(self, arguments, quiet=False):
        previous = sys.argv
        sys.argv = [previous[0], str(self.path)] + arguments
        try:
            if quiet:
                with contextlib.redirect_stdout(io.StringIO()):
                    return UnitContract().run()
            return UnitContract().run()
        finally:
            sys.argv = previous

    def run(self):
        args = self.arguments
        reply = self.data.get('faults', {}).get(json.dumps(args))
        if reply is not None:
            if (type(reply.get('rc')) is not int or not isinstance(reply.get('stdout', ''), str) or
                    not isinstance(reply.get('stderr', ''), str)):
                raise ValueError('Malformed native CLI fault fixture')
            print(reply.get('stdout', ''), end='')
            print(reply.get('stderr', ''), end='', file=sys.stderr)
            return reply['rc']
        if args == ['daemon-reload']:
            return self.legacy([json.dumps({'daemon_reload': True})], quiet=True)
        if not args:
            raise ValueError('A native CLI operation is required')
        operation, tail = args[0], args[1:]
        quiet = '--quiet' in tail
        if quiet:
            tail = [value for value in tail if value != '--quiet']
        if operation == 'is-enabled' and tail[-1:] == ['-l']:
            tail = tail[:-1]
        if len(tail) != 1 or tail[0] not in self.UNITS:
            raise ValueError('Native unit CLI refuses an unreviewed unit or option')
        unit = tail[0]
        if operation in ('is-active', 'is-enabled'):
            return self.legacy([operation, unit], quiet=quiet)
        if quiet:
            raise ValueError('Native unit CLI refuses an unreviewed quiet operation')
        if operation == 'show':
            state = self.data['units'].get(unit, {'active': False, 'enabled': False})
            assert type(state['active']) is bool and type(state['enabled']) is bool
            print('LoadState=loaded\nActiveState=' + ('active' if state['active'] else 'inactive') +
                  '\nUnitFileState=' + ('enabled' if state['enabled'] else 'disabled'))
            return 0
        if operation in ('enable', 'disable'):
            request = {'name': unit, 'enabled': operation == 'enable'}
        elif operation in ('start', 'stop', 'restart', 'reload'):
            request = {'name': unit, 'state': {'start': 'started', 'stop': 'stopped',
                       'restart': 'restarted', 'reload': 'reloaded'}[operation]}
        else:
            raise ValueError('Native unit CLI refuses an unreviewed operation')
        return self.legacy([json.dumps(request)], quiet=True)


if __name__ == '__main__':
    try:
        sys.exit(NativeUnitCli(sys.argv[1], sys.argv[2:]).run())
    except (IndexError, ValueError, AssertionError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(64)
