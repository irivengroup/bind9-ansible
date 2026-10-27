"""Native NetworkManager/Ansible acceptance in a disposable private namespace."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import yaml


class NativeRouteIntegration:
    """Exercise plan, deferred recovery, commit, replay and actual profile reload."""

    def __init__(self, workarea):
        self.root = Path(__file__).resolve().parents[1]
        self.workarea = workarea
        interpreter = workarea / 'python-test'
        interpreter.symlink_to(sys.executable)
        self.variables = dict(ansible_python_interpreter=str(interpreter), ansible_host='192.0.2.2',
                              bind_front_ip='198.51.100.2', bind_back_ip='10.0.0.2',
                              bind_primary_host='localhost', bind_secondary_host='secondary',
                              bind_cloud_clients=['203.0.113.0/24'], bind_front_route_metric=100)
        self.inventory = workarea / 'hosts.ini'
        self.inventory.write_text('[dns_primary]\nlocalhost ansible_connection=local bind_back_ip=10.0.0.2\n'
                                  '[dns_secondary]\nsecondary bind_back_ip=10.0.0.3\n')
        config = workarea / 'ansible.cfg'
        config.write_text('[defaults]\nretry_files_enabled=False\n')
        self.env = dict(os.environ, ANSIBLE_STDOUT_CALLBACK='json', ANSIBLE_CONFIG=str(config),
                        ANSIBLE_ROLES_PATH=str(self.root / 'roles'))
        self.workflows = 0

    @staticmethod
    def command(*argv):
        return subprocess.check_output(list(argv), universal_newlines=True).strip()

    def role(self, action='apply', token=None, defer=False):
        variables = dict(self.variables, bind_front_requested_action=action, bind_front_routes_defer_commit=defer)
        if token:
            variables['bind_front_transaction_token'] = token
        playbook = self.workarea / 'play.yml'
        playbook.write_text(yaml.safe_dump([{'hosts': 'localhost', 'gather_facts': False,
            'vars': variables, 'tasks': [{'include_role': {'name': 'bind9',
                'tasks_from': 'tasks.d/deploy/front_return_routes.yml'}}]}]))
        result = subprocess.run(['ansible-playbook', '-i', str(self.inventory), str(playbook)],
                                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                universal_newlines=True, timeout=180)
        if result.returncode:
            raise AssertionError(result.stdout)
        self.workflows += 1
        return json.JSONDecoder().raw_decode(result.stdout[result.stdout.index('{'):])[0]

    def routes(self):
        return json.loads(self.command('ip', '-j', '-4', 'route', 'show', 'table', 'main'))

    def profile(self, name):
        return self.command('nmcli', '-g', 'ipv4.addresses,ipv4.gateway,ipv4.routes,ipv4.route-metric',
                            'connection', 'show', name)

    def run(self):
        assert os.environ.get('BIND_NM_NETNS') == '1', 'Disposable private namespace required'
        protected = {name: self.profile(name) for name in ('back', 'adm')}
        before = self.routes()
        assert any(row.get('dst') == 'default' and row.get('dev') == 'back0' for row in before)
        assert not any(row.get('dst') == 'default' and row.get('dev') == 'front0' for row in before)
        profile = self.command('nmcli', '-g', 'ipv4.routes', 'connection', 'show', 'front')
        active_dns = self.command('nmcli', '-g', 'IP4.DNS', 'device', 'show', 'front0')
        self.command('nmcli', 'connection', 'modify', 'front', 'ipv4.dns', '198.51.100.53')
        self.role('plan')
        assert self.routes() == before
        self.role(defer=True)
        journal = Path('/var/lib/Bind9-Ansible/front-routes.pending')
        token = (journal / 'meta').read_text().split('|', 1)[0]
        self.role('verify', token)
        assert all(row in self.routes() for row in before)
        assert self.command('nmcli', '-g', 'IP4.DNS', 'device', 'show', 'front0') == active_dns
        self.role('recover')
        assert self.routes() == before
        assert self.command('nmcli', '-g', 'ipv4.routes', 'connection', 'show', 'front') == profile
        assert not journal.exists()
        self.role()
        assert self.role()['stats']['localhost']['changed'] == 0
        # Automatic per-route/global metrics must use the observed live device,
        # without applying the profile's other pending changes.
        self.command('nmcli', 'connection', 'modify', 'front', 'ipv4.route-metric', '-1')
        self.command('nmcli', 'connection', 'modify', 'front', '+ipv4.routes', '172.21.0.0/24 198.51.100.1')
        self.variables['bind_cloud_clients'] += ['172.21.0.0/24']
        self.role()
        assert self.role()['stats']['localhost']['changed'] == 0
        inherited = next(row for row in self.routes() if row.get('dst') == '172.21.0.0/24')
        assert inherited['metric'] == 500
        assert protected == {name: self.profile(name) for name in protected}
        # Reactivation belongs to this private test only, never the route helper.
        self.command('nmcli', 'connection', 'reload')
        self.command('nmcli', 'connection', 'down', 'front')
        self.command('nmcli', 'connection', 'up', 'front')
        assert self.role()['stats']['localhost']['changed'] == 0
        result = json.loads(self.command('ip', '-j', '-4', 'route', 'get', '203.0.113.1', 'from', '198.51.100.2'))[0]
        assert result['dev'] == 'front0' and result['gateway'] == '198.51.100.1'
        default = json.loads(self.command('ip', '-j', '-4', 'route', 'get', '8.8.8.8', 'from', '10.0.0.2'))[0]
        assert default['dev'] == 'back0' and default['gateway'] == '10.0.0.1'
        # Reproduce an active gateway omitted from the persistent gateway field.
        self.command('nmcli', 'connection', 'modify', 'front', 'ipv4.gateway', '')
        assert self.command('nmcli', '-g', 'IP4.GATEWAY', 'device', 'show', 'front0') == '198.51.100.1'
        self.variables['bind_cloud_clients'] += ['172.20.0.0/24']
        self.role()
        assert self.role()['stats']['localhost']['changed'] == 0
        assert protected == {name: self.profile(name) for name in protected}
        # A never-default FRONT exposes its next-hop only through specific
        # routes after reload; BACK must remain the sole system default.
        self.command('nmcli', 'connection', 'modify', 'front', 'ipv4.never-default', 'yes')
        self.command('nmcli', 'connection', 'down', 'front')
        self.command('nmcli', 'connection', 'up', 'front')
        assert not self.command('nmcli', '-g', 'ipv4.gateway', 'connection', 'show', 'front')
        assert not self.command('nmcli', '-g', 'IP4.GATEWAY', 'device', 'show', 'front0')
        assert not any(row.get('dst') == 'default' and row.get('dev') == 'front0' for row in self.routes())
        before_specific = self.routes()
        before_profile = self.command('nmcli', '-g', 'ipv4.routes', 'connection', 'show', 'front')
        self.variables['bind_cloud_clients'] += ['172.19.0.0/24']
        self.role('plan')
        assert self.routes() == before_specific
        assert self.command('nmcli', '-g', 'ipv4.routes', 'connection', 'show', 'front') == before_profile
        self.role()
        assert self.role()['stats']['localhost']['changed'] == 0
        assert all(row in self.routes() for row in before_specific)
        selected = json.loads(self.command('ip', '-j', '-4', 'route', 'get', '172.19.0.1', 'from', '198.51.100.2'))[0]
        assert selected['dev'] == 'front0' and selected['gateway'] == '198.51.100.1'
        assert protected == {name: self.profile(name) for name in protected}
        assert [row for row in self.routes() if row.get('dst') == 'default'] == [
            row for row in before_specific if row.get('dst') == 'default']
        print(json.dumps({'networkmanager': self.command('nmcli', '-g', 'VERSION', 'general'),
            'ansible_workflows': self.workflows, 'native_plan_apply_recover': True,
            'idempotence_after_profile_reload': True, 'active_gateway_fallback': True, 'automatic_metric_observed': True,
            'front_gateway_without_front_default_route': True,
            'specific_front_route_gateway_without_any_front_default': True,
            'pending_dns_unapplied': True, 'back_adm_and_default_preserved': True}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workarea', type=Path, required=True)
    NativeRouteIntegration(parser.parse_args().workarea).run()
