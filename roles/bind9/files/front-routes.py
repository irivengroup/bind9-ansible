#!/usr/bin/env python3
"""Fail-closed NetworkManager FRONT return-route management (Python >=3.8)."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys

STATE = Path('/var/lib/Bind9-Ansible/front-routes-rollback.json')


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
        raise ValueError('FRONT has no active NetworkManager connection')
    gateway_lines = run('nmcli', '-g', 'IP4.GATEWAY', 'device', 'show', device).splitlines()
    gateway = next((x.strip() for x in gateway_lines if x.strip() and x.strip() != '--'), '')
    if not gateway:
        raise ValueError('FRONT gateway not discovered; configure one explicitly, do not guess')
    gw = ipaddress.IPv4Address(gateway)
    if gw.is_multicast or gw.is_unspecified or gw.is_loopback:
        raise ValueError('Unsafe FRONT gateway')
    # A gateway must be on-link for the selected FRONT interface.
    if not any(gw in ipaddress.ip_interface('%s/%s' % (a['local'], a['prefixlen'])).network
               for e in data if e['ifname'] == device for a in e.get('addr_info', [])
               if a.get('family') == 'inet'):
        raise ValueError('FRONT gateway is not on-link')
    return device, conn, str(gw)


def targets(raw):
    networks = []
    for item in raw:
        net = ipaddress.ip_network(item, strict=True)
        if net.version != 4:
            continue
        if net.prefixlen == 0 or net.is_multicast or net.is_loopback or net.is_link_local or net.is_unspecified:
            raise ValueError('Unsafe FRONT client route: %s' % net)
        networks.append(net)
    return sorted(set(networks), key=lambda n: (int(n.network_address), n.prefixlen))


def conflicts(networks, device):
    """Reject more-specific routes outside FRONT: a covering route cannot override them."""
    routes = json.loads(run('ip', '-j', '-4', 'route', 'show', 'table', 'main'))
    bad = []
    for net in networks:
        for entry in routes:
            dst = entry.get('dst', 'default')
            try:
                existing = ipaddress.ip_network('0.0.0.0/0' if dst == 'default' else dst, strict=False)
            except ValueError:
                continue
            if existing.subnet_of(net) and entry.get('dev') != device:
                bad.append('%s via %s' % (existing, entry.get('dev', 'unknown')))
    return sorted(set(bad))


def save_state(connection, device, old_routes):
    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(STATE), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump({'connection': connection, 'device': device, 'routes': old_routes}, handle)
        handle.flush()
        os.fsync(handle.fileno())


def rollback():
    if not STATE.exists():
        print(json.dumps({'changed': False, 'reason': 'no pending route changes'}))
        return
    state = json.loads(STATE.read_text())
    run('nmcli', 'connection', 'modify', state['connection'], 'ipv4.routes', state['routes'])
    run('nmcli', 'device', 'reapply', state['device'])
    STATE.unlink()
    print(json.dumps({'changed': True, 'rolled_back': True}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--front-ip')
    parser.add_argument('--clients-json')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--rollback', action='store_true')
    mode.add_argument('--commit', action='store_true')
    args = parser.parse_args()
    if args.rollback:
        rollback()
        return
    if args.commit:
        if STATE.exists():
            STATE.unlink()
        print(json.dumps({'changed': False, 'committed': True}))
        return
    if STATE.exists():
        raise ValueError('Pending FRONT route transaction; rollback before retry')
    if not args.front_ip or not args.clients_json:
        parser.error('--apply requires --front-ip and --clients-json')
    ipaddress.IPv4Address(args.front_ip)
    requested = targets(json.loads(args.clients_json))
    if not requested:
        print(json.dumps({'changed': False, 'routes': []}))
        return
    device, connection, gateway = discover(args.front_ip)
    bad = conflicts(requested, device)
    if bad:
        raise ValueError('More-specific routes bypass FRONT: %s' % ', '.join(bad))
    current = run('nmcli', '-g', 'ipv4.routes', 'connection', 'show', connection)
    additions = []
    for net in requested:
        route = run('ip', '-4', 'route', 'get', str(net.network_address), 'from', args.front_ip)
        if ' dev %s ' % device in ' %s ' % route and (' via %s ' % gateway in ' %s ' % route or ' via ' not in route):
            continue
        spec = '%s %s' % (net, gateway)
        if spec not in current:
            additions.append(spec)
    if additions:
        save_state(connection, device, current)
        try:
            for spec in additions:
                run('nmcli', 'connection', 'modify', connection, '+ipv4.routes', spec)
            run('nmcli', 'device', 'reapply', device)
            if conflicts(requested, device):
                raise RuntimeError('Post-apply route conflict')
        except Exception:
            rollback()
            raise
    print(json.dumps({'changed': bool(additions), 'device': device,
                      'connection': connection, 'gateway': gateway, 'routes': additions}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        print('FRONT routing refused: %s' % exc, file=sys.stderr)
        sys.exit(1)
