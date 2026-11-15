"""Use real TCP/UDP packets to distinguish firewall refusal from missing listeners."""
import argparse
import errno
import json
import selectors
import socket
import struct
import sys
import time


# Packet acceptance must retain every assertion, including negative controls.
if sys.flags.optimize:
    raise RuntimeError('Optimized Python is forbidden for the Docker validation recipe')


class PacketProbe:
    """Bounded IPv4 DNS probes and a private ADM listener used only as a witness."""

    MARKER = b'bind9-firewall-witness'
    DNS_QUERY = (struct.pack('!HHHHHH', 0xB154, 0, 1, 0, 0, 0) +
                 b'\x06recipe\x07example\x04test\x00\x00\x06\x00\x01')

    @staticmethod
    def receive(stream, length):
        data = b''
        while len(data) < length:
            part = stream.recv(length - len(data))
            if not part:
                raise RuntimeError('Truncated native DNS response')
            data += part
        return data

    def probe(self, address, protocol, mode, expected):
        reachable, valid, reason = False, False, None
        payload = self.DNS_QUERY if mode == 'dns' else self.MARKER
        kind = socket.SOCK_STREAM if protocol == 'tcp' else socket.SOCK_DGRAM
        try:
            with socket.socket(socket.AF_INET, kind) as stream:
                stream.settimeout(2)
                if protocol == 'tcp':
                    stream.connect((address, 53))
                    reachable = True
                    if expected == 'blocked':
                        raise AssertionError('The forbidden TCP listener is reachable')
                    stream.sendall(struct.pack('!H', len(payload)) + payload if mode == 'dns' else payload)
                    data = (self.receive(stream, struct.unpack('!H', self.receive(stream, 2))[0])
                            if mode == 'dns' else self.receive(stream, len(payload)))
                else:
                    stream.sendto(payload, (address, 53))
                    data, peer = stream.recvfrom(65535)
                    assert peer == (address, 53)
                    reachable = True
                if mode == 'dns':
                    assert len(data) >= 12
                    identity, flags, _, answers, _, _ = struct.unpack('!HHHHHH', data[:12])
                    valid = identity == 0xB154 and flags == 0x8480 and answers > 0
                else:
                    valid = data == self.MARKER
        except socket.timeout:
            reason = 'timeout'
        except OSError as error:
            if error.errno not in (errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.EACCES, errno.ETIMEDOUT):
                raise
            reason = errno.errorcode[error.errno]
        result = dict(address=address, protocol=protocol, mode=mode,
                      reachable=reachable, valid_response=valid, reason=reason, expected=expected)
        print(json.dumps(result), flush=True)
        assert (reachable and valid) if expected == 'allowed' else not reachable

    def serve(self, address):
        # The separate ADM witness proves that a negative network observation
        # comes from filtering even though a real listener remains available.
        selector = selectors.DefaultSelector()
        sockets = []
        try:
            for kind in (socket.SOCK_DGRAM, socket.SOCK_STREAM):
                stream = socket.socket(socket.AF_INET, kind)
                stream.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                stream.bind((address, 53))
                if kind == socket.SOCK_STREAM:
                    stream.listen(8)
                selector.register(stream, selectors.EVENT_READ, kind)
                sockets.append(stream)
            print('native ADM witness ready', flush=True)
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                for key, _ in selector.select(timeout=5):
                    if key.data == socket.SOCK_DGRAM:
                        data, peer = key.fileobj.recvfrom(4096)
                        key.fileobj.sendto(data, peer)
                    else:
                        connection, _ = key.fileobj.accept()
                        with connection:
                            connection.settimeout(2)
                            try:
                                connection.sendall(connection.recv(4096))
                            except socket.timeout:
                                pass  # A connecting probe need not send a payload.
            raise RuntimeError('The disposable witness exceeded its 30 minute lifetime')
        finally:
            selector.close()
            for stream in sockets:
                stream.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('serve', 'probe'))
    parser.add_argument('--address', required=True)
    parser.add_argument('--protocol', choices=('tcp', 'udp'), default='tcp')
    parser.add_argument('--mode', choices=('dns', 'echo'), default='dns')
    parser.add_argument('--expected', choices=('allowed', 'blocked'), default='allowed')
    arguments = parser.parse_args()
    instance = PacketProbe()
    if arguments.action == 'serve':
        instance.serve(arguments.address)
    else:
        instance.probe(arguments.address, arguments.protocol, arguments.mode, arguments.expected)
