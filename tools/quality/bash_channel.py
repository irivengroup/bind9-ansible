"""Bounded Unix datagrams for native Bash facts and kernel sender provenance.

The writer survives the interpreter's own fork operations. Its native hook
sets close-on-exec, so external commands cannot inherit or forge this stream.
"""
import fcntl
import os
import socket
import struct
import threading

from bash_protocol import BashEventSchema
from bind9_coverage import InstrumentationError


class NativeBashChannel:
    """Own one reader thread and a private descriptor in Bash's allowed range."""

    MAX_BYTES = 33554432
    MAX_ROWS = 100000
    MAX_PACKET = 1024

    def __init__(self, digest, owner_uid=None):
        if not isinstance(digest, str) or BashEventSchema.DIGEST.fullmatch(digest) is None:
            raise InstrumentationError('Native Bash channel requires an exact source digest')
        self.owner_uid = os.geteuid() if owner_uid is None else owner_uid
        if (type(self.owner_uid) is not int or not 0 <= self.owner_uid < 4294967295 or
                (self.owner_uid != os.geteuid() and os.geteuid() != 0)):
            raise InstrumentationError('Native Bash writer requires an authorized numeric UID')
        self.digest = digest
        self.reader, writer = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.descriptor = None
        try:
            # The native hook and SCM_CREDENTIALS must agree on the requested
            # child identity. Transfer only this private writer, never the reader.
            if self.owner_uid != os.geteuid():
                os.fchown(writer.fileno(), self.owner_uid, -1)
            os.fchmod(writer.fileno(), 0o600)
            self.reader.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            self.reader.settimeout(0.02)
            self.descriptor = fcntl.fcntl(writer.fileno(), fcntl.F_DUPFD_CLOEXEC, 64)
            if self.descriptor > 1023:
                raise InstrumentationError('No bounded private descriptor is available for native Bash')
        except BaseException:
            self.reader.close()
            if self.descriptor is not None:
                os.close(self.descriptor)
            raise
        finally:
            writer.close()
        self.rows, self.errors, self.total = [], [], 0
        self.stopped = threading.Event()
        self.closed = False
        self.thread = threading.Thread(target=self.read, name='bind9-native-bash-facts')
        try:
            self.thread.start()
        except BaseException:
            os.close(self.descriptor)
            self.reader.close()
            raise

    def read(self):
        try:
            while True:
                try:
                    raw, ancillary, flags, _ = self.reader.recvmsg(self.MAX_PACKET, socket.CMSG_SPACE(12))
                except socket.timeout:
                    if self.stopped.is_set():
                        return
                    continue
                self.total += len(raw)
                if (flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or self.total > self.MAX_BYTES or
                        len(self.rows) >= self.MAX_ROWS):
                    raise InstrumentationError('Truncated or excessive native Bash fact stream')
                credentials = [value for level, kind, value in ancillary
                               if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS]
                if len(credentials) != 1 or len(credentials[0]) != 12 or not raw.endswith(b'\n'):
                    raise InstrumentationError('Missing native Bash datagram provenance or framing')
                pid, uid, _ = struct.unpack('3i', credentials[0])
                row = BashEventSchema.check(raw[:-1].decode('ascii').split('\t'), self.digest)
                if int(row[2]) != pid or pid <= 0 or uid != self.owner_uid:
                    raise InstrumentationError('Native Bash sender differs from its kernel credentials')
                self.rows.append(row)
        except (InstrumentationError, UnicodeError, ValueError, struct.error) as error:
            # Fixed schema messages contain no script arguments or command data.
            self.errors.append(str(error) if isinstance(error, InstrumentationError)
                               else 'Malformed native Bash datagram encoding')
        except OSError:
            self.errors.append('Native Bash fact socket failed')
        finally:
            # A refusal stops the reader immediately and preserves the error.
            # Remaining senders receive a closed channel instead of an unbounded
            # producer/consumer retry cycle; the native hook preserves errno.
            self.reader.close()

    def close(self):
        if not self.closed:
            self.closed = True
            os.close(self.descriptor)
            self.stopped.set()
            self.thread.join(timeout=2)
            if self.thread.is_alive():
                self.reader.close()
                self.thread.join(timeout=0.1)
                self.errors.append('Native Bash reader did not stop within its deadline')
        if self.errors:
            raise InstrumentationError('; '.join(self.errors))
