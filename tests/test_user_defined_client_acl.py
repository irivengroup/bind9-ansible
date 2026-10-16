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
        context.update(group_names=[role], bind_cloud_clients=cloud, bind_internal_clients=internal,
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
