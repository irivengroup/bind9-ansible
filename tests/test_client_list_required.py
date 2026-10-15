"""Exercise separate client inputs and their computed DNS union with real tasks."""
from pathlib import Path

import pytest
import yaml

from route_task_expressions import TaskExpressions


class TestClientList:
    """Keep authorization complete without routing internal destinations via FRONT."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def validate(cls, clients, internal=None):
        tasks = yaml.safe_load((cls.ROOT / 'roles/bind9/tasks/tasks.d/validate.yml').read_text())
        names = {'Validate template input types before rendering',
                 'Require at least one configured DNS client network'}
        scope = dict(bind_cloud_clients=clients, bind_internal_clients=[] if internal is None else internal,
                     bind_tsig_key_name='test-transfer',
                     bind_tsig_algorithm='hmac-sha256', bind_tsig_secret='render-only-' * 4,
                     bind_front_ip='192.0.2.11', bind_back_ip='10.20.1.11')
        runner = TaskExpressions(scope)
        runner.run('clients.yml')
        runner.run('validate.yml', tasks=[t for t in tasks if t['name'] in names])
        return runner.variables

    @pytest.mark.parametrize('clients', [['192.0.2.0/24'], ['10.150.1.23'],
                                        ['192.0.2.0/24', '10.150.1.23']])
    def test_nonempty_flat_ipv4_list_is_accepted(self, clients):
        assert self.validate(clients)['bind_allowed_clients'] == clients
        assert self.validate([], internal=clients)['bind_allowed_clients'] == clients

    def test_union_is_unique_ordered_and_recomputed_from_both_inputs(self):
        result = self.validate(['192.0.2.0/24', '192.0.2.0/24'],
                               internal=['10.30.1.20', '192.0.2.0/24'])
        assert result['bind_allowed_clients'] == ['192.0.2.0/24', '10.30.1.20']
        assert result['bind_cloud_clients'] == ['192.0.2.0/24', '192.0.2.0/24']
        assert result['bind_internal_clients'] == ['10.30.1.20', '192.0.2.0/24']

    def test_empty_union_is_refused_before_any_tag_can_change_the_server(self):
        with pytest.raises(ValueError):
            self.validate([])

    @pytest.mark.parametrize('kind', ['cloud', 'internal'])
    @pytest.mark.parametrize('clients', [None, False, 42, '192.0.2.1', {},
        {'cloud': [{'name': 'aws', 'networks': ['192.0.2.0/24']}]},
        [{'name': 'cloud', 'type': 'external', 'networks': ['192.0.2.0/24']}],
        [['192.0.2.1']], ['2001:db8::1'], ['0.0.0.0/0'], ['192.0.2.1/24'],
        [42], [None], ['192.0.2.1; any;'], ['192.0.2.1\n']])
    def test_malformed_and_unsafe_lists_are_refused(self, clients, kind):
        with pytest.raises(ValueError):
            if kind == 'cloud':
                self.validate(clients)
            else:
                # Bypass the convenience default so explicit null is tested too.
                TaskExpressions({'bind_cloud_clients': ['192.0.2.0/24'],
                                 'bind_internal_clients': clients}).run('clients.yml')

    def test_defaults_expose_two_inputs_and_no_override_for_the_computed_union(self):
        paths = [self.ROOT / 'roles/bind9/defaults/main.yml']
        paths += list((self.ROOT / 'inventory').glob('*/group_vars/site*.yml'))
        for path in paths:
            values = yaml.safe_load(path.read_text())
            client_keys = {key for key in values if key.endswith('_clients')}
            assert 'bind_allowed_clients' not in values, path
            assert client_keys <= {'bind_cloud_clients', 'bind_internal_clients'}, path
            assert isinstance(values['bind_cloud_clients'], list)
        defaults = yaml.safe_load(paths[0].read_text())
        assert defaults['bind_internal_clients'] == []

    def test_union_is_prepared_for_all_role_tags_before_template_validation(self):
        main = yaml.safe_load((self.ROOT / 'roles/bind9/tasks/main.yml').read_text())
        assert main[1]['ansible.builtin.import_tasks'] == 'tasks.d/clients.yml'
        assert main[1]['tags'] == ['always']
