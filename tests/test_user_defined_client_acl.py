"""Protect the ACL rename while retaining the calculated Ansible input contract."""
import copy
from pathlib import Path

import pytest

from integration_loopback import LoopbackIntegration


class TestUserDefinedClientAcl:
    """Render real inventory context and both roles for every client-list shape."""

    @pytest.mark.parametrize('role', ['dns_primary', 'dns_secondary'])
    @pytest.mark.parametrize('cloud,internal', [(['192.0.2.10'], []), ([], ['10.0.0.0/24']),
                                              (['192.0.2.0/24'], ['10.0.0.10']), ([], [])])
    def test_renamed_acl_is_defined_and_consumed_without_undefined_references(self, role, cloud, internal):
        adapter = LoopbackIntegration(Path('.'), Path('.'), Path('.'))
        _, original = next(adapter.contexts())
        context = copy.deepcopy(original)
        context.update(group_names=[role], bind_trusted_networks=dict(cloud_clients=cloud, internal_clients=internal),
                       bind_allowed_clients=list(dict.fromkeys(cloud + internal)))
        output = adapter.jinja.get_template('named.conf.j2').render(**context)
        assert 'acl "allowed_clients"' not in output and '\n    allowed_clients;' not in output
        assert output.count('acl "user_defined_clients"') == (1 if cloud or internal else 0)
        assert output.count('\n    user_defined_clients;') == (2 if cloud or internal else 0)
        for address in cloud + internal:
            assert '    ' + address + ';' in output

    def test_calculated_ansible_variable_remains_the_template_input(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / 'roles/bind9/templates/named.conf.j2').read_text()
        assert '{% set client_networks = bind_allowed_clients %}' in template
        assert 'bind_user_defined_clients' not in template

    @pytest.mark.parametrize('role', ['dns_primary', 'dns_secondary'])
    @pytest.mark.parametrize('field', ['cloud_clients', 'internal_clients'])
    def test_a_single_supplied_list_preserves_acl_and_recursion_destination(self, role, field):
        adapter = LoopbackIntegration(Path('.'), Path('.'), Path('.'))
        _, original = next(adapter.contexts())
        context = copy.deepcopy(original)
        context.update(group_names=[role], bind_trusted_networks={field: ['192.0.2.10']},
                       bind_allowed_clients=['192.0.2.10'])
        output = adapter.jinja.get_template('named.conf.j2').render(**context)
        recursion = output.split('allow-recursion-on {', 1)[1].split('};', 1)[0]
        server = 'primary' if role == 'dns_primary' else 'secondary'
        expected = 'front' if field == 'cloud_clients' else 'back'
        other = 'back' if expected == 'front' else 'front'
        assert f'bind_{server}_{expected}_ip;' in recursion
        assert f'bind_{server}_{other}_ip;' not in recursion
        assert '    192.0.2.10;' in output

    @pytest.mark.parametrize('role', ['dns_primary', 'dns_secondary'])
    def test_an_omitted_mapping_renders_the_same_technical_acls_as_empty_defaults(self, role):
        adapter = LoopbackIntegration(Path('.'), Path('.'), Path('.'))
        _, original = next(adapter.contexts())
        context = copy.deepcopy(original)
        context.update(group_names=[role], bind_allowed_clients=[])
        context.pop('bind_trusted_networks', None)
        template = adapter.jinja.get_template('named.conf.j2')
        omitted = template.render(**context)
        context['bind_trusted_networks'] = {'cloud_clients': [], 'internal_clients': []}
        assert template.render(**context) == omitted
        assert 'acl "bind_servers_back_ip"' in omitted
