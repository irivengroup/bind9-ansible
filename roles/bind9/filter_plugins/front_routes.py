"""Plan additive NetworkManager routes on the controller, never on the target.

Native ip/nmcli commands provide observations. Ambiguous or conflicting state
fails before mutation. No default route or existing profile entry is replaced.
"""
import ipaddress
import json
import re
import uuid


class FrontRoutePlanner:
    """Validate a main-table IPv4 topology and expose a serializable plan."""

    @staticmethod
    def contains_network(container, network):
        """Use public range membership available before Python 3.7 subnet_of."""
        return network.network_address in container and network.broadcast_address in container

    @staticmethod
    def ssh_ipv4_guard(address):
        """Protect the real SSH peer, including IPv4 mapped IPv6 socket addresses."""
        if not address:
            return []  # Only local connection tests omit a real SSH transport.
        parsed = ipaddress.ip_address(address)
        if parsed.version == 4:
            return [str(parsed)]
        return [str(parsed.ipv4_mapped)] if parsed.ipv4_mapped else []

    @staticmethod
    def runtime_network(row):
        """Normalize ip's host routes (which omit /32) without changing snapshots."""
        value = row.get('dst', 'default')
        return str(ipaddress.IPv4Network('0.0.0.0/0' if value == 'default' else value, strict=False))

    @staticmethod
    def json_list(text, label):
        """Decode a successful native ip command; reject empty/malformed output."""
        try:
            value = json.loads(text)
        except (TypeError, ValueError) as error:
            raise ValueError('Invalid JSON from ' + label) from error
        if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
            raise ValueError(label + ' must return an array of objects')
        return value

    @classmethod
    def interface(cls, addresses, front, back, adm):
        """Identify one UP FRONT interface, distinct from BACK and ADM."""
        addresses = cls.json_list(addresses, 'ip address')
        front, back, adm = (ipaddress.IPv4Address(x) for x in (front, back, adm))
        found = []
        present = set()
        protected = []
        for device in addresses:
            networks = [ipaddress.IPv4Interface(x['local'] + '/' + str(x['prefixlen']))
                        for x in device.get('addr_info', []) if x.get('family') == 'inet']
            present.update(x.ip for x in networks)
            if any(x.ip == front for x in networks):
                up = device.get('operstate') == 'UP' or (device.get('operstate') == 'UNKNOWN' and 'UP' in device.get('flags', []))
                if not up or 'master' in device:
                    raise ValueError('FRONT must be UP and outside a VRF/bridge master')
                if any(x.ip in (back, adm) for x in networks):
                    raise ValueError('FRONT must be distinct from BACK and ADM')
                found.append({'device': device['ifname'],
                              'address': str(front),
                              'network': str(next(x.network for x in networks if x.ip == front))})
            protected.extend(str(x.network) for x in networks if x.ip in (back, adm))
        if len(found) != 1 or back not in present or adm not in present:
            raise ValueError('FRONT is ambiguous or BACK/ADM addresses are not present')
        found[0]['protected_networks'] = sorted(set(protected))
        return found[0]

    @staticmethod
    def profile_uuid(text):
        """Require one unambiguous active UUID instead of a connection name."""
        text = text.strip()
        try:
            parsed = uuid.UUID(text)
        except (ValueError, AttributeError) as error:
            raise ValueError('FRONT has no unique active NetworkManager profile') from error
        if str(parsed) != text.lower():
            raise ValueError('Invalid NetworkManager UUID')
        return str(parsed)

    @staticmethod
    def profile_routes(text):
        """Parse nmcli route properties in brace or terse canonical CLI format.

        Unrelated route attributes are retained as observations. Unknown syntax
        is rejected, including truncation; an empty property is a valid list.
        """
        text = text.strip()
        if text in ('', '--'):
            return []
        if '{' not in text and '}' not in text:
            routes = []
            for entry in text.split(','):
                parts = entry.strip().split()
                if not parts:
                    raise ValueError('Empty nmcli route entry')
                network = ipaddress.IPv4Network(parts.pop(0), strict=False)
                # A missing nmcli next-hop denotes an on-link route. Integer
                # zero is address metadata; this parser never opens a socket.
                gateway = ipaddress.IPv4Address(0)
                metric = -1
                if parts and '.' in parts[0] and '=' not in parts[0]:
                    gateway = ipaddress.IPv4Address(parts.pop(0))
                if parts and re.fullmatch(r'-?[0-9]+', parts[0]):
                    metric = int(parts.pop(0))
                attributes = {}
                for part in parts:
                    pair = part.split('=', 1)
                    if len(pair) != 2 or not pair[0] or not pair[1] or pair[0] in attributes:
                        raise ValueError('Malformed terse nmcli route attributes')
                    attributes[pair[0]] = pair[1]
                routes.append({'network': str(network), 'gateway': str(gateway),
                               'metric': metric, 'attributes': attributes})
            return routes
        entries = re.findall(r'\{([^{}]+)\}', text)
        if not entries or re.sub(r'\{[^{}]+\}', '', text).strip(' ,;\n'):
            raise ValueError('Unsupported nmcli ipv4.routes format')
        routes = []
        for entry in entries:
            fields = {}
            for part in entry.split(','):
                pair = part.strip().split('=', 1)
                if len(pair) != 2 or pair[0].strip() in fields:
                    raise ValueError('Malformed nmcli route entry')
                fields[pair[0].strip()] = pair[1].strip()
            if 'ip' not in fields or 'nh' not in fields:
                raise ValueError('Incomplete nmcli route entry')
            network = ipaddress.IPv4Network(fields.pop('ip'), strict=False)
            gateway = ipaddress.IPv4Address(fields.pop('nh'))
            metric = int(fields.pop('mt', '-1'))
            routes.append({'network': str(network), 'gateway': str(gateway),
                           'metric': metric, 'attributes': fields})
        return routes

    @classmethod
    def plan(cls, interface, clients, gateway, persistent, runtime, rules,
             route_table, profile, metric, protected_ips):
        """Produce missing persistent/runtime routes and keep protected snapshots."""
        profile = cls.profile_uuid(profile)
        if str(route_table).strip() not in ('0', '254'):
            raise ValueError('FRONT policy routing is unsupported; main table required')
        rules = cls.json_list(rules, 'ip rule')
        if len(rules) != 3 or {(x.get('priority'), str(x.get('table')), x.get('src')) for x in rules} != {
                (0, 'local', 'all'), (32766, 'main', 'all'), (32767, 'default', 'all')}:
            raise ValueError('Custom IPv4 routing rules require a separate routing design')
        if isinstance(metric, bool) or not isinstance(metric, int) or not 1 <= metric <= 4294967295:
            raise ValueError('bind_front_route_metric must be an integer from 1 to 4294967295')
        gateway = ipaddress.IPv4Address(gateway.strip())
        front_network = ipaddress.IPv4Network(interface['network'])
        if gateway not in front_network or gateway in (front_network.network_address, front_network.broadcast_address, ipaddress.IPv4Address(interface['address'])):
            raise ValueError('Configured FRONT gateway is not on-link and usable')
        persistent = cls.profile_routes(persistent)
        runtime = cls.json_list(runtime, 'ip route')
        protected = [ipaddress.IPv4Network(x) for x in interface['protected_networks']]
        protected += [ipaddress.IPv4Network(str(ipaddress.IPv4Address(x)) + '/32') for x in protected_ips]
        wanted = []
        probes = []
        for client in clients:
            network = ipaddress.ip_network(client, strict=True)
            if network.version != 4:
                raise ValueError('FRONT routes support IPv4 only; IPv6 listener is disabled')
            if network.prefixlen == 0 or any(network.overlaps(ipaddress.IPv4Network(x)) for x in
                    ('0.0.0.0/8', '127.0.0.0/8', '169.254.0.0/16', '224.0.0.0/4', '240.0.0.0/4')):
                raise ValueError('Unsafe external client network: ' + str(network))
            if any(network.overlaps(x) for x in protected):
                raise ValueError('Client network overlaps BACK/ADM, a protected peer or the SSH controller: ' + str(network))
            if cls.contains_network(front_network, network):
                for route in runtime:
                    other = ipaddress.IPv4Network(route.get('dst', 'default').replace('default', '0.0.0.0/0'), strict=False)
                    if cls.contains_network(network, other) and (route.get('dev') != interface['device'] or route.get('gateway')):
                        raise ValueError('Conflicting route for connected FRONT clients')
                probe = network.network_address if network.prefixlen == 32 else network.network_address + 1
                probes.append({'address': str(probe), 'network': str(network), 'gateway': None})
                continue  # Connected FRONT clients need no gateway route.
            if network.overlaps(front_network):
                raise ValueError('Client network includes the directly connected FRONT subnet')
            wanted.append(network)
        wanted = list(ipaddress.collapse_addresses(wanted))
        add_profile, add_runtime = [], []
        for network in wanted:
            persisted = []
            active = []
            for route in persistent:
                other = ipaddress.IPv4Network(route['network'])
                if cls.contains_network(network, other):
                    if route['gateway'] != str(gateway) or route['attributes']:
                        raise ValueError('Conflicting/attributed persistent FRONT route: ' + str(other))
                    if other == network:
                        persisted.append(route)
            for route in runtime:
                other = ipaddress.IPv4Network(route.get('dst', 'default').replace('default', '0.0.0.0/0'), strict=False)
                if cls.contains_network(network, other):
                    if (route.get('dev') != interface['device'] or route.get('gateway') != str(gateway)
                            or route.get('type', 'unicast') != 'unicast' or 'nexthops' in route):
                        raise ValueError('Conflicting runtime return route: ' + str(other))
                    if other == network:
                        active.append(route)
            # Keep existing metrics; only new entries receive the configured metric.
            chosen_metric = persisted[0]['metric'] if persisted else metric
            if len(persisted) > 1 or len(active) > 1:
                raise ValueError('Ambiguous duplicate route: ' + str(network))
            if active and not persisted:
                raise ValueError('Unowned runtime-only FRONT route; persist it explicitly before deployment')
            command_route = str(network) + ' ' + str(gateway)
            if chosen_metric >= 0:
                command_route += ' ' + str(chosen_metric)
            if not persisted:
                add_profile.append(command_route)
            if not active:
                add_runtime.append(command_route)
            probe = network.network_address if network.prefixlen == 32 else network.network_address + 1
            probes.append({'address': str(probe), 'network': str(network), 'gateway': str(gateway)})
        return {'device': interface['device'], 'uuid': profile, 'gateway': str(gateway),
                'profile_additions': add_profile, 'runtime_additions': add_runtime,
                'probes': probes, 'runtime_before': runtime, 'profile_before': persistent,
                'protected_ips': protected_ips}

    @staticmethod
    def validated_journal(plan):
        """Reject malformed durable plans before executing recovery commands."""
        if not isinstance(plan, dict) or set(plan) != {'device', 'uuid', 'gateway',
                'profile_additions', 'runtime_additions', 'probes', 'runtime_before',
                'profile_before', 'protected_ips'}:
            raise ValueError('Invalid FRONT recovery journal schema')
        FrontRoutePlanner.profile_uuid(plan['uuid'])
        if not isinstance(plan['device'], str) or not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,15}', plan['device']):
            raise ValueError('Invalid recovery device name')
        ipaddress.IPv4Address(plan['gateway'])
        for field in ('profile_additions', 'runtime_additions', 'probes',
                      'runtime_before', 'profile_before', 'protected_ips'):
            if not isinstance(plan[field], list):
                raise ValueError('Invalid recovery journal list: ' + field)
        for field in ('profile_additions', 'runtime_additions'):
            for value in plan[field]:
                if not isinstance(value, str):
                    raise ValueError('Invalid route recovery entry')
                parts = value.split()
                if len(parts) not in (2, 3) or str(ipaddress.IPv4Network(parts[0])) != parts[0]:
                    raise ValueError('Invalid recovery route syntax')
                network = ipaddress.IPv4Network(parts[0])
                if network.prefixlen == 0 or parts[1] != plan['gateway']:
                    raise ValueError('Unsafe recovery route')
                if len(parts) == 3 and not 1 <= int(parts[2]) <= 4294967295:
                    raise ValueError('Invalid recovery metric')
                if any(ipaddress.IPv4Address(x) in network for x in plan['protected_ips']):
                    raise ValueError('Recovery route overlaps a protected address')
                original = plan['profile_before'] if field == 'profile_additions' else plan['runtime_before']
                if any((x['network'] if 'network' in x else FrontRoutePlanner.runtime_network(x)) == parts[0] for x in original):
                    raise ValueError('Recovery would remove a preexisting route')
        return plan

    @classmethod
    def owned_profile_routes(cls, plan, current):
        """Select only journal additions still present with their exact identity."""
        cls.validated_journal(plan)
        routes = cls.profile_routes(current)
        result = []
        for text in plan['profile_additions']:
            parts = text.split()
            matching = [x for x in routes if x['network'] == parts[0]]
            expected = {'network': parts[0], 'gateway': parts[1],
                        'metric': int(parts[2]) if len(parts) == 3 else -1, 'attributes': {}}
            if matching and matching != [expected]:
                raise ValueError('Owned persistent route identity changed; manual recovery required')
            if matching:
                result.append(text)
        return result

    @classmethod
    def owned_runtime_routes(cls, plan, current):
        """Do not remove a replacement installed by another network operator."""
        cls.validated_journal(plan)
        routes = cls.json_list(current, 'rollback ip route')
        result = []
        for text in plan['runtime_additions']:
            parts = text.split()
            matching = [x for x in routes if cls.runtime_network(x) == parts[0]]
            if matching and (len(matching) != 1 or matching[0].get('dev') != plan['device']
                    or matching[0].get('gateway') != parts[1]
                    or (len(parts) == 3 and matching[0].get('metric') != int(parts[2]))):
                raise ValueError('Owned active route identity changed; manual recovery required')
            if matching:
                result.append(text)
        return result

    @classmethod
    def unchanged_routes(cls, before, after, plan, applied=True):
        """Require every original route intact and no unrelated routes added."""
        before = before if isinstance(before, list) else cls.json_list(before, 'routes before')
        after = cls.json_list(after, 'routes after')
        normalize = lambda rows: {json.dumps(x, sort_keys=True) for x in rows}
        if not normalize(before).issubset(normalize(after)):
            raise ValueError('An existing route changed or disappeared, including default/BACK/ADM')
        additions = normalize(after) - normalize(before)
        permitted = {x.split()[0] for x in plan['runtime_additions']} if applied else set()
        for encoded in additions:
            row = json.loads(encoded)
            if (cls.runtime_network(row) not in permitted or row.get('dev') != plan['device']
                    or row.get('gateway') != plan['gateway']):
                raise ValueError('An unrelated route appeared during the FRONT operation')
        if applied:
            for text in plan['runtime_additions']:
                parts = text.split()
                candidates = [x for x in after if cls.runtime_network(x) == parts[0]
                              and x.get('dev') == plan['device'] and x.get('gateway') == parts[1]]
                if len(candidates) != 1 or (len(parts) == 3 and candidates[0].get('metric') != int(parts[2])):
                    raise ValueError('An intended runtime route is missing or has the wrong metric')
        return True

    @classmethod
    def unchanged_profile(cls, before, after, plan, applied=True):
        """Check profile additions without rewriting or deleting preexisting entries."""
        after = cls.profile_routes(after)
        normalize = lambda rows: {json.dumps(x, sort_keys=True) for x in rows}
        if not normalize(before).issubset(normalize(after)):
            raise ValueError('An existing persistent route was changed or removed')
        expected = []
        for text in plan['profile_additions'] if applied else []:
            parts = text.split()
            expected.append({'network': parts[0], 'gateway': parts[1],
                             'metric': int(parts[2]) if len(parts) == 3 else -1, 'attributes': {}})
        if normalize(after) != normalize(before + expected):
            raise ValueError('Persistent route additions do not match the exact plan')
        return True

    @classmethod
    def verify_lookup(cls, output, device, gateway=None):
        """Require one actual return path; no shell output or helper JSON involved."""
        routes = cls.json_list(output, 'ip route get')
        if len(routes) != 1 or routes[0].get('dev') != device:
            raise ValueError('Return path does not use the required interface')
        if gateway is not None and routes[0].get('gateway') != gateway:
            raise ValueError('Return path does not use the configured FRONT gateway')
        return True


class FilterModule:
    """Expose read-only planning and validation to Ansible 2.9."""

    def filters(self):
        return {'front_interface': FrontRoutePlanner.interface,
                'front_ssh_ipv4_guard': FrontRoutePlanner.ssh_ipv4_guard,
                'front_profile_uuid': FrontRoutePlanner.profile_uuid,
                'front_route_plan': FrontRoutePlanner.plan,
                'front_routes_unchanged': FrontRoutePlanner.unchanged_routes,
                'front_profile_unchanged': FrontRoutePlanner.unchanged_profile,
                'front_route_journal': FrontRoutePlanner.validated_journal,
                'front_owned_profile_routes': FrontRoutePlanner.owned_profile_routes,
                'front_owned_runtime_routes': FrontRoutePlanner.owned_runtime_routes,
                'front_route_lookup': FrontRoutePlanner.verify_lookup}
