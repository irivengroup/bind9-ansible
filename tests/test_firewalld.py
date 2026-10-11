from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/bind9'

def test_firewall_tasks_parse():
    tasks = yaml.safe_load((ROLE / 'tasks/firewalld.yml').read_text())
    assert len(tasks) >= 9
    assert all('name' in task for task in tasks)

def test_rule_priorities_and_interfaces():
    data = (ROLE / 'tasks/firewalld.yml').read_text()
    assert 'priority="-200"' in data
    assert 'priority="-100"' in data
    assert 'bind_front_interface' in data and 'bind_back_interface' in data
    assert 'destination address=' in data
    assert 'bind_firewalld_secondary_back_ip' in data
    assert 'bind_firewalld_primary_back_ip' in data

def test_integration():
    assert 'import_tasks: firewalld.yml' in (ROLE / 'tasks/main.yml').read_text()
    assert 'ansible.posix' in (ROOT / 'requirements.yml').read_text()
