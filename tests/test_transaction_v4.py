"""V4 structural regression tests; RHEL 8 integration tests remain mandatory."""
from pathlib import Path
import ast
import subprocess
import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/bind9'


def test_transaction_script_parses():
    ast.parse((ROLE / 'files/named-transaction.py').read_text())


def test_transaction_lifecycle_and_recovery():
    script = (ROLE / 'files/named-transaction.py').read_text()
    for fragment in ('pending.json', 'pending.tar.gz', 'sha256(', 'flock(', 'SNAPSHOT_CREATED', 'RESTORED', 'COMMITTED', 'Symlink refused'):
        assert fragment in script
    assert 'extractall(' not in script


def test_rollback_order():
    tasks = yaml.safe_load((ROLE / 'tasks/main.yml').read_text())
    block = next(task for task in tasks if 'rescue' in task)
    assert 'verify.yml' in str(block['block'])
    assert 'health.yml' in str(block['block'])
    assert 'commit' in str(block['block'])
    assert 'restore' in str(block['rescue'])
    assert 'firewalld.yml' not in str(tasks)


def test_monitoring_timer():
    tasks = yaml.safe_load((ROLE / 'tasks/tasks.d/monitoring.yml').read_text())
    assert any('named-healthcheck.timer' in str(t) for t in tasks)
    subprocess.run(['bash', '-n', str(ROLE / 'files/named-healthcheck.sh')], check=True)


def test_zone_layout_unchanged():
    entrypoint = (ROLE / 'templates/named.conf.j2').read_text()
    assert '/etc/named/40-views.conf' in entrypoint
    assert '/etc/named/zones/' not in entrypoint
    views = (ROLE / 'templates/40-views.conf.j2').read_text()
    assert '/etc/named/zones/10-authoritative-zones.conf' in views
    assert '/etc/named/zones/20-forward-zones.conf' in views
