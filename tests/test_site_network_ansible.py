"""Run pair discovery through the real Ansible role and delegated tasks."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

from ansible import release
import pytest
import yaml

from native_ansible_runner import NativeTaskRun
from test_dns_network import links, write_ip_probe


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != 'posix' or shutil.which('ansible-playbook') is None,
                               reason='Requires the installed native Ansible controller')


class SitePlayRun(NativeTaskRun):
    """Keep the native executor visible to the YAML observer on the QA controller."""

    CANONICAL_ROLE_SEARCH = True

    def transport_variables(self):
        values = super().transport_variables()
        values.pop('ansible_host')
        return values


@pytest.mark.parametrize('profile', ['single', 'one-listener', 'split', 'secondary-selection', 'asymmetric'])
def test_site_discovery_validates_both_hosts_before_assigning_peer_addresses(tmp_path, profile):
    settings, observed = {}, {}
    for hostname, suffix, devices in [('primary', '11', ('eth0', 'eth1', 'eth2', 'eth3')),
                                      ('secondary', '12', ('ens0', 'ens1', 'ens2', 'ens3'))]:
        admin = '192.0.2.' + suffix
        rows = links(suffix, devices)
        host = dict(ansible_connection='local', ansible_python_interpreter=sys.executable,
                    ansible_host=admin, bind_environment='test', bind_site_name='dns_lab')
        if profile == 'single' or (profile == 'asymmetric' and hostname == 'secondary'):
            rows = rows[:2]
        elif profile == 'one-listener':
            host['bind_listen_interfaces'] = [devices[1]]
        elif profile == 'secondary-selection':
            admin = '203.0.113.' + suffix
            host.update(ansible_host=admin, bind_front_ip='192.0.2.' + suffix, bind_back_ip='198.51.100.' + suffix)
            if hostname == 'secondary':
                host['bind_listen_interfaces'] = list(devices[:2])
        else:
            host.update(bind_listen_interfaces=[devices[0], devices[1], devices[3]],
                        bind_front_ip='192.0.2.' + suffix, bind_back_ip='198.51.100.' + suffix,
                        bind_admin_ip='203.0.113.' + suffix)
        observed[admin] = rows
        settings[hostname] = host
    inventory = {'all': {'children': {
        'dns_lab': {'hosts': settings},
        'dns_primary': {'hosts': {'primary': {}}},
        'dns_secondary': {'hosts': {'secondary': {}}},
    }}}
    file = tmp_path / 'inventory.yml'
    file.write_text(yaml.safe_dump(inventory))
    write_ip_probe(tmp_path, observed)
    aliases = '198.51.100.' if profile in ('one-listener', 'split', 'secondary-selection') else '192.0.2.'
    listener_count = {'split': 3, 'secondary-selection': 2}.get(profile, 1)
    checks = ['hostvars.secondary.bind_back_ip == "' + aliases + '12"',
              'bind_back_ip == "' + aliases + '11"',
              'bind_dns_listen_addresses | length == ' + str(listener_count),
              '(bind_dns_front_routes_applicable | bool) == ' + ('true' if listener_count > 1 else 'false')]
    playbook = tmp_path / 'discover.yml'
    playbook.write_text(yaml.safe_dump([{
        'hosts': 'primary', 'gather_facts': False,
        'environment': {'BIND_TEST_TARGET': '{{ hostvars[item if item is string and item in hostvars else inventory_hostname].ansible_host }}',
                        'PATH': str(tmp_path) + ':' + os.environ['PATH']},
        'tasks': [{'include_role': {'name': 'bind9', 'tasks_from': 'tasks.d/site_peers.yml'}},
                  {'name': 'Check the observed role state', 'assert': {'that': checks}}],
    }]))
    if release.__version__ == '2.16.19':
        groups = {name: list(group['hosts']) for name, group in inventory['all']['children'].items()}
        run = SitePlayRun('tasks.d/site_peers.yml', {}, inventory_hosts=settings, inventory_groups=groups, check_mode=True)
        run.native_playbook = playbook
        status, result = run.run()
        if profile == 'asymmetric':
            assert status == 2 and result.failed == ['Require equivalent DNS and SSH topology on the primary and secondary']
            assert 'Resolve the DNS service identities used by peer transfers and zone data' not in result.ok
        else:
            assert status == 0 and not result.failed, result.failed
            assert 'Check the observed role state' in result.ok
        return

    environment = dict(os.environ, ANSIBLE_CONFIG=str(ROOT / 'ansible.cfg'), ANSIBLE_ROLES_PATH=str(ROOT / 'roles'))
    result = subprocess.run(['ansible-playbook', '-i', str(file), str(playbook), '--check'],
                            cwd=str(ROOT), env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            universal_newlines=True, timeout=90)
    if profile == 'asymmetric':
        assert result.returncode != 0 and 'must have equivalent IPv4' in result.stdout, result.stdout
        assert 'Check the observed role state' not in result.stdout
    else:
        assert result.returncode == 0, result.stdout
