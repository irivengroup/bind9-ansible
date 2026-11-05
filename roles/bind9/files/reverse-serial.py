#!/usr/bin/env python3
"""Advance SOA serial when a managed reverse zone changes against the pre-deployment backup."""
import argparse
import gzip
import os
import re
import stat
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

SOA = re.compile(r'(?m)^(@[ \t]+IN[ \t]+SOA[ \t]+\S+[ \t]+\S+[ \t]+\([ \t]*)([0-9]{1,10})([ \t]+3600[ \t]+900[ \t]+1209600[ \t]+300[ \t]*\)[ \t]*)$')
MARKER = '; Bind9-Ansible-MANAGED-REVERSE'
# The canonical template carries the general ownership marker first so restore
# can delete only owned files. Accept both this header and the legacy one.
HEADERS = (MARKER + '\n', '; Bind9-Ansible-MANAGED-ZONE\n' + MARKER + '\n')


class ReverseZoneSerial:
    """Observe one owned zone, then publish a complete, durable replacement."""

    MAX_BYTES = 16 * 1024 * 1024  # Match the management static-asset budget.
    MAX_METADATA = 1024 * 1024

    def __init__(self, path):
        self.path = Path(path)
        self.original, self.info, self.attributes = self.read(self.path)

    @staticmethod
    def stamp(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mode, info.st_uid,
                info.st_gid, info.st_nlink, info.st_mtime_ns, info.st_ctime_ns)

    @classmethod
    def attributes_for(cls, descriptor):
        """Preserve extended metadata, including a present SELinux label/ACL.

        Attribute failures are explicit. Replacing a labelled zone with a new
        inode must not silently discard its security metadata.
        """
        attributes = {}
        total = 0
        for name in os.listxattr(descriptor):
            value = os.getxattr(descriptor, name)
            total += len(name.encode('utf-8')) + len(value)
            if total > cls.MAX_METADATA:
                raise ValueError('Reverse zone metadata exceeds the 1 MiB bound')
            attributes[name] = value
        return attributes

    @classmethod
    def read(cls, path):
        """Reject links, special files, truncated reads and concurrent changes."""
        descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    info.st_uid != os.geteuid() or info.st_mode & 0o022):
                raise ValueError('Unsafe reverse zone ownership, type, permissions or links')
            if info.st_size > cls.MAX_BYTES:
                raise ValueError('Reverse zone exceeds the 16 MiB static-asset bound')
            data = stream.read(cls.MAX_BYTES + 1)
            if len(data) != info.st_size or b'\x00' in data:
                raise ValueError('Incomplete or invalid reverse-zone text')
            attributes = cls.attributes_for(stream.fileno())
            observed = cls.stamp(info)
            if observed != cls.stamp(os.fstat(stream.fileno())) or observed != cls.stamp(path.lstat()):
                raise ValueError('Reverse zone changed during observation')
        return data.decode('utf-8', errors='strict'), info, attributes

    @staticmethod
    def serial(text, previous=False):
        """Only one actual apex SOA may determine the serial; comments never do."""
        if not isinstance(text, str) or not text.startswith(HEADERS):
            raise ValueError('Refusing pre-existing unmanaged reverse zone' if previous else 'Refusing unmanaged reverse zone')
        matches = list(SOA.finditer(text))
        if len(matches) != 1:
            raise ValueError('Missing or ambiguous previous reverse SOA serial' if previous else 'Missing or ambiguous reverse SOA serial')
        match = matches[0]
        if int(match.group(2)) > 4294967295:
            raise ValueError('SOA serial overflow')
        return match

    def prepare(self, old):
        """Compute the complete candidate without changing a live file."""
        current = self.serial(self.original)
        if old is None:
            return None
        previous = self.serial(old, previous=True)
        same = (old[:previous.start(2)] + 'SERIAL' + old[previous.end(2):] ==
                self.original[:current.start(2)] + 'SERIAL' + self.original[current.end(2):])
        old_serial, desired = int(previous.group(2)), int(current.group(2))
        target = old_serial if same else max(old_serial + 1, desired)
        if target > 4294967295:
            raise ValueError('SOA serial overflow')
        if target == desired:
            return None
        return (self.original[:current.start(2)] + str(target) + self.original[current.end(2):]).encode('utf-8')

    def publish(self, data):
        """Stage/fsync, verify the observed zone again, then rename once."""
        descriptor, temporary = tempfile.mkstemp(prefix='.reverse-serial-', dir=str(self.path.parent))
        try:
            with os.fdopen(descriptor, 'w+b') as stream:
                if stream.write(data) != len(data):
                    raise ValueError('Incomplete reverse SOA staging write')
                stream.flush()
                stream.seek(0)
                if stream.read(len(data) + 1) != data:
                    raise ValueError('Reverse SOA staging readback differs')
                # Data writes can clear security attributes. Apply and verify
                # final ownership/mode/xattrs only after the complete content.
                os.fchown(stream.fileno(), self.info.st_uid, self.info.st_gid)
                os.fchmod(stream.fileno(), stat.S_IMODE(self.info.st_mode))
                for name, value in self.attributes.items():
                    os.setxattr(stream.fileno(), name, value)
                if self.attributes_for(stream.fileno()) != self.attributes:
                    raise ValueError('Reverse zone metadata staging readback differs')
                os.fsync(stream.fileno())
            current, info, _ = self.read(self.path)
            if current != self.original or self.stamp(info) != self.stamp(self.info):
                raise ValueError('Reverse zone changed before publication')
            os.replace(temporary, str(self.path))
            directory = os.open(str(self.path.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def reconcile(self, old):
        data = self.prepare(old)
        if data is None:
            return False
        self.publish(data)
        return True


class ArchiveStream:
    """Bound all inflated bytes, including PAX headers and ignored tar padding."""

    def __init__(self, stream, limit):
        self.stream, self.limit, self.count, self.tail = stream, limit, 0, b''

    def read(self, size):
        data = self.stream.read(min(size, self.limit - self.count + 1))
        self.count += len(data)
        if self.count > self.limit:
            raise ValueError('Reverse backup exceeds the inflated stream bound')
        self.tail = (self.tail + data)[-1024:]
        return data

    def finish(self):
        # tarfile stops at the first zero header. Drain within the same budget
        # to validate gzip CRC/footer and the backup contract's tar end blocks.
        while self.read(65536):
            pass
        if self.count < 1024 or self.count % 512 or self.tail != b'\x00' * 1024:
            raise ValueError('Incomplete reverse backup tar records/end blocks')


class ReverseArchive:
    """Read requested prior zones; never extract or load unrelated file data.

    The native backup contract already refuses links and foreign paths. Scan
    budgets bound malformed input: 65,536 members, 1 GiB expanded payload and
    the same 16 MiB maximum per selected static zone as the management engine.
    """

    MAX_MEMBERS = 65536
    MAX_EXPANDED = 1024 * 1024 * 1024
    MAX_STREAM = MAX_EXPANDED + MAX_MEMBERS * 1024 + 10240

    @classmethod
    def read(cls, path, zones):
        selected = {'var/named/zones/' + zone + '.zone' for zone in zones}
        descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    info.st_uid != os.geteuid() or info.st_mode & 0o077):
                raise ValueError('Reverse backup must be a private owned regular file without links')
            with gzip.GzipFile(fileobj=stream, mode='rb') as compressed:
                bounded = ArchiveStream(compressed, cls.MAX_STREAM)
                previous = cls.scan(bounded, selected)
                bounded.finish()
            observed = ReverseZoneSerial.stamp(info)
            if observed != ReverseZoneSerial.stamp(os.fstat(stream.fileno())) or observed != ReverseZoneSerial.stamp(Path(path).lstat()):
                raise ValueError('Reverse backup changed during observation')
        return previous

    @classmethod
    def scan(cls, stream, selected):
        """Validate the full member inventory while retaining selected zones only."""
        previous, seen, total = {}, set(), 0
        with tarfile.open(fileobj=stream, mode='r|') as archive:
            for member in archive:
                name = member.name.rstrip('/')
                if (len(seen) >= cls.MAX_MEMBERS or len(name) > 4096 or member.size < 0 or
                        PurePosixPath(name).is_absolute() or
                        any(not part or part in ('.', '..') for part in name.split('/')) or
                        '\\' in name or '\x00' in name or name in seen or
                        not (member.isfile() or member.isdir()) or
                        not (name in ('etc/named.conf', 'etc/named', 'var/named') or
                             name.startswith(('etc/named/', 'var/named/')))):
                    raise ValueError('Unsafe or excessive reverse backup member inventory')
                seen.add(name)
                total += member.size
                if total > cls.MAX_EXPANDED:
                    raise ValueError('Reverse backup exceeds the 1 GiB expanded scan bound')
                if name in selected:
                    if not member.isfile() or member.size > ReverseZoneSerial.MAX_BYTES:
                        raise ValueError('Invalid or oversized previous reverse zone')
                    with archive.extractfile(member) as saved:
                        data = saved.read(ReverseZoneSerial.MAX_BYTES + 1)
                    if len(data) != member.size or b'\x00' in data:
                        raise ValueError('Incomplete or invalid previous reverse zone')
                    previous[name] = data.decode('utf-8', errors='strict')
        return previous


def reconcile(path, old):
    """Keep the original helper API; report change only after durable publication."""
    return ReverseZoneSerial(path).reconcile(old)


def main(argv=None, zone_root=Path('/var/named/zones')):
    """Validate every requested zone before publishing the first replacement."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', required=True)
    parser.add_argument('zones', nargs='*')
    args = parser.parse_args(argv)
    if any(not re.fullmatch(r'[a-zA-Z0-9.-]+(?:in-addr|ip6)\.arpa', zone) for zone in args.zones):
        raise ValueError('Invalid reverse zone name')
    previous = ReverseArchive.read(args.backup, args.zones)
    pending = []
    for zone in dict.fromkeys(args.zones):
        target = ReverseZoneSerial(Path(zone_root) / (zone + '.zone'))
        data = target.prepare(previous.get('var/named/zones/' + zone + '.zone'))
        if data is not None:
            pending.append((target, data))
    for target, data in pending:
        target.publish(data)


if __name__ == '__main__':
    main()
