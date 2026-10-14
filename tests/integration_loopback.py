"""Validate all inventory renders and exercise native loopback with isolated BIND.

Run on a Linux test controller with Jinja2/PyYAML and BIND binaries. Native zone
files are read from --native-dir, never installed or changed. BIND runs only on
loopback at a temporary high port; the system service is not used.
"""
import argparse
import base64
import configparser
import copy
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import tempfile
import time

from jinja2 import Environment, FileSystemLoader, StrictUndefined
import yaml


class LoopbackIntegration:
    """Keep rendered files, daemon lifetime and DNS requests within one sandbox."""

    def __init__(self, bin_dir, native_dir, named):
        self.root = Path(__file__).resolve().parents[1]
        self.role = self.root / 'roles/bind9'
        self.bin_dir, self.native_dir, self.named = bin_dir, native_dir, named
        self.jinja = Environment(loader=FileSystemLoader(str(self.role / 'templates')), undefined=StrictUndefined)
        spec = importlib.util.spec_from_file_location('reverse_dns', self.role / 'filter_plugins/reverse_dns.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.reverse = module.automatic_reverse_zones
        spec = importlib.util.spec_from_file_location('dns_topology', self.role / 'filter_plugins/dns_topology.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.topology = module.DnsTopology
        self.jinja.filters.update(module.FilterModule().filters())

    @staticmethod
    def read_yaml(path):
        """Read one source inventory file without executing inventory plugins."""
        return yaml.safe_load(path.read_text()) or {}

    def contexts(self):
        """Yield each delivered host with its actual defaults and site data."""
        for environment in ('dev', 'preprod', 'prod'):
            inventory = self.root / 'inventory' / environment
            groups = configparser.ConfigParser(allow_no_value=True, delimiters=('=',))
            groups.read(str(inventory / 'hosts.ini'))
            hosts = {p.stem: self.read_yaml(p) for p in (inventory / 'host_vars').glob('*.yml')}
            for host in groups['dns_deployment_order']:
                context = self.read_yaml(self.role / 'defaults/main.yml')
                context.update(self.read_yaml(inventory / 'group_vars/all.yml'))
                memberships = [name for name in groups.sections() if host in groups[name]]
                for group in memberships:
                    path = inventory / 'group_vars' / (group + '.yml')
                    if path.exists():
                        context.update(self.read_yaml(path))
                context.update(hosts[host])
                context['group_names'] = memberships
                context['hostvars'] = hosts
                site = list(groups[context['bind_site_name']])
                for kind in ('primary', 'secondary'):
                    context['bind_' + kind + '_host'] = next(x for x in site if x in groups[context['bind_' + kind + '_group']])
                context['bind_tsig_secret'] = base64.b64encode(secrets.token_bytes(32)).decode('ascii')
                context['bind_generated_reverse_zones'] = self.reverse(context['bind_authoritative_zones'])
                context['bind_managed_zones'] = context['bind_authoritative_zones'] + context['bind_generated_reverse_zones']
                yield environment + '-' + host, context

    def render(self, folder, context):
        """Rebase only filesystem locations into the isolated test directory."""
        self.topology(context['bind_managed_zones'], context['bind_forward_zones'])
        data = folder / 'named'
        (data / 'zones').mkdir(parents=True)
        (data / 'slaves').mkdir()
        for name in ('named.localhost', 'named.loopback', 'named.empty'):
            shutil.copyfile(str(self.native_dir / name), str(data / name))
        native = folder / 'named.rfc1912.zones'
        shutil.copyfile(str(self.native_dir / native.name), str(native))
        (data / 'empty-root.zone').write_text(self.jinja.get_template('empty-root.zone.j2').render(**context))
        for zone in context['bind_managed_zones']:
            template = 'reverse.zone.j2' if zone.get('reverse') else 'authoritative.zone.j2'
            (data / 'zones' / (zone['domain'] + '.zone')).write_text(self.jinja.get_template(template).render(auth_zone=zone, **context))
        config = self.jinja.get_template('named.conf.j2').render(**context)
        config = config.replace('/var/named', str(data)).replace('/etc/named.rfc1912.zones', str(native))
        path = folder / 'named.conf'
        path.write_text(config)
        result = subprocess.run([str(self.bin_dir / 'named-checkconf'), '-z', str(path)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        if result.returncode:
            raise AssertionError(result.stdout)
        return path

    @staticmethod
    def sandbox_config(path, port):
        """Rebase listener and process files without changing view policies."""
        folder = path.parent
        config = path.read_text().replace('listen-on port 53', 'listen-on port ' + str(port))
        config = config.replace('options {', 'controls { };\noptions {\n pid-file "' + str(folder / 'named.pid') + '";\n session-keyfile "' + str(folder / 'session.key') + '";\n managed-keys-directory "' + str(folder) + '";')
        path.write_text(config)

    def exercise_replication(self, folder, original):
        """Test initial TSIG transfer, signed NOTIFY convergence and forwarding."""
        primary = copy.deepcopy(original)
        primary.update(bind_front_ip='127.0.0.2', bind_back_ip='127.0.0.3',
                       group_names=[primary['bind_primary_group']],
                       bind_frontend_clients=['127.0.0.10'], bind_backend_clients=['127.0.0.11'])
        primary['hostvars'][primary['bind_primary_host']].update(bind_front_ip='127.0.0.2', bind_back_ip='127.0.0.3')
        primary['hostvars'][primary['bind_secondary_host']].update(bind_front_ip='127.0.0.4', bind_back_ip='127.0.0.5')
        # Reserve all ports together so the kernel cannot assign duplicates.
        reservations = []
        for _ in range(3):
            reservation = socket.socket()
            reservation.bind(('127.0.0.1', 0))
            reservations.append(reservation)
        pport, sport, uport = [x.getsockname()[1] for x in reservations]
        for zone in primary['bind_forward_zones']:
            zone['forwarders'] = [{'address': '127.0.0.6', 'port': uport}]
        secondary = copy.deepcopy(primary)
        secondary.update(bind_front_ip='127.0.0.4', bind_back_ip='127.0.0.5',
                         group_names=[primary['bind_secondary_group']])
        pconf = self.render(folder / 'primary', primary)
        sconf = self.render(folder / 'secondary', secondary)
        self.sandbox_config(pconf, pport)
        self.sandbox_config(sconf, sport)
        # Production uses port 53; only fixture peer endpoints use reserved ports.
        pconf.write_text(pconf.read_text().replace('127.0.0.5 key', '127.0.0.5 port ' + str(sport) + ' key'))
        sconf.write_text(sconf.read_text().replace('127.0.0.3 key', '127.0.0.3 port ' + str(pport) + ' key'))
        upstream = folder / 'upstream'
        upstream.mkdir()
        forward_domain = primary['bind_forward_zones'][0]['domain']
        zonefile = upstream / 'forward.zone'
        zonefile.write_text('$TTL 300\n@ IN SOA ns.' + forward_domain + '. hostmaster.' + forward_domain + '. (1 3600 900 1209600 300)\n@ IN NS ns.' + forward_domain + '.\nns IN A 127.0.0.6\nprobe IN A 203.0.113.55\n')
        uconf = upstream / 'named.conf'
        uconf.write_text('options { directory "' + str(upstream) + '"; listen-on port ' + str(uport) + ' { 127.0.0.6; }; listen-on-v6 { none; }; recursion no; dnssec-validation no; pid-file "' + str(upstream / 'named.pid') + '"; }; controls { }; zone "' + forward_domain + '" { type master; file "' + str(zonefile) + '"; };')
        for reservation in reservations:
            reservation.close()
        processes, logs = [], []
        try:
            for config in (uconf, pconf, sconf):
                log = (config.parent / 'named.log').open('w+')
                logs.append(log)
                processes.append(subprocess.Popen([str(self.named), '-g', '-c', str(config), '-n', '1', '-U', '1'], stdout=log, stderr=log))
            zone = primary['bind_authoritative_zones'][0]
            record = next(x for x in zone['records'] if x['type'] == 'A')
            query = record['name'] + '.' + zone['domain']
            def dig(destination, port, source, name, kind='A', transport='+notcp', short=True, timeout=1):
                argv = [str(self.bin_dir / 'dig'), '@' + destination, '-p', str(port), '-b', source,
                        transport, '+time=' + str(timeout), '+tries=1', name, kind]
                if short:
                    argv.append('+short')
                result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                return result.stdout.strip()
            for attempt in range(100):
                if dig('127.0.0.4', sport, '127.0.0.10', query) == record['value']:
                    break
                if any(x.poll() is not None for x in processes):
                    raise AssertionError('Isolated primary/secondary/upstream failed to start')
                time.sleep(0.1)
            else:
                raise AssertionError('Initial signed zone transfer did not converge')
            checks = 1
            for source, destination in (('127.0.0.1', '127.0.0.1'), ('127.0.0.10', '127.0.0.4'), ('127.0.0.11', '127.0.0.5')):
                for transport in ('+notcp', '+tcp'):
                    assert dig(destination, sport, source, query, transport=transport) == record['value']
                    checks += 1
            for source, destination in (('127.0.0.1', '127.0.0.1'), ('127.0.0.10', '127.0.0.2'), ('127.0.0.11', '127.0.0.3')):
                for transport in ('+notcp', '+tcp'):
                    answer = dig(destination, pport, source, 'probe.' + forward_domain, transport=transport)
                    assert answer == '203.0.113.55', (source, destination, forward_domain, answer,
                        dig(destination, pport, source, 'probe.' + forward_domain, short=False),
                        dig('127.0.0.6', uport, '127.0.0.1', 'probe.' + forward_domain, short=False))
                    checks += 1
            for port, front, back in ((pport, '127.0.0.2', '127.0.0.3'), (sport, '127.0.0.4', '127.0.0.5')):
                for source, destination in (('127.0.0.1', '127.0.0.1'), ('127.0.0.10', front), ('127.0.0.11', back)):
                    for transport in ('+notcp', '+tcp'):
                        assert dig(destination, port, source, 'probe.' + forward_domain, transport=transport) == '203.0.113.55'
                        unknown = dig(destination, port, source, 'undeclared.internet.invalid', transport=transport, short=False)
                        assert 'status: NXDOMAIN' in unknown and ' aa' in unknown, unknown
                        checks += 2
            serial = int(zone['serial']) + 1
            record['value'] = '203.0.113.77'
            zone['serial'] = serial
            primaryfile = pconf.parent / 'named/zones' / (zone['domain'] + '.zone')
            primaryfile.write_text(self.jinja.get_template('authoritative.zone.j2').render(auth_zone=zone, **primary))
            processes[1].send_signal(signal.SIGHUP)
            for attempt in range(100):
                if dig('127.0.0.4', sport, '127.0.0.10', query) == record['value']:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError('Signed NOTIFY/update transfer did not converge')
            soa = dig('127.0.0.5', sport, '127.0.0.11', zone['domain'], 'SOA', short=False)
            assert 'status: NOERROR' in soa and ' aa' in soa and (' ' + str(serial) + ' ') in soa
            assert (sconf.parent / 'named/slaves' / (zone['domain'] + '.zone')).exists()
            checks += 2
            # An unavailable forwarder must fail closed, without Internet fallback.
            processes[0].terminate()
            processes[0].wait(timeout=5)
            for destination, port, source in (('127.0.0.1', pport, '127.0.0.1'), ('127.0.0.4', sport, '127.0.0.10')):
                unavailable = dig(destination, port, source, 'unavailable.' + forward_domain,
                                  short=False, timeout=20)
                assert 'status: SERVFAIL' in unavailable, unavailable
                checks += 1
            return checks
        except Exception:
            for log in logs:
                log.flush()
                log.seek(0)
                print(log.read())
            raise
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            for log in logs:
                log.close()

    def exercise(self, folder, context):
        """Query native and managed zones without binding production addresses."""
        context['bind_front_ip'], context['bind_back_ip'] = '127.0.0.2', '127.0.0.3'
        context['group_names'] = [context['bind_primary_group']]
        context['hostvars'][context['bind_primary_host']].update(bind_front_ip='127.0.0.2', bind_back_ip='127.0.0.3')
        context['hostvars'][context['bind_secondary_host']].update(bind_front_ip='127.0.0.4', bind_back_ip='127.0.0.5')
        context['bind_frontend_clients'], context['bind_backend_clients'] = ['127.0.0.10'], ['127.0.0.11']
        for zone in context['bind_forward_zones']:
            zone['forwarders'] = [{'address': '127.0.0.6', 'port': 65534}]
        path = self.render(folder, context)
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        config = path.read_text().replace('listen-on port 53', 'listen-on port ' + str(port))
        # Only sandbox process settings differ; views and ACLs stay unchanged.
        config = config.replace('options {', 'controls { };\noptions {\n pid-file "' + str(folder / 'named.pid') + '";\n session-keyfile "' + str(folder / 'session.key') + '";\n managed-keys-directory "' + str(folder) + '";')
        path.write_text(config)
        with (folder / 'named.log').open('w+') as log:
            process = subprocess.Popen([str(self.named), '-g', '-c', str(path), '-n', '1', '-U', '1'], stdout=log, stderr=log)
            try:
                for attempt in range(50):
                    if process.poll() is not None:
                        log.seek(0)
                        raise AssertionError(log.read())
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=0.1):
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise AssertionError('Isolated BIND listener did not start')
                checks = 0
                for transport in ('+notcp', '+tcp'):
                    for name, kind, expected in (('localhost', 'A', '127.0.0.1'), ('localhost.localdomain', 'A', '127.0.0.1'), ('localhost', 'AAAA', '::1'), ('1.0.0.127.in-addr.arpa', 'PTR', 'localhost.'), (ipaddress.IPv6Address('::1').reverse_pointer, 'PTR', 'localhost.')):
                        command = [str(self.bin_dir / 'dig'), '@127.0.0.1', '-p', str(port), '-b', '127.0.0.1', transport, '+time=2', '+tries=1', '+short', name, kind]
                        result = subprocess.check_output(command, universal_newlines=True).strip()
                        assert result == expected, (name, kind, result)
                        checks += 1
                denied = subprocess.check_output([str(self.bin_dir / 'dig'), '@127.0.0.1', '-p', str(port), '-b', '127.0.0.9', '+time=2', '+tries=1', 'localhost', 'A'], universal_newlines=True)
                assert 'status: REFUSED' in denied
                # Business-zone access remains available through localhost.
                zone = context['bind_authoritative_zones'][0]
                record = next(x for x in zone['records'] if x['type'] == 'A')
                query = record['name'] + '.' + zone['domain']
                answer = subprocess.check_output([str(self.bin_dir / 'dig'), '@127.0.0.1', '-p', str(port), '+short', query, 'A'], universal_newlines=True).strip()
                assert answer == record['value'], (query, answer, record['value'])
                checks += 2
                for source, destination in (('127.0.0.10', '127.0.0.2'), ('127.0.0.11', '127.0.0.3')):
                    for transport in ('+notcp', '+tcp'):
                        answer = subprocess.check_output([str(self.bin_dir / 'dig'), '@' + destination, '-b', source, '-p', str(port), transport, '+short', query, 'A'], universal_newlines=True).strip()
                        assert answer == record['value'], (source, destination, query, answer)
                        checks += 1
                for source, destination in (('127.0.0.9', '127.0.0.2'), ('127.0.0.10', '127.0.0.3'), ('127.0.0.11', '127.0.0.2')):
                    denied = subprocess.check_output([str(self.bin_dir / 'dig'), '@' + destination, '-b', source, '-p', str(port), '+time=2', '+tries=1', query, 'A'], universal_newlines=True)
                    assert 'status: REFUSED' in denied, (source, destination, denied)
                    checks += 1
                keyfile = folder / 'transfer.key'
                keyfile.write_text('key "' + context['bind_tsig_key_name'] + '" { algorithm ' + context['bind_tsig_algorithm'] + '; secret "' + context['bind_tsig_secret'] + '"; };')
                keyfile.chmod(0o600)
                for source, destination, signed, allowed in (
                    ('127.0.0.5', '127.0.0.3', True, True),
                    ('127.0.0.5', '127.0.0.3', False, False),
                    ('127.0.0.10', '127.0.0.2', True, False),
                    ('127.0.0.5', '127.0.0.2', True, False),
                    ('127.0.0.1', '127.0.0.1', True, False),
                ):
                    command = [str(self.bin_dir / 'dig'), '@' + destination, '-b', source, '-p', str(port), '+yaml', '+time=2', '+tries=1', zone['domain'], 'AXFR']
                    if signed:
                        command.extend(['-k', str(keyfile)])
                    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                    transferred = 'status: NOERROR' in result.stdout and result.stdout.count(' IN SOA ') == 2
                    assert transferred == allowed, (source, destination, signed, result.stdout)
                    if not allowed:
                        # An excluded transfer key reaches the deny view, which
                        # has no copy of the zone and may answer NOTAUTH.
                        assert any('status: ' + status in result.stdout for status in ('REFUSED', 'NOTAUTH')), result.stdout
                    checks += 1
                # Only rebase the port for the real diagnostic CLI. DNS status
                # and TSIG still come from the actual dig/BIND processes.
                binaries = folder / 'bin'
                binaries.mkdir()
                wrapper = binaries / 'dig'
                wrapper.write_text('#!/usr/bin/env bash\nexec ' + shlex.quote(str(self.bin_dir / 'dig')) + ' -p ' + str(port) + ' "$@"\n')
                wrapper.chmod(0o755)
                result = subprocess.run(['bash', str(self.role / 'files/bind-configtool.sh'), 'check-front-axfr',
                                         '127.0.0.2', zone['domain'], '127.0.0.10', str(keyfile)],
                                        env=dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ['PATH']),
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                assert result.returncode == 0 and result.stdout.count('DENIED:') == 2, result.stdout
                return checks + 1
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

    def run(self):
        """Check all delivered hosts and prove native input files are untouched."""
        native = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.native_dir.glob('named.*')}
        with tempfile.TemporaryDirectory(prefix='bind9-loopback-') as temp:
            root = Path(temp)
            count = 0
            for name, context in self.contexts():
                self.render(root / name, context)
                count += 1
            _, original = next(self.contexts())
            # Exercise all template branches: primary/secondary, client views,
            # presence/absence of authority and forwarding (32 combinations).
            for role in ('dns_primary', 'dns_secondary'):
                for front, back in ((True, False), (False, True), (True, True), (False, False)):
                    for auth, forward in ((True, True), (True, False), (False, True), (False, False)):
                        context = copy.deepcopy(original)
                        context['group_names'] = [role]
                        if not front:
                            context['bind_frontend_clients'] = []
                        if back:
                            context['bind_backend_clients'] = ['192.0.2.0/24']
                        else:
                            context['bind_backend_clients'] = []
                        if not auth:
                            context['bind_authoritative_zones'] = []
                            context['bind_managed_zones'] = []
                        if not forward:
                            context['bind_forward_zones'] = []
                        self.render(root / ('matrix-' + str(count)), context)
                        count += 1
            _, context = next(self.contexts())
            queries = self.exercise(root / 'live', copy.deepcopy(context))
            queries += self.exercise_replication(root / 'replication', context)
            unrelated = copy.deepcopy(context)
            unrelated['bind_forward_zones'][0]['domain'] = 'remote.other.test'
            queries += self.exercise_replication(root / 'unrelated', unrelated)
        assert native == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.native_dir.glob('named.*')}
        print(json.dumps({'configurations_loaded': count, 'dns_checks': queries, 'native_files_unchanged': True}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, required=True)
    parser.add_argument('--native-dir', type=Path, required=True)
    parser.add_argument('--named', type=Path, required=True)
    args = parser.parse_args()
    LoopbackIntegration(args.bin_dir, args.native_dir, args.named).run()
