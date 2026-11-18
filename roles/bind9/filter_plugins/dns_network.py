"""Resolve the DNS and SSH roles from the IPv4 interfaces of a site pair."""
import ipaddress
import re


class DnsNetwork:
    """Select exactly one IPv4 per listening interface and compare peer roles."""

    @staticmethod
    def requires_discovery(front, back, selected_interfaces, explicit_admin):
        """Keep the legacy explicit FRONT/BACK management path compatible."""
        return selected_interfaces or explicit_admin or not front or not back or front == back

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

    def listeners(self, selection, front, back, admin):
        if selection is None:
            if len(self.devices) == 1:
                device = self.owners[admin]
                return {device: admin}
            if front not in self.owners or back not in self.owners:
                raise ValueError('Declare bind_listen_interfaces on a server with multiple IPv4 interfaces')
            names = [self.owners[front], self.owners[back]]
            if names[0] == names[1]:
                raise ValueError('Specify bind_listen_interfaces when FRONT and BACK share an interface')
            return dict(zip(names, (front, back)))

        if isinstance(selection, list):
            if any(not isinstance(name, str) for name in selection) or len(selection) != len(set(selection)):
                raise ValueError('bind_listen_interfaces must name each listening interface once')
            selection = dict.fromkeys(selection)
        if not isinstance(selection, dict) or not selection:
            raise ValueError('bind_listen_interfaces must be a nonempty list or mapping')
        if any(not isinstance(name, str) or name not in self.devices for name in selection):
            raise ValueError('Each listening interface must have an active local IPv4 address')
        return {device: self.address_on(device, address) for device, address in selection.items()}

    def resolve(self, selection, front, back, admin):
        if not self.unicast(admin) or admin not in self.owners:
            raise ValueError('The SSH endpoint must be a local IPv4 (set bind_admin_ip when using a hostname)')
        for address in (front, back):
            if address is not None and (not self.unicast(address) or address not in self.owners):
                raise ValueError('Configured FRONT and BACK addresses must be local IPv4 addresses')
        dns = self.listeners(selection, front, back, admin)
        if len(dns) == 1:
            address = next(iter(dns.values()))
            if any(role is not None and role != address for role in (front, back)):
                raise ValueError('FRONT and BACK must use the sole DNS listener')
            front = back = address
        elif front == back or front not in dns.values() or back not in dns.values():
            raise ValueError('Choose distinct FRONT and BACK IPv4 addresses from the DNS listeners')
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
                'dns_network_required': DnsNetwork.requires_discovery}

    @staticmethod
    def resolve(links, selection, front, back, admin):
        return DnsNetwork(links).resolve(selection, front, back, admin)
