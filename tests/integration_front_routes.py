"""Exercise actual Ansible tasks against NetworkManager in an isolated Linux lab.

The caller must provide disposable front0/back0/adm0 profiles, a private system
bus and isolated /var/lib. Never run this against an operational server.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import yaml


class NativeRouteIntegration:
    """Compare real kernel/profile state across apply, replay, recovery and reload."""

    def __init__(self, workarea):
        self.root = Path(__file__).resolve().parents[1]
        self.workarea = workarea
        spec = importlib.util.spec_from_file_location('front_route_planner', self.root / 'roles/bind9/filter_plugins/front_routes.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.planner = module.FrontRoutePlanner
        interpreter = workarea / 'python-test'
        interpreter.symlink_to(sys.executable)
        self.variables = {'ansible_python_interpreter': str(interpreter),
                          'ansible_host': '192.0.2.2', 'ansible_env': {'SSH_CONNECTION': ''},
                          'bind_front_ip': '198.51.100.2', 'bind_back_ip': '10.0.0.2',
                          'bind_primary_host': 'localhost', 'bind_secondary_host': 'secondary',
                          'bind_frontend_clients': ['203.0.113.0/24'], 'bind_front_route_metric': 100}
        inventory = workarea / 'hosts.ini'
        inventory.write_text('[dns_primary]\nlocalhost ansible_connection=local bind_back_ip=10.0.0.2\n'
                             '[dns_secondary]\nsecondary bind_back_ip=10.0.0.3\n')
        self.inventory = inventory
        config = workarea / 'ansible.cfg'
        config.write_text('[defaults]\nretry_files_enabled=False\n')
        self.env = dict(os.environ, ANSIBLE_CONFIG=str(config), ANSIBLE_ROLES_PATH=str(self.root / 'roles'))

    @staticmethod
    def command(*argv):
        """Run a native lab command and expose failures rather than guessing."""
        return subprocess.check_output(list(argv), universal_newlines=True).strip()

    def role(self, tasks='tasks.d/front_return_routes.yml'):
        playbook = self.workarea / 'play.yml'
        playbook.write_text(yaml.safe_dump([{'hosts': 'localhost', 'gather_facts': False,
            'vars': self.variables, 'tasks': [{'include_role': {'name': 'bind9', 'tasks_from': tasks}}]}]))
        result = subprocess.run(['ansible-playbook', '-i', str(self.inventory), str(playbook)],
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=180)
        if result.returncode:
            raise AssertionError(result.stdout)
        return result.stdout

    def routes(self):
        return self.command('ip', '-j', '-4', 'route', 'show', 'table', 'main')

    def profile(self, name):
        return self.command('nmcli', '-g', 'ipv4.addresses,ipv4.gateway,ipv4.routes,ipv4.route-metric', 'connection', 'show', name)

    def run(self):
        """Use real observations, including a pending unrelated DNS profile change."""
        assert os.environ.get('BIND_NM_NETNS') == '1', 'Private namespace launcher required'
        protected = {name: self.profile(name) for name in ('back', 'adm')}
        runtime_before = self.routes()
        profile_before = self.command('nmcli', '-g', 'ipv4.routes', 'connection', 'show', 'front')
        active_dns = self.command('nmcli', '-g', 'IP4.DNS', 'device', 'show', 'front0')
        self.command('nmcli', 'connection', 'modify', 'front', 'ipv4.dns', '198.51.100.53')
        addresses = self.command('ip', '-j', '-4', 'address', 'show')
        interface = self.planner.interface(addresses, '198.51.100.2', '10.0.0.2', '192.0.2.2')
        uuid = self.command('nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', 'front0')
        plan = self.planner.plan(interface, ['203.0.113.0/24'], '198.51.100.1', profile_before,
            runtime_before, self.command('ip', '-j', '-4', 'rule', 'show'), '0', uuid, 100,
            ['192.0.2.2', '10.0.0.2', '10.0.0.3'])
        self.role()
        assert self.planner.unchanged_routes(runtime_before, self.routes(), plan)
        assert active_dns == self.command('nmcli', '-g', 'IP4.DNS', 'device', 'show', 'front0')
        assert protected == {name: self.profile(name) for name in protected}
        assert 'changed=0' in self.role()
        # Recreate the original operation's durable journal to test recovery.
        journal = Path('/var/lib/Bind9-Ansible/front-routes.lock')
        journal.mkdir(mode=0o700)
        file = journal / 'plan.json'
        file.write_text(json.dumps(plan))
        file.chmod(0o600)
        self.role('tasks.d/front_routes_recover.yml')
        assert self.planner.unchanged_routes(runtime_before, self.routes(), plan, applied=False)
        assert profile_before == self.command('nmcli', '-g', 'ipv4.routes', 'connection', 'show', 'front')
        assert not journal.exists()
        self.role()
        # Only in this disposable lab: reload persistent settings and reactivate
        # FRONT to demonstrate that the route survives profile reactivation.
        self.command('nmcli', 'connection', 'reload')
        self.command('nmcli', 'connection', 'down', 'front')
        self.command('nmcli', 'connection', 'up', 'front')
        assert self.planner.unchanged_routes(runtime_before, self.routes(), plan)
        assert protected == {name: self.profile(name) for name in protected}
        result = json.loads(self.command('ip', '-j', '-4', 'route', 'get', '203.0.113.1', 'from', '198.51.100.2'))
        assert result[0]['dev'] == 'front0' and result[0]['gateway'] == '198.51.100.1'
        default = json.loads(self.command('ip', '-j', '-4', 'route', 'get', '8.8.8.8', 'from', '10.0.0.2'))
        assert default[0]['dev'] == 'back0' and default[0]['gateway'] == '10.0.0.1'
        print(json.dumps({'networkmanager': self.command('nmcli', '-g', 'VERSION', 'general'),
            'ansible_workflows': 4, 'idempotence': True, 'selective_recovery': True,
            'profile_reload_persistence': True, 'pending_dns_unapplied': True,
            'original_routes_back_adm_and_default_preserved': True}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workarea', type=Path, required=True)
    NativeRouteIntegration(parser.parse_args().workarea).run()
