"""Resolve the DNS and SSH roles from the IPv4 interfaces of a site pair."""
import ipaddress
import re


class DnsNetwork:
    """Select exactly one IPv4 per listening interface and compare peer roles."""

    IPV4_ROW = re.compile(
        r'[1-9][0-9]*: ([A-Za-z0-9_.:-]{1,15})(?:@[A-Za-z0-9_.:-]+)? +inet '
        r'([0-9.]+/[0-9]{1,2}) (?:.* )?scope (global|host|link)(?: .*)?', re.ASCII)

    @classmethod
    def ipv4_links(cls, output):
        """Parse native ip -o output, shared by EL7 and newer iproute releases.

        Keep secondary addresses and duplicate records visible to the existing
        ownership checks. A malformed row fails the whole observation.
        """
        if not isinstance(output, str):
            raise ValueError('Expected native IPv4 address text')
        links = {}
        for line in output.splitlines():
            match = cls.IPV4_ROW.fullmatch(line)
            if match is None:
                raise ValueError('Malformed native IPv4 address record')
            device, cidr, scope = match.groups()
            address = ipaddress.IPv4Interface(cidr)
            if str(address) != cidr:
                raise ValueError('Noncanonical native IPv4 address record')
            link = links.setdefault(device, {'ifname': device, 'addr_info': []})
            link['addr_info'].append({'family': 'inet', 'local': str(address.ip),
                                      'prefixlen': address.network.prefixlen, 'scope': scope})
        return list(links.values())

    @staticmethod
    def unicast(value):
        if not isinstance(value, str):
            return False
        try:
            address = ipaddress.IPv4Address(value)
        except ValueError:
            return False
        return (str(address) == value and not address.is_unspecified and
                not address.is_loopback and not address.is_multicast and
                not address.is_link_local and value != '255.255.255.255')

    def __init__(self, links):
        self.devices = {}
        self.owners = {}
        if not isinstance(links, list):
            raise ValueError('ip must report an IPv4 interface list')
        for link in links:
            device = link['ifname']
            if not isinstance(device, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', device):
                raise ValueError('Invalid interface name in IPv4 inventory')
            for entry in link.get('addr_info', []):
                address = entry['local']
                if entry.get('scope') != 'global' or not self.unicast(address):
                    continue
                if address in self.owners:
                    raise ValueError('IPv4 address occurs more than once: ' + address)
                self.owners[address] = device
                self.devices.setdefault(device, []).append(address)
        if not self.devices:
            raise ValueError('No active non-loopback interface with a usable IPv4 address')

    def address_on(self, device, requested):
        addresses = self.devices.get(device, [])
        if requested is None and len(addresses) == 1:
            return addresses[0]
        if requested in addresses:
            return requested
        raise ValueError('Specify one local IPv4 for listening interface ' + device)

    @classmethod
    def declared_listeners(cls, entries):
        """Normalize objects without trusting their names, IPv4s or DNS roles.

        Keep the caller's inventory immutable. A scope labels a DNS role;
        ADMIN remains the independently verified local SSH endpoint.
        """
        selection, scopes = {}, {}
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) - {'name', 'ipaddress', 'scope'}:
                raise ValueError('Listening interface entries allow only name, ipaddress and scope')
            name = entry.get('name')
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', name):
                raise ValueError('Each listening interface entry requires a valid name')
            if name in selection:
                raise ValueError('Declare each listening interface once')
            if 'ipaddress' in entry and not cls.unicast(entry['ipaddress']):
                raise ValueError('Listening ipaddress must be a canonical unicast IPv4')
            if 'scope' in entry:
                scope = entry['scope']
                if not isinstance(scope, str) or scope not in ('front', 'back'):
                    raise ValueError('Listening scope must be front or back; ADMIN is the SSH endpoint')
                if scope in scopes:
                    raise ValueError('Declare each front/back scope once')
                scopes[scope] = name
            selection[name] = entry.get('ipaddress')
        return selection, scopes

    def listeners(self, selection):
        """Resolve the sole supported declaration against observed local IPv4s."""
        if not isinstance(selection, list) or not selection:
            raise ValueError('bind_listen_interfaces must be a nonempty list of interface objects')
        selection, scopes = self.declared_listeners(selection)
        if any(name not in self.devices for name in selection):
            raise ValueError('Each listening interface must have an active local IPv4 address')
        return {device: self.address_on(device, address) for device, address in selection.items()}, scopes

    def resolve(self, selection, admin):
        """Derive DNS roles solely from listener objects; ADMIN comes from SSH."""
        if not self.unicast(admin) or admin not in self.owners:
            raise ValueError('The SSH endpoint must be a local IPv4 (set bind_admin_ip when using a hostname)')
        dns, scopes = self.listeners(selection)
        if len(dns) == 1:
            front = back = next(iter(dns.values()))
        else:
            if set(scopes) != {'front', 'back'}:
                raise ValueError('Declare exactly one front scope and one back scope with multiple listeners')
            front, back = dns[scopes['front']], dns[scopes['back']]
        front_device, back_device, admin_device = (self.owners[address] for address in (front, back, admin))
        return {
            'front': front, 'back': back, 'admin': admin, 'listeners': list(dns.values()),
            'interface_count': len(self.devices), 'listener_count': len(dns),
            'admin_role': ('front' if admin_device == front_device else
                           'back' if admin_device == back_device else
                           'listener' if admin_device in dns else 'dedicated'),
            # The existing return-route transaction requires three distinct
            # paths. Shared SSH/DNS links use the host's existing routing.
            'front_routes': len({front_device, back_device, admin_device}) == 3,
        }

    @staticmethod
    def validate_pair(local, peer):
        signature = ('interface_count', 'listener_count', 'admin_role', 'front_routes')
        return (all(local[key] == peer[key] for key in signature) and
                not (set(local['listeners'] + [local['admin']]) & set(peer['listeners'] + [peer['admin']])))


class FilterModule:
    """Expose the resolver shared by deployment and management."""

    def filters(self):
        return {'dns_network_resolve': self.resolve, 'dns_network_peer_valid': DnsNetwork.validate_pair,
                'dns_ipv4_links': DnsNetwork.ipv4_links}

    @staticmethod
    def resolve(links, selection, admin):
        return DnsNetwork(links).resolve(selection, admin)
