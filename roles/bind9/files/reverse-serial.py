#!/usr/bin/env python3
"""Advance SOA serial when a managed reverse zone changes within a transaction."""
import argparse
import re
import tarfile
from pathlib import Path

SOA = re.compile(r'(\(\s*)(\d{1,10})(\s+3600\s+900\s+1209600\s+300\s*\))')
MARKER = '; Bind9-Ansible-MANAGED-REVERSE'


def reconcile(path, old):
    current = path.read_text()
    if not current.startswith(MARKER + '\n'):
        raise ValueError('Refusing unmanaged reverse zone')
    match = SOA.search(current)
    if not match:
        raise ValueError('Missing reverse SOA serial')
    desired_serial = int(match.group(2))
    if old is None:
        return False
    if not old.startswith(MARKER + '\n'):
        raise ValueError('Refusing to overwrite pre-existing unmanaged reverse zone')
    old_match = SOA.search(old)
    if not old_match:
        raise ValueError('Missing previous reverse SOA serial')
    old_serial = int(old_match.group(2))
    normalize = lambda text: SOA.sub(lambda m: m.group(1) + 'SERIAL' + m.group(3), text, count=1)
    if normalize(old) == normalize(current):
        target = old_serial
    else:
        target = max(old_serial + 1, desired_serial)
    if target > 4294967295:
        raise ValueError('SOA serial overflow')
    if target != desired_serial:
        updated = current[:match.start(2)] + str(target) + current[match.end(2):]
        path.write_text(updated)
        return True
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', required=True)
    parser.add_argument('zones', nargs='*')
    args = parser.parse_args()
    with tarfile.open(args.snapshot, 'r:gz') as archive:
        previous = {}
        for member in archive:
            if member.isfile() and member.name.startswith('var/named/zones/'):
                previous[member.name] = archive.extractfile(member).read().decode('utf-8')
    for zone in args.zones:
        if not re.fullmatch(r'[a-zA-Z0-9.-]+(?:in-addr|ip6)\.arpa', zone):
            raise ValueError('Invalid reverse zone name')
        file = Path('/var/named/zones') / (zone + '.zone')
        reconcile(file, previous.get('var/named/zones/' + zone + '.zone'))


if __name__ == '__main__':
    main()
