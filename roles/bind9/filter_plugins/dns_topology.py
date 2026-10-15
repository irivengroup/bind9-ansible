"""Plan local DNS delegation cuts without opening general Internet recursion.

Executed on the Ansible controller. The synthetic root remains authoritative
for undeclared names; explicit cuts let conditional forwarding escape it.
"""
import base64
import binascii
import ipaddress
import re


class DnsTopology:
    """Validate zone relationships and derive nearest-parent forwarding cuts."""

    NATIVE = ('localhost', 'localhost.localdomain', '127.in-addr.arpa',
              '0.in-addr.arpa', '255.in-addr.arpa',
              ipaddress.IPv6Address('::1').reverse_pointer)
    DOMAIN = re.compile(r'^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')

    def __init__(self, authoritative, forwarded):
        self.forwarded = forwarded
        self.auth_names = [self.domain(zone['domain']) for zone in authoritative]
        self.forward_names = [self.domain(zone['domain']) for zone in forwarded]
        names = self.auth_names + self.forward_names
        if any(len(name) > 249 for name in self.auth_names) or any(len(name) > 235 for name in self.forward_names):
            raise ValueError('Zone suffix is too long for generated NS/glue hostnames')
        if len(set(names)) != len(names):
            raise ValueError('DNS zone names must be unique, ignoring case')
        for name in names:
            if any(self.within(name, native) or self.within(native, name) for native in self.NATIVE):
                raise ValueError('Managed or forwarded zone overlaps a distribution-owned native zone: ' + name)
        for name in self.forward_names:
            if any(self.within(auth, name) for auth in self.auth_names):
                raise ValueError('Forwarding cannot enclose an authoritative zone: ' + name)
            if any(other != name and self.within(other, name) for other in self.forward_names):
                raise ValueError('Nested forwarding zones require explicit DNS design: ' + name)
        for zone in forwarded:
            if 'dnssec_validate' in zone and not isinstance(zone['dnssec_validate'], bool):
                raise ValueError('dnssec_validate must be a YAML boolean')
            if any(not FilterModule.forwarder(server) for server in zone['forwarders']):
                raise ValueError('Forwarders must be valid unicast IPv4 or IPv6 endpoints')
        # Data at/below a delegation cut would be hidden by forwarding. Reject
        # it instead of silently changing authoritative answers on deployment.
        for zone, parent in zip(authoritative, self.auth_names):
            for record in zone.get('records', []):
                owner = parent if record['name'] == '@' else record['name'].lower() + '.' + parent
                if any(self.within(child, parent) and self.within(owner, child) for child in self.forward_names):
                    raise ValueError('Authoritative record conflicts with forwarding delegation: ' + owner)

    @classmethod
    def domain(cls, value):
        """Accept canonical suffix spelling; compare DNS names without case."""
        if not isinstance(value, str) or not cls.DOMAIN.fullmatch(value.lower()):
            raise ValueError('Invalid DNS zone suffix: ' + str(value))
        return value.lower()

    @staticmethod
    def within(name, parent):
        """Return whether name is parent or a descendant at a label boundary."""
        return name == parent or name.endswith('.' + parent)

    def delegations(self, parent):
        """Return NS/glue metadata for cuts whose nearest local parent is parent.

        These cuts belong only to the private authoritative data. Conditional
        forwarders still use their configured ports; NS glue contains addresses.
        """
        parent = parent.lower()
        output = []
        for zone, child in zip(self.forwarded, self.forward_names):
            ancestors = [name for name in self.auth_names if self.within(child, name)]
            nearest = max(ancestors, key=len) if ancestors else '.'
            if nearest != parent:
                continue
            servers = []
            for number, server in enumerate(zone['forwarders'], 1):
                address = ipaddress.ip_address(server['address'])
                servers.append({'name': 'bind-forwarder-' + str(number) + '.' + child + '.',
                                'type': 'A' if address.version == 4 else 'AAAA', 'address': str(address)})
            output.append({'domain': child + '.', 'servers': servers})
        return output


class FilterModule:
    """Expose strict preflight validation and derived delegation records."""

    def filters(self):
        return {'dns_topology_valid': self.valid, 'forward_delegations': self.delegations,
                'dns_identity_valid': self.identity, 'dns_client_safe': self.client,
                'dns_forwarder_safe': self.forwarder}

    @staticmethod
    def forwarder(server):
        """Require a literal unicast endpoint and a strictly typed decimal port.

        Mapped IPv4 and scoped IPv6 are refused so textual aliases cannot bypass
        the role's explicit local-endpoint checks or require interface guessing.
        """
        if not isinstance(server, dict):
            return False
        value = server.get('address')
        if not isinstance(value, str) or value != value.strip() or '%' in value:
            return False
        port = server.get('port', 53)
        if isinstance(port, bool) or not isinstance(port, (int, str)):
            return False
        if not re.fullmatch(r'[0-9]{1,5}', str(port)) or not 1 <= int(port) <= 65535:
            return False
        try:
            address = ipaddress.ip_address(value)
            if address.version == 6 and address.ipv4_mapped is not None:
                return False
            if address.version == 4 and str(address) != value:
                return False
            return not (address.is_unspecified or address.is_multicast or address.is_link_local or
                        str(address) == '255.255.255.255')
        except (ValueError, TypeError, KeyError):
            return False

    @staticmethod
    def client(value):
        """Inbound DNS is IPv4; reject unrestricted ACLs and non-unicast clients."""
        try:
            network = ipaddress.ip_network(value, strict=True)
            return (network.version == 4 and network.prefixlen > 0 and
                    not network.is_multicast and not network.is_unspecified and not network.is_link_local)
        except (ValueError, TypeError):
            return False

    @staticmethod
    def valid(authoritative, forwarded):
        DnsTopology(authoritative, forwarded)
        return True

    @staticmethod
    def delegations(parent, authoritative, forwarded):
        return DnsTopology(authoritative, forwarded).delegations(parent)

    @staticmethod
    def identity(front, back, key, algorithm, secret):
        """Prevent BIND interpolation/injection and accidental weaker TSIG."""
        if not all(isinstance(value, str) for value in (front, back, key, algorithm, secret)):
            return False
        try:
            addresses = [ipaddress.IPv4Address(front), ipaddress.IPv4Address(back)]
            if front == back or any(x.is_loopback or x.is_unspecified or x.is_multicast or x.is_link_local for x in addresses):
                return False
            decoded = base64.b64decode(secret, validate=True)
        except (ValueError, binascii.Error):
            return False
        return bool(re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}', key)
                    and algorithm == 'hmac-sha256' and len(decoded) >= 32)
