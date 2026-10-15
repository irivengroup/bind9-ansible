#!/usr/bin/env python3
"""Deterministic ip/nmcli contract double for isolated Ansible workflow tests.

This is a test fixture, never installed by the role. Mutations deliberately emit
empty stdout to reproduce the former changed_when/from_json failure condition.
"""
import ipaddress
import json
import os
from pathlib import Path
import sys


class NativeNetworkDouble:
    """Persist native command observations and inject one bounded failure."""

    def __init__(self):
        self.path = Path(os.environ['BIND_NETWORK_DOUBLE_STATE'])
        self.state = json.loads(self.path.read_text())

    def save(self):
        self.path.write_text(json.dumps(self.state))

    def ip(self, args):
        if args[:3] in (['-4', 'route', 'add'], ['-4', 'route', 'del']):
            # Reuse only the fixture's state/failure machinery; the role's
            # actual command is ip, not a NetworkManager device reactivation.
            if args[4] != 'via' or args[6] != 'dev' or args[-2:] != ['table', 'main']:
                raise ValueError('Unexpected ip mutation arguments')
            text = args[3] + ' ' + args[5]
            if 'metric' in args:
                text += ' ' + args[args.index('metric') + 1]
            return self.nmcli(['device', 'modify', args[7],
                              '+ipv4.routes' if args[2] == 'add' else '-ipv4.routes', text])
        if args == ['-j', '-4', 'address', 'show']:
            return self.state['addresses']
        if args == ['-j', '-4', 'rule', 'show']:
            return self.state['rules']
        if args == ['-j', '-4', 'route', 'show', 'table', 'main']:
            return self.state['runtime']
        if args[:4] == ['-j', '-4', 'route', 'get']:
            target = ipaddress.IPv4Address(args[4])
            matches = [x for x in self.state['runtime'] if target in ipaddress.IPv4Network(
                x.get('dst', 'default').replace('default', '0.0.0.0/0'))]
            matches.sort(key=lambda x: ipaddress.IPv4Network(
                x.get('dst', 'default').replace('default', '0.0.0.0/0')).prefixlen, reverse=True)
            result = dict(matches[0])
            if self.state.get('bad_lookup'):
                result['dev'] = 'back0'
            return [result]
        raise ValueError('Unexpected ip contract: ' + repr(args))

    def nmcli(self, args):
        if args[:2] == ['--wait', '10']:
            args = args[2:]
        if args[:2] == ['-g', 'GENERAL.CON-UUID']:
            return self.state['uuid']
        if args[:1] == ['-g'] and args[2:6] == ['connection', 'show', 'uuid', self.state['uuid']]:
            if args[1] == 'ipv4.gateway':
                return self.state['gateway']
            if args[1] == 'ipv4.route-table':
                return '0'
            if args[1] == 'ipv4.routes':
                return ', '.join('{ ip = ' + x['network'] + ', nh = ' + x['gateway'] +
                                 ', mt = ' + str(x['metric']) + ' }' for x in self.state['persistent'])
        persistent = args[:3] == ['connection', 'modify', 'uuid']
        active = args[:2] == ['device', 'modify']
        if not persistent and not active:
            raise ValueError('Unexpected nmcli contract: ' + repr(args))
        action, text = args[-2:]
        if action not in ('+ipv4.routes', '-ipv4.routes'):
            raise ValueError('Non-additive route command')
        self.state['operations'].append({'persistent': persistent, 'action': action, 'route': text})
        if active and action == '+ipv4.routes' and self.state.get('fail_active_once'):
            self.state['fail_active_once'] = False
            self.save()
            print('Injected native NetworkManager failure', file=sys.stderr)
            sys.exit(5)
        network, gateway, metric = text.split()
        if persistent:
            route = {'network': network, 'gateway': gateway, 'metric': int(metric)}
            collection = self.state['persistent']
        else:
            route = {'dst': network, 'gateway': gateway, 'metric': int(metric), 'dev': 'front0'}
            collection = self.state['runtime']
        if action == '+ipv4.routes' and route not in collection:
            collection.append(route)
        if action == '-ipv4.routes':
            collection.remove(route)
        self.save()
        return ''

    def run(self):
        command = Path(sys.argv[0]).name
        value = self.ip(sys.argv[1:]) if command == 'ip' else self.nmcli(sys.argv[1:])
        if isinstance(value, str):
            if value:
                print(value)
        else:
            print(json.dumps(value))


if __name__ == '__main__':
    NativeNetworkDouble().run()
