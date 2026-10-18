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
import re
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
        self.control_ports = {}

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
        (data / 'data').mkdir()
        (data / 'dynamic').mkdir()
        (folder / 'logs').mkdir(mode=0o750)
        for name in ('named.localhost', 'named.loopback', 'named.empty'):
            shutil.copyfile(str(self.native_dir / name), str(data / name))
        native = folder / 'named.rfc1912.zones'
        shutil.copyfile(str(self.native_dir / native.name), str(native))
        (data / 'empty-root.zone').write_text(self.jinja.get_template('empty-root.zone.j2').render(**context))
        for zone in context['bind_managed_zones']:
            template = 'reverse.zone.j2' if zone.get('reverse') else 'authoritative.zone.j2'
            (data / 'zones' / (zone['domain'] + '.zone')).write_text(self.jinja.get_template(template).render(auth_zone=zone, **context))
        context['bind_allowed_clients'] = list(dict.fromkeys(context.get('bind_cloud_clients', []) + context.get('bind_internal_clients', [])))
        config = self.jinja.get_template('named.conf.j2').render(**context)
        config = config.replace('/var/named', str(data)).replace('/etc/named.rfc1912.zones', str(native))
        config = config.replace('/etc/crypto-policies/back-ends/bind.config', str(self.native_dir / 'bind.config'))
        config = config.replace('/etc/named.root.key', str(self.native_dir / 'named.root.key'))
        rndc_key = folder / 'rndc.key'
        rndc_key.write_text('key "rndc-key" { algorithm hmac-sha256; secret "' +
                            base64.b64encode(secrets.token_bytes(32)).decode('ascii') + '"; };\n')
        rndc_key.chmod(0o600)
        config = config.replace('/etc/rndc.key', str(rndc_key))
        config = config.replace('/var/log/named', str(folder / 'logs'))
        path = folder / 'named.conf'
        path.write_text(config)
        result = subprocess.run([str(self.bin_dir / 'named-checkconf'), '-z', str(path)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        if result.returncode:
            raise AssertionError(result.stdout)
        return path

    def sandbox_config(self, path, port):
        """Rebase listener and process files without changing access policies."""
        folder = path.parent
        config = path.read_text().replace('listen-on port 53', 'listen-on port ' + str(port))
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            self.control_ports[path] = reservation.getsockname()[1]
        config = config.replace('inet 127.0.0.1 port 953', 'inet 127.0.0.1 port ' + str(self.control_ports[path]))
        config = config.replace('/run/named/named.pid', str(folder / 'named.pid'))
        config = config.replace('/run/named/session.key', str(folder / 'session.key'))
        path.write_text(config)

    def private_exceptions(self, config):
        """Apply the production helper through a real authenticated RNDC channel."""
        for _ in range(100):
            ready = subprocess.run([str(self.bin_dir / 'rndc'), '-s', '127.0.0.1',
                                    '-p', str(self.control_ports[config]), '-k', str(config.parent / 'rndc.key'),
                                    'status'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if ready.returncode == 0:
                break
            time.sleep(0.1)
        result = subprocess.run([os.sys.executable, str(self.role / 'files/bind-private-dnssec.py'),
                                 '--config', str(config), '--state', str(config.parent / 'state/owned.json'),
                                 '--rndc', str(self.bin_dir / 'rndc'), '--key', str(config.parent / 'rndc.key'),
                                 '--port', str(self.control_ports[config])],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        assert result.returncode == 0, result.stdout
        return json.loads(result.stdout)

    def ansible_loopback_health(self, folder, binaries, context):
        """Run the delivered task unchanged; the dig wrapper rebases only its port.

        Direct DNS queries alone missed Jinja's interpretation of the task's
        regular expression. Exercise the actual runner in both typing modes.
        """
        task = yaml.safe_load((self.role / 'tasks/tasks.d/deploy/health.yml').read_text())[0]
        readiness = yaml.safe_load((self.role / 'tasks/tasks.d/deploy/primary_readiness.yml').read_text())[1]
        verification = yaml.safe_load((self.role / 'tasks/tasks.d/deploy/verify.yml').read_text())
        # Rebase only tool/file locations. Preserve deployed verification logic.
        for check in verification:
            if 'ansible.builtin.command' in check:
                argv = check['ansible.builtin.command']['argv']
                argv[0] = str(self.bin_dir / Path(argv[0]).name)
                check['ansible.builtin.command']['argv'] = [
                    value.replace('/etc/named.conf', str(folder / 'named.conf'))
                         .replace('/var/named', str(folder / 'named')) for value in argv]
        unsafe = folder / 'unsafe-transfer.conf'
        unsafe.write_text('options { allow-transfer { any; }; }; controls {};\n')
        formatted = subprocess.check_output([str(self.bin_dir / 'named-checkconf'), '-p', str(unsafe)],
                                            universal_newlines=True)
        transfer_assertion = next(t for t in verification if 'ansible.builtin.assert' in t)
        # Ansible 2.9 module shebangs need a path without spaces in this lab.
        interpreter = folder / 'python-controller'
        interpreter.symlink_to(os.sys.executable)
        play = [{'hosts': 'localhost', 'connection': 'local', 'gather_facts': False,
                 'vars': {'ansible_python_interpreter': str(interpreter),
                          'bind_back_ip': '127.0.0.3', 'bind_primary_host': 'localhost',
                          'bind_primary_group': 'dns_primary',
                          'bind_managed_zones': context['bind_managed_zones']},
                 'tasks': verification + [{'set_fact': {'bind_back_ip': '127.0.0.3'}}, task, {'assert': {'that': [
                     'bind_loopback_check.results | length == 4',
                     'not bind_loopback_check.changed',
                     'bind_loopback_check.results | map(attribute="attempts") | list == [1, 1, 1, 1]',
                     'bind_loopback_check.results | map(attribute="rc") | list == [0, 0, 0, 0]']}},
                           readiness, {'assert': {'that': [
                               'not bind_primary_soa.changed',
                               'bind_primary_soa.results | length == bind_managed_zones | length',
                               'bind_primary_soa.results | selectattr("failed", "equalto", true) | list | length == 0']}},
                           {'set_fact': {'bind_checked_configuration': {'stdout': formatted}, 'unsafe_rejected': False},
                            'no_log': True},
                           {'block': [transfer_assertion], 'rescue': [{'set_fact': {'unsafe_rejected': True}}]},
                           {'assert': {'that': ['unsafe_rejected']}}]}]
        path = folder / 'ansible-health.yml'
        path.write_text(yaml.safe_dump(play, sort_keys=False))
        inventory = folder / 'health-hosts.ini'
        inventory.write_text('[dns_primary]\nlocalhost ansible_connection=local\n')
        for native in ('false', 'true'):
            environment = dict(os.environ, ANSIBLE_CONFIG=str(self.root / 'ansible.cfg'),
                               ANSIBLE_FILTER_PLUGINS=str(self.role / 'filter_plugins'),
                               ANSIBLE_JINJA2_NATIVE=native,
                               PATH=str(binaries) + os.pathsep + os.environ['PATH'])
            result = subprocess.run(['ansible-playbook', '-i', str(inventory), str(path)],
                                    env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    universal_newlines=True, timeout=90)
            assert result.returncode == 0, result.stdout
            (folder / ('ansible-health-native-' + native + '.log')).write_text(result.stdout)
        return 2 * (4 + len(context['bind_managed_zones']))

    def signed_upstream(self, folder):
        """Create a trusted signed fixture and corrupt one RRset after signing.

        Its explicit test trust anchor makes genuine DNSSEC checks independent
        of external root access and RFC5011 bootstrap. Private keys stay in the
        temporary directory and are never included in a delivery.
        """
        folder.mkdir(parents=True, mode=0o700)
        domain = 'signed-validation.example.com'
        keys = []
        for extra in ([], ['-f', 'KSK']):
            result = subprocess.run([str(self.bin_dir / 'dnssec-keygen'), '-q', '-a', 'ECDSAP256SHA256',
                                     '-n', 'ZONE'] + extra + [domain], cwd=str(folder),
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            assert result.returncode == 0, result.stdout
            keys.append(folder / (result.stdout.strip() + '.key'))
        unsigned = folder / 'secure.zone'
        unsigned.write_text('$TTL 300\n@ IN SOA ns.' + domain + '. hostmaster.' + domain +
                            '. (1 3600 900 1209600 300)\n@ IN NS ns.' + domain +
                            '.\nns IN A 127.0.0.6\ngood IN A 203.0.113.60\nbogus IN A 203.0.113.61\n')
        result = subprocess.run([str(self.bin_dir / 'dnssec-signzone'), '-S', '-n', '1', '-o', domain,
                                 str(unsigned)], cwd=str(folder), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, universal_newlines=True)
        assert result.returncode == 0, result.stdout
        signed = folder / 'secure.zone.signed'
        signed.write_text(signed.read_text().replace('203.0.113.61', '203.0.113.62'))
        public = keys[1].read_text().split('DNSKEY', 1)[1].split(';', 1)[0].replace('(', ' ').replace(')', ' ').split()
        assert public[:3] == ['257', '3', '13']
        anchor = 'trusted-keys { "' + domain + '" 257 3 13 "' + ''.join(public[3:]) + '"; };\n'
        return domain, signed, anchor

    def exercise_replication(self, folder, original, validation='auto'):
        """Test initial TSIG transfer, signed NOTIFY convergence and forwarding."""
        primary = copy.deepcopy(original)
        primary.update(bind_dnssec_active=validation, bind_front_ip='127.0.0.2', bind_back_ip='127.0.0.3',
                       group_names=[primary['bind_primary_group']],
                       bind_cloud_clients=['127.0.0.10'], bind_internal_clients=['127.0.0.11'])
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
        secure_domain, secure_file, secure_anchor = self.signed_upstream(folder / 'signed')
        primary['bind_forward_zones'].append({'domain': secure_domain, 'dnssec_validate': True,
                                              'forwarders': [{'address': '127.0.0.6', 'port': uport}]})
        secondary = copy.deepcopy(primary)
        secondary.update(bind_front_ip='127.0.0.4', bind_back_ip='127.0.0.5',
                         group_names=[primary['bind_secondary_group']])
        pconf = self.render(folder / 'primary', primary)
        sconf = self.render(folder / 'secondary', secondary)
        for config in (pconf, sconf):
            config.write_text(config.read_text() + '\n' + secure_anchor)
        self.sandbox_config(pconf, pport)
        self.sandbox_config(sconf, sport)
        # Production uses port 53; only fixture peer endpoints use reserved ports.
        pconf.write_text(pconf.read_text().replace('127.0.0.5 key', '127.0.0.5 port ' + str(sport) + ' key'))
        sconf.write_text(sconf.read_text().replace('127.0.0.3 key', '127.0.0.3 port ' + str(pport) + ' key'))
        upstream = folder / 'upstream'
        upstream.mkdir()
        forward_domain = primary['bind_forward_zones'][0]['domain']
        zonefile = upstream / 'forward.zone'
        zonefile.write_text('$TTL 300\n@ IN SOA ns.' + forward_domain + '. hostmaster.' + forward_domain + '. (1 3600 900 1209600 300)\n@ IN NS ns.' + forward_domain + '.\nns IN A 127.0.0.6\nprobe IN A 203.0.113.55\nstrict IN A 203.0.113.56\n')
        uconf = upstream / 'named.conf'
        uconf.write_text('options { directory "' + str(upstream) + '"; listen-on port ' + str(uport) + ' { 127.0.0.6; }; listen-on-v6 { none; }; recursion no; dnssec-validation no; pid-file "' + str(upstream / 'named.pid') + '"; }; controls { }; zone "' + forward_domain + '" { type master; file "' + str(zonefile) + '"; }; zone "' + secure_domain + '" { type master; file "' + str(secure_file) + '"; };')
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
            def dig(destination, port, source, name, kind='A', transport='+notcp', short=True, timeout=1, cd=False):
                argv = [str(self.bin_dir / 'dig'), '@' + destination, '-p', str(port), '-b', source,
                        transport, '+time=' + str(timeout), '+tries=1', name, kind]
                if short:
                    argv.append('+short')
                if cd:
                    argv.append('+cdflag')
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
            if validation is not False:
                good = dig('127.0.0.1', pport, '127.0.0.1', 'good.' + secure_domain, short=False)
                assert 'status: NOERROR' in good and ' ad;' in good and '203.0.113.60' in good, good
                bogus = dig('127.0.0.1', pport, '127.0.0.1', 'bogus.' + secure_domain, short=False)
                assert 'status: SERVFAIL' in bogus, bogus
                assert dig('127.0.0.1', pport, '127.0.0.1', 'bogus.' + secure_domain, cd=True) == '203.0.113.62'
                checks += 3
                for config in (pconf, sconf):
                    assert self.private_exceptions(config)['renewed'] == sorted(zone['domain'] for zone in primary['bind_forward_zones'] if not zone.get('dnssec_validate', True))
                    checks += 1
                # Removal of a private marker drops only the corresponding owned NTA.
                # Unsigned data may legitimately be insecure; strict validation must
                # reject bogus signatures, not every unsigned DNS response.
                saved = pconf.read_text()
                pconf.write_text(saved.replace('// Bind9-Ansible DNSSEC-PRIVATE ' + forward_domain, '// Strict validation requested'))
                assert self.private_exceptions(pconf)['removed'] == [forward_domain]
                checks += 1
                pconf.write_text(saved)
                assert self.private_exceptions(pconf)['renewed'] == [forward_domain]
                assert dig('127.0.0.1', pport, '127.0.0.1', 'strict.' + forward_domain) == '203.0.113.56'
                checks += 2
                for destination, port in (('127.0.0.1', pport), ('127.0.0.1', sport)):
                    good = dig(destination, port, '127.0.0.1', 'good.' + secure_domain, short=False)
                    bogus = dig(destination, port, '127.0.0.1', 'bogus.' + secure_domain, short=False)
                    assert 'status: NOERROR' in good and ' ad;' in good, good
                    assert 'status: SERVFAIL' in bogus, bogus
                    checks += 2
            else:
                for config in (pconf, sconf):
                    assert 'DNSSEC-PRIVATE' not in config.read_text()
                    assert 'managed-keys-directory' not in config.read_text()
                    checks += 1
                for destination, port in (('127.0.0.1', pport), ('127.0.0.1', sport)):
                    good = dig(destination, port, '127.0.0.1', 'good.' + secure_domain, short=False)
                    bogus = dig(destination, port, '127.0.0.1', 'bogus.' + secure_domain, short=False)
                    assert 'status: NOERROR' in good and ' ad;' not in good, good
                    assert 'status: NOERROR' in bogus and '203.0.113.62' in bogus and ' ad;' not in bogus, bogus
                    checks += 2
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
            # Both input lists feed shared source authorization. Without views,
            # activating both destinations also permits cross-interface queries.
            for port, front, back in ((pport, '127.0.0.2', '127.0.0.3'), (sport, '127.0.0.4', '127.0.0.5')):
                for source, destination in (('127.0.0.10', back), ('127.0.0.11', front)):
                    for transport in ('+notcp', '+tcp'):
                        answer = dig(destination, port, source, 'probe.' + forward_domain,
                                     transport=transport, short=False)
                        assert 'status: NOERROR' in answer and '203.0.113.55' in answer, answer
                        checks += 1
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
        context['bind_cloud_clients'], context['bind_internal_clients'] = ['127.0.0.10'], ['127.0.0.11']
        for zone in context['bind_forward_zones']:
            zone['forwarders'] = [{'address': '127.0.0.6', 'port': 65534}]
        path = self.render(folder, context)
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        self.sandbox_config(path, port)
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
                # Omit empty-zones-enable and prove the native automatic zone is
                # still authoritative, rather than accepting NXDOMAIN from ".".
                for transport in ('+notcp', '+tcp'):
                    answer = subprocess.check_output([
                        str(self.bin_dir / 'dig'), '@127.0.0.1', '-p', str(port), transport,
                        '+time=2', '+tries=1', '+noall', '+comments', '+authority',
                        '250.255.255.10.in-addr.arpa', 'PTR'], universal_newlines=True)
                    assert 'status: NXDOMAIN' in answer, answer
                    assert re.search(r';; flags: [^;]*\baa\b', answer), answer
                    assert re.search(r'^10\.in-addr\.arpa\.\s+\d+\s+IN\s+SOA\s', answer, re.M | re.I), answer
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
                # One namespace: declared sources now work on both interfaces.
                for source, destination in (('127.0.0.10', '127.0.0.3'), ('127.0.0.11', '127.0.0.2')):
                    answer = subprocess.check_output([str(self.bin_dir / 'dig'), '@' + destination, '-b', source, '-p', str(port), '+short', query, 'A'], universal_newlines=True).strip()
                    assert answer == record['value'], (source, destination, answer)
                    checks += 1
                for source, destination in (('127.0.0.9', '127.0.0.2'),):
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
                    # allow-transfer matches source/key, not destination IP.
                    ('127.0.0.5', '127.0.0.2', True, True),
                    ('127.0.0.1', '127.0.0.1', True, False),
                ):
                    command = [str(self.bin_dir / 'dig'), '@' + destination, '-b', source, '-p', str(port), '+yaml', '+time=2', '+tries=1', zone['domain'], 'AXFR']
                    if signed:
                        command.extend(['-k', str(keyfile)])
                    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                    transferred = 'status: NOERROR' in result.stdout and result.stdout.count(' IN SOA ') == 2
                    assert transferred == allowed, (source, destination, signed, result.stdout)
                    if not allowed:
                        # Only explicit DNS denial is proof of a refused transfer.
                        assert any('status: ' + status in result.stdout for status in ('REFUSED', 'NOTAUTH')), result.stdout
                    checks += 1
                # Only rebase the port for the real diagnostic CLI. DNS status
                # and TSIG still come from the actual dig/BIND processes.
                binaries = folder / 'bin'
                binaries.mkdir()
                wrapper = binaries / 'dig'
                wrapper.write_text('#!/usr/bin/env bash\nexec ' + shlex.quote(str(self.bin_dir / 'dig')) + ' -p ' + str(port) + ' "$@"\n')
                wrapper.chmod(0o755)
                checks += self.ansible_loopback_health(folder, binaries, context)
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

    def exercise_ipv6_upstream(self,folder,original):
        """Prove IPv6 UDP/TCP egress while both IPv6 DNS listeners stay closed."""
        context=copy.deepcopy(original)
        domain='ipv6-upstream.example.test'
        upstream=folder/'upstream'
        upstream.mkdir(parents=True)
        remote=socket.socket(socket.AF_INET6,socket.SOCK_STREAM)
        remote.bind(('::1',0))
        uport=remote.getsockname()[1]
        local=socket.socket()
        local.bind(('127.0.0.1',0))
        while local.getsockname()[1]==uport:
            local.close()
            local=socket.socket()
            local.bind(('127.0.0.1',0))
        port=local.getsockname()[1]
        context.update(bind_dnssec_active=False,bind_authoritative_zones=[],bind_managed_zones=[],
                       bind_forward_zones=[{'domain':domain,'forwarders':[{'address':'::1','port':uport}]}],
                       bind_front_ip='127.0.0.2',bind_back_ip='127.0.0.3',
                       group_names=[context['bind_primary_group']],bind_cloud_clients=['127.0.0.10'],bind_internal_clients=[])
        context['hostvars'][context['bind_primary_host']].update(bind_front_ip='127.0.0.2',bind_back_ip='127.0.0.3')
        context['hostvars'][context['bind_secondary_host']].update(bind_front_ip='127.0.0.4',bind_back_ip='127.0.0.5')
        config=self.render(folder/'resolver',context)
        self.sandbox_config(config,port)
        zone=upstream/'forward.zone'
        zone.write_text('$TTL 60\n@ IN SOA ns.'+domain+'. hostmaster.'+domain+'. (1 3600 900 1209600 60)\n'
                        '@ IN NS ns.'+domain+'.\nns IN AAAA ::1\nprobe IN A 203.0.113.88\n'
                        'probe IN AAAA 2001:db8::88\nbig IN TXT '+('"'+('x'*200)+'" ')*3+'\n')
        queries=upstream/'queries.log'
        uconf=upstream/'named.conf'
        uconf.write_text('options { directory "'+str(upstream)+'"; listen-on { none; }; listen-on-v6 port '+str(uport)+
                         ' { ::1; }; recursion no; dnssec-validation no; max-udp-size 512; querylog yes; pid-file "'+
                         str(upstream/'named.pid')+'"; session-keyfile "'+str(upstream/'session.key')+'"; }; controls {}; '
                         'logging { channel transport { file "'+str(queries)+'"; severity info; }; category queries { transport; }; }; '
                         'zone "'+domain+'" { type master; file "'+str(zone)+'"; };')
        remote.close()
        local.close()
        processes,logs=[],[]
        try:
            for path in (uconf,config):
                log=(path.parent/'named.log').open('w+')
                logs.append(log)
                foreground='-f' if path==uconf else '-g'
                processes.append(subprocess.Popen([str(self.named),foreground,'-c',str(path),'-n','1','-U','1'],stdout=log,stderr=log))
            def query(name,kind='A',transport='+notcp',destination='127.0.0.1'):
                return subprocess.run([str(self.bin_dir/'dig'),'@'+destination,'-p',str(port),
                                       '+time=2','+tries=1','+short',transport,name,kind],
                                      stdout=subprocess.PIPE,stderr=subprocess.STDOUT,universal_newlines=True)
            for _ in range(50):
                if any(process.poll() is not None for process in processes):
                    raise AssertionError('IPv6 upstream/resolver failed to start')
                answer=query('probe.'+domain)
                if answer.returncode==0 and answer.stdout.strip()=='203.0.113.88':
                    break
                time.sleep(0.1)
            else:
                raise AssertionError('IPv4 client query did not reach the IPv6-only forwarder: '+answer.stdout)
            answer=query('probe.'+domain,'AAAA','+tcp')
            assert answer.returncode==0 and answer.stdout.strip()=='2001:db8::88',answer.stdout
            answer=query('big.'+domain,'TXT')
            assert answer.returncode==0 and answer.stdout.count('x')==600,answer.stdout
            # The large RRset exceeds the upstream's UDP cap, forcing TCP over
            # IPv6. Query log flags distinguish this from client-side TCP only.
            for _ in range(20):
                text=queries.read_text() if queries.exists() else ''
                matches=re.findall(r'query: big\.'+re.escape(domain)+r' IN TXT (\S+)',text,re.I)
                if any('T' in flags for flags in matches):
                    break
                time.sleep(0.1)
            assert any('T' in flags for flags in matches),text
            assert '::1#' in text and re.search(r'query: probe\.'+re.escape(domain)+r' IN A ',text,re.I),text
            for transport in ('+notcp','+tcp'):
                answer=query('probe.'+domain,transport=transport,destination='::1')
                assert answer.returncode!=0,answer.stdout
            return 7
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

    def run(self):
        """Check all delivered hosts and prove native input files are untouched."""
        native = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.native_dir.iterdir() if p.is_file()}
        with tempfile.TemporaryDirectory(prefix='bind9-loopback-') as temp:
            root = Path(temp)
            count = 0
            for name, context in self.contexts():
                self.render(root / name, context)
                count += 1
            _, original = next(self.contexts())
            # Cover both roles, empty/cloud/internal/mixed client lists, and
            # presence/absence of authority/forwarding (32 combinations).
            for validation in (False, True, 'auto'):
                for role in ('dns_primary', 'dns_secondary'):
                    for clients, internal in (([], []), (['192.0.2.10'], []), ([], ['10.30.1.20']), (['192.0.2.0/24'], ['10.30.1.20'])):
                        for auth, forward in ((True, True), (True, False), (False, True), (False, False)):
                            context = copy.deepcopy(original)
                            context['bind_dnssec_active'] = validation
                            context['group_names'] = [role]
                            context['bind_cloud_clients'] = clients
                            context['bind_internal_clients'] = internal
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
            queries += self.exercise_replication(root / 'manual-validation', copy.deepcopy(context), validation=True)
            queries += self.exercise_replication(root / 'validation-disabled', copy.deepcopy(context), validation=False)
            queries += self.exercise_ipv6_upstream(root / 'ipv6-upstream',context)
            count += 1
        assert native == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.native_dir.iterdir() if p.is_file()}
        print(json.dumps({'configurations_loaded': count, 'dns_checks': queries, 'native_files_unchanged': True,
                          'native_automatic_empty_zone_udp_tcp': True,
                          'ipv6_udp_tcp_forwarding':True,'ipv6_dns_listener_absent':True,
                          'ansible_loopback_checks': 8,
                          'ansible_primary_soa_checks': 2 * len(context['bind_managed_zones']),
                          'ansible_deployed_configuration_checked': True,
                          'direct_wildcard_transfer_rejected': True}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, required=True)
    parser.add_argument('--native-dir', type=Path, required=True)
    parser.add_argument('--named', type=Path, required=True)
    args = parser.parse_args()
    LoopbackIntegration(args.bin_dir, args.native_dir, args.named).run()
