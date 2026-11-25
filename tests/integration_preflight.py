"""Run real Ansible preflight assertions against every delivered inventory.

All hosts use the local test connection, modeled links and supplied RHEL facts. This validates
inputs and Ansible expressions, not the distribution or connectivity of targets.
No package, file or network setting is modified.
"""
import base64
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile

import yaml


class InventoryPreflight:
    """Use the role's actual filters and assertions with test-only TSIG material."""

    def network_probe(self, folder, root):
        """Model only the read-only link query; reject any other ip invocation."""
        models = {}
        for name in ('dev', 'preprod', 'prod'):
            models[name] = {}
            for path in sorted((root / 'inventory' / name / 'host_vars').glob('*.yml')):
                values = yaml.safe_load(path.read_text())
                devices = values['bind_listen_interfaces']
                assert isinstance(devices, list) and len(devices) == 2
                assert all(set(device) == {'name', 'ipaddress', 'scope'} for device in devices)
                assert len({device['name'] for device in devices}) == 2
                assert {device['scope'] for device in devices} == {'front', 'back'}
                models[name][path.stem] = '\n'.join(
                    '%d: %s inet %s/24 scope global %s' % (index, device, address, device)
                    for index, (device, address) in enumerate([
                        ('lo', '127.0.0.1'), ('qa-admin', values['ansible_host']),
                        *[(device['name'], device['ipaddress']) for device in devices]], 1)) + '\n'
        model = folder / 'links.json'
        model.write_text(json.dumps(models))
        observations = folder / 'observations.jsonl'
        binary = folder / 'bin'; binary.mkdir()
        probe = binary / 'ip'
        probe.write_text('#!' + sys.executable + '\n' + '''"""Test-only network observation; no network mutation is supported."""
import json
import os
from pathlib import Path
import sys
if sys.argv[1:] != ['-o', '-4', 'address', 'show', 'up']:
    raise SystemExit('Unsupported test network query')
environment = os.environ['BIND_PREFLIGHT_ENV']
host = os.environ['BIND_PREFLIGHT_HOST']
model = json.loads(Path(os.environ['BIND_PREFLIGHT_MODEL']).read_text())
output = model[environment][host]
descriptor = os.open(os.environ['BIND_PREFLIGHT_OBSERVATIONS'], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
try:
    os.write(descriptor, (json.dumps([environment, host]) + '\\n').encode())
finally:
    os.close(descriptor)
print(output, end='')
''')
        probe.chmod(0o700)
        return models, binary, model, observations

    def run(self):
        root = Path(__file__).resolve().parents[1]
        environment = dict(os.environ, ANSIBLE_CONFIG=str(root / 'ansible.cfg'),
                           ANSIBLE_ROLES_PATH=str(root / 'roles'))
        with tempfile.TemporaryDirectory(prefix='bind9-preflight-') as folder:
            folder = Path(folder)
            models, binary, model, observations = self.network_probe(folder, root)
            environment.update(PATH=str(binary) + os.pathsep + environment['PATH'],
                               BIND_PREFLIGHT_MODEL=str(model), BIND_PREFLIGHT_OBSERVATIONS=str(observations),
                               ANSIBLE_REMOTE_TEMP=str(folder / 'remote'))
            path = folder / 'play.yml'
            play = [{'hosts': 'dns_deployment_order', 'gather_facts': False,
                     'environment': {'BIND_PREFLIGHT_HOST': '{{ item | default(inventory_hostname) }}'},
                     'vars': {'ansible_connection': 'local', 'ansible_distribution': 'RedHat',
                              'ansible_remote_tmp': str(folder / 'remote'),
                              'ansible_python_interpreter': sys.executable,
                              'ansible_distribution_major_version': '8',
                              'bind_tsig_secret': base64.b64encode(secrets.token_bytes(32)).decode('ascii')},
                     'tasks': [
                         {'include_role': {'name': 'bind9', 'tasks_from': 'tasks.d/dnssec_settings.yml'}},
                         {'include_role': {'name': 'bind9', 'tasks_from': 'tasks.d/clients.yml'}},
                         {'include_role': {'name': 'bind9', 'tasks_from': 'tasks.d/site_peers.yml'}},
                         {'include_role': {'name': 'bind9', 'tasks_from': 'tasks.d/deploy/resources.yml'}},
                         {'set_fact': {'bind_managed_zones': '{{ _bind_deploy_authoritative_zones + (_bind_deploy_authoritative_zones | automatic_reverse_zones) }}'}},
                         {'include_role': {'name': 'bind9', 'tasks_from': 'tasks.d/deploy/validate.yml'}}]}]
            path.write_text(yaml.safe_dump(play))
            for name in ('dev', 'preprod', 'prod'):
                environment['BIND_PREFLIGHT_ENV'] = name
                result = subprocess.run(['ansible-playbook', '-i', str(root / 'inventory' / name / 'hosts.ini'), str(path)],
                                        env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        universal_newlines=True, timeout=120)
                if result.returncode:
                    raise AssertionError(name + '\n' + result.stdout)
                recaps = re.findall(r'^(\S+)\s+:.*\bchanged=0\s+unreachable=0\s+failed=0\b', result.stdout, re.MULTILINE)
                if len(recaps) != 4 or set(recaps) != set(models[name]):
                    raise AssertionError(result.stdout)
            observed = [json.loads(line) for line in observations.read_text().splitlines()]
            expected = {(name, host) for name, hosts in models.items() for host in hosts}
            assert len(observed) == 24 and {tuple(row) for row in observed} == expected
        print(json.dumps({'inventories': 3, 'hosts': 12, 'real_preflight_assertions': True,
                          'target_facts_simulated': True, 'network_observation_simulated': True,
                          'network_observations': len(observed)}))


if __name__ == '__main__':
    InventoryPreflight().run()
