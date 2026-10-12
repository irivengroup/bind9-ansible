from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'roles' / 'bind9'

def read(path):
    return (ROOT / path).read_text()

def test_filesystem_preflight():
    assert 'path_safety.yml' in read('tasks/main.yml')
    assert 'follow: false' in read('tasks/tasks.d/path_safety.yml')
    assert 'not item.stat.islnk' in read('tasks/tasks.d/path_safety.yml')

def test_do_not_change_vendor_parent_permissions():
    assert 'path: /var/named, owner:' not in read('tasks/tasks.d/directories.yml')

def test_per_file_backups_and_zone_validation():
    for f in ('tasks/tasks.d/configuration.yml', 'tasks/tasks.d/zones.yml'):
        assert 'backup: true' in read(f)
    assert '/usr/sbin/named-checkzone' in read('tasks/tasks.d/verify.yml')

def test_baseline_rechecked():
    assert 'Baseline checksum verification failed' in read('files/bind-backup.sh')

def test_authoritative_primary_readiness():
    assert 'flags:' in read('tasks/tasks.d/primary_readiness.yml')
    assert 'SOA' in read('tasks/tasks.d/primary_readiness.yml')
