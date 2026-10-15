"""Run real Ansible preflight assertions against every delivered inventory.

All hosts use the local test connection and supplied RHEL facts. This validates
inputs and Ansible expressions, not the distribution or connectivity of targets.
No package, file or network setting is modified.
"""
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile

import yaml


class InventoryPreflight:
    """Use the role's actual filters and assertions with test-only TSIG material."""

    def run(self):
        root = Path(__file__).resolve().parents[1]
        environment = dict(os.environ, ANSIBLE_CONFIG=str(root / 'ansible.cfg'),
                           ANSIBLE_ROLES_PATH=str(root / 'roles'))
        with tempfile.TemporaryDirectory(prefix='bind9-preflight-') as folder:
            path = Path(folder) / 'play.yml'
            play = [{'hosts': 'dns_deployment_order', 'gather_facts': False,
                     'vars': {'ansible_connection': 'local', 'ansible_distribution': 'RedHat',
                              'ansible_distribution_major_version': '8',
                              'bind_tsig_secret': base64.b64encode(secrets.token_bytes(32)).decode('ascii')},
                     'tasks': [
                         {'include_role': {'name': 'bind9', 'tasks_from': 'tasks.d/dnssec_settings.yml'}},
                         {'include_role': {'name': 'bind9', 'tasks_from': 'tasks.d/clients.yml'}},
                         {'include_role': {'name': 'bind9', 'tasks_from': 'tasks.d/site_peers.yml'}},
                         {'set_fact': {'bind_managed_zones': '{{ bind_authoritative_zones + (bind_authoritative_zones | automatic_reverse_zones) }}'}},
                         {'include_role': {'name': 'bind9', 'tasks_from': 'tasks.d/validate.yml'}}]}]
            path.write_text(yaml.safe_dump(play))
            for name in ('dev', 'preprod', 'prod'):
                result = subprocess.run(['ansible-playbook', '-i', str(root / 'inventory' / name / 'hosts.ini'), str(path)],
                                        env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        universal_newlines=True, timeout=120)
                if result.returncode:
                    raise AssertionError(name + '\n' + result.stdout)
                if 'changed=0' not in result.stdout or 'failed=0' not in result.stdout:
                    raise AssertionError(result.stdout)
        print(json.dumps({'inventories': 3, 'hosts': 12, 'real_preflight_assertions': True, 'target_facts_simulated': True}))


if __name__ == '__main__':
    InventoryPreflight().run()
