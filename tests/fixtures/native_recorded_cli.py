#!/usr/bin/env python3
"""Exact private argv/reply transport for native Ansible command integration.

This records invocations; it never simulates BIND parsing or DNS semantics.
Actual validator and protocol controls remain independent integration tests.
"""
import json
import os
from pathlib import Path
import sys


class RecordedCli:
    """Refuse unknown calls and malformed replies in disconnected test roots."""

    def __init__(self, state, arguments):
        if (os.environ.get('BIND9_NATIVE_STATE_LAB') != '1' or os.geteuid() != 0 or
                not Path('/.dockerenv').is_file() or
                {path.name for path in Path('/sys/class/net').iterdir()} != {'lo'}):
            raise ValueError('Recorded CLI requires an explicitly isolated disposable root')
        self.path, self.arguments = Path(state), arguments
        self.path.resolve().relative_to('/tmp')
        info = self.path.lstat()
        if (self.path.is_symlink() or not self.path.is_file() or info.st_uid != 0 or
                info.st_nlink != 1 or info.st_mode & 0o777 != 0o600 or info.st_size > 65536):
            raise ValueError('Unsafe recorded command state')
        self.data = json.loads(self.path.read_text(encoding='utf-8'))

    def run(self):
        self.data['calls'].append(self.arguments)
        self.path.write_text(json.dumps(self.data), encoding='utf-8')
        reply = self.data['replies'].get(json.dumps(self.arguments))
        if isinstance(reply, dict) and set(reply) == {'sequence'}:
            sequence = reply['sequence']
            if (not isinstance(sequence, list) or not 1 <= len(sequence) <= 16 or
                    not all(self.valid_reply(value) for value in sequence)):
                raise ValueError('Malformed or exhausted bounded reply sequence')
            reply = sequence.pop(0)
            self.path.write_text(json.dumps(self.data), encoding='utf-8')
        if not self.valid_reply(reply):
            raise ValueError('Unknown recorded command or malformed fixture reply')
        print(reply.get('stdout', ''), end='')
        print(reply.get('stderr', ''), end='', file=sys.stderr)
        return reply['rc']

    @staticmethod
    def valid_reply(reply):
        """A sequence adds explicit retry state; ordinary malformed data stays refused."""
        return (isinstance(reply, dict) and type(reply.get('rc')) is int and 0 <= reply['rc'] <= 255 and
                isinstance(reply.get('stdout', ''), str) and isinstance(reply.get('stderr', ''), str))


if __name__ == '__main__':
    try:
        sys.exit(RecordedCli(sys.argv[1], sys.argv[2:]).run())
    except (ValueError, OSError, IndexError, KeyError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(91)
