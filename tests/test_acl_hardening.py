from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / 'roles/bind9/tasks'


def test_stale_acl_removed_only_when_both_lists_empty():
    content = (TASKS / 'tasks.d/acl_cleanup.yml').read_text()
    assert '/etc/named/20-acl.conf' in content
    assert 'state: absent' in content
    assert 'bind_frontend_clients' in content
    assert 'bind_backend_clients' in content
    assert 'length) == 0' in content
    assert 'tasks.d/acl_cleanup.yml' in (TASKS / 'main.yml').read_text()


def test_acl_values_are_validated_before_rendering():
    content = (TASKS / 'tasks.d/validate.yml').read_text()
    assert 'Validate individual client ACL entries' in content
    assert 'item is string' in content
    assert 'Client ACL entries must be nonempty' in content
