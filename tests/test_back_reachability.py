from pathlib import Path


def test_primary_listener_checked_from_secondary_not_controller():
    content = Path("roles/bind9/tasks/tasks.d/primary_readiness.yml").read_text()
    assert 'delegate_to: "{{ inventory_hostname }}"' in content
    assert 'hostvars[bind_primary_host].bind_back_ip' in content
    assert 'delegate_to: localhost' not in content
    assert 'local_action:' not in content
