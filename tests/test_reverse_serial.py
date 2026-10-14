"""Exercise the real reverse template against the installed serial reconciler."""
import importlib.util
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROLE = Path(__file__).resolve().parents[1] / 'roles/bind9'
spec = importlib.util.spec_from_file_location('reverse_serial', ROLE / 'files/reverse-serial.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
spec = importlib.util.spec_from_file_location('topology_serial_test', ROLE / 'filter_plugins/dns_topology.py')
topology = importlib.util.module_from_spec(spec)
spec.loader.exec_module(topology)


class TestReverseSerial:
    def render(self, serial=10, value='app.example.test.'):
        env = Environment(loader=FileSystemLoader(str(ROLE / 'templates')), undefined=StrictUndefined)
        env.filters.update(topology.FilterModule().filters())
        zone = {'domain': '2.0.192.in-addr.arpa', 'serial': serial, 'records': [{'name': '10', 'value': value}]}
        return env.get_template('reverse.zone.j2').render(auth_zone=zone, bind_managed_zones=[zone],
                    bind_authoritative_zones=[{'domain': 'example.test'}], bind_forward_zones=[])

    def test_canonical_two_marker_template_is_accepted(self, tmp_path):
        path = tmp_path / 'zone'
        path.write_text(self.render())
        assert path.read_text().startswith('; Bind9-Ansible-MANAGED-ZONE\n' + module.MARKER + '\n')
        assert module.reconcile(path, None) is False  # Previously always raised.

    def test_changed_ptr_advances_serial_and_unchanged_data_retains_it(self, tmp_path):
        path = tmp_path / 'zone'
        path.write_text(self.render(value='updated.example.test.'))
        assert module.reconcile(path, self.render()) is True
        assert '( 11 3600' in path.read_text()
        old = path.read_text()
        path.write_text(self.render(value='updated.example.test.'))
        assert module.reconcile(path, old) is True
        assert path.read_text() == old

    def test_legacy_reverse_header_remains_compatible(self, tmp_path):
        path = tmp_path / 'zone'
        text = self.render().replace('; Bind9-Ansible-MANAGED-ZONE\n', '')
        path.write_text(text)
        assert module.reconcile(path, text) is False

    @pytest.mark.parametrize('previous', [False, True])
    def test_unmanaged_data_is_never_accepted(self, tmp_path, previous):
        path = tmp_path / 'zone'
        path.write_text(self.render() if previous else '; unmanaged\n')
        with pytest.raises(ValueError, match='unmanaged'):
            module.reconcile(path, '; unmanaged\n' if previous else None)

    def test_serial_overflow_fails_before_rewriting_the_file(self, tmp_path):
        path = tmp_path / 'zone'
        text = self.render(value='updated.example.test.')
        path.write_text(text)
        with pytest.raises(ValueError, match='overflow'):
            module.reconcile(path, self.render(serial=4294967295))
        assert path.read_text() == text
