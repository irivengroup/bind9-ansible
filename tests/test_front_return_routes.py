"""Guardrails for persistent external-client return routing."""
import importlib.util
from pathlib import Path
import pytest

SCRIPT = Path('roles/bind9/files/front-routes.py')
spec = importlib.util.spec_from_file_location('front_return_routes', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_only_canonical_specific_ipv4_networks():
    assert [str(n) for n in module.targets(['203.0.113.0/24', '2001:db8::/64', '203.0.113.0/24'])] == ['203.0.113.0/24']
    for item in ['0.0.0.0/0', '127.0.0.0/8', '203.0.113.2/24', '224.0.0.0/4']:
        with pytest.raises(ValueError):
            module.targets([item])


def test_manager_is_declarative_and_persistent():
    task = Path('roles/bind9/tasks/tasks.d/front_return_routes.yml').read_text()
    main = Path('roles/bind9/tasks/main.yml').read_text()
    assert '--clients-json' in task and 'bind_frontend_clients | to_json' in task
    assert 'tasks.d/front_return_routes.yml' in main
    assert "'+ipv4.routes'" in SCRIPT.read_text()
    assert "'device', 'reapply'" in SCRIPT.read_text()
