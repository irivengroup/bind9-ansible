#!/usr/bin/env python3
"""Persist narrowly scoped FRONT return routes using NetworkManager (Python 3.8+)."""
import argparse
import ipaddress
import json
import subprocess
import sys


def run(*args):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError('%s: %s' % (' '.join(args), result.stderr.strip()))
    return result.stdout.strip()


def discover(front_ip):
    data = json.loads(run('ip', '-j', '-4', 'address', 'show'))
    matches = [entry['ifname'] for entry in data for addr in entry.get('addr_info', [])
               if addr.get('family') == 'inet' and addr.get('local') == front_ip]
    if len(matches) != 1:
        raise ValueError('FRONT IPv4 must match exactly one local interface')
    device = matches[0]
    conn = run('nmcli', '-g', 'GENERAL.CONNECTION', 'device', 'show', device)
    if not conn or conn == '--':
        raise ValueError('FRONT interface has no active NetworkManager connection')
    gateway = run('nmcli', '-g', 'IP4.GATEWAY', 'device', 'show', device).splitlines()
    gateway = next((x.strip() for x in gateway if x.strip() and x.strip() != '--'), '')
    if not gateway:
        raise ValueError('FRONT interface has no discoverable IPv4 gateway; refusing to guess')
    gw = ipaddress.IPv4Address(gateway)
    if gw.is_multicast or gw.is_unspecified or gw.is_loopback:
        raise ValueError('Unsafe FRONT gateway')
    return device, conn, str(gw)


def targets(raw):
    networks = []
    for item in raw:
        net = ipaddress.ip_network(item, strict=True)
        if net.version != 4:
            continue  # IPv6 ACLs are retained; IPv6 routing is outside this IPv4 manager
        if net.prefixlen == 0 or net.is_multicast or net.is_loopback or net.is_link_local or net.is_unspecified:
            raise ValueError('Unsafe FRONT client route: %s' % net)
        networks.append(net)
    return sorted(set(networks), key=lambda n: (int(n.network_address), n.prefixlen))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--front-ip', required=True)
    parser.add_argument('--clients-json', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    ipaddress.IPv4Address(args.front_ip)
    requested = targets(json.loads(args.clients_json))
    if not requested:
        print(json.dumps({'changed': False, 'routes': [], 'reason': 'no IPv4 client networks'}))
        return
    device, connection, gateway = discover(args.front_ip)
    current = run('nmcli', '-g', 'ipv4.routes', 'connection', 'show', connection)
    additions = []
    for net in requested:
        # Connected and already correctly routed networks need no persistent override.
        route = run('ip', '-4', 'route', 'get', str(net.network_address), 'from', args.front_ip)
        if ' dev %s ' % device in ' %s ' % route and (' via %s ' % gateway in ' %s ' % route or ' via ' not in route):
            continue
        route_spec = '%s %s' % (net, gateway)
        if route_spec not in current:
            additions.append(route_spec)
    if args.apply and additions:
        try:
            for spec in additions:
                run('nmcli', 'connection', 'modify', connection, '+ipv4.routes', spec)
            run('nmcli', 'device', 'reapply', device)
        except Exception:
            # Restore the prior persistent route list; leave other connection settings untouched.
            subprocess.run(['nmcli', 'connection', 'modify', connection, 'ipv4.routes', current],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(['nmcli', 'device', 'reapply', device], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            raise
    print(json.dumps({'changed': bool(args.apply and additions), 'device': device,
                      'connection': connection, 'gateway': gateway, 'routes': additions}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        print('FRONT routing refused: %s' % exc, file=sys.stderr)
        sys.exit(1)
