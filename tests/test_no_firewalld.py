from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/bind9'

def test_no_firewalld_management():
    assert not (ROLE / 'tasks/tasks.d/firewalld.yml').exists()
    assert not (ROOT / 'tests/test_firewalld.py').exists()
    assert 'firewalld' not in (ROLE / 'tasks/main.yml').read_text().lower()
    assert 'firewalld' not in (ROLE / 'handlers/main.yml').read_text().lower()
    assert not any(k.startswith('bind_firewalld') for k in yaml.safe_load((ROLE / 'defaults/main.yml').read_text()))

def test_ci_paths_exist():
    ci = (ROOT / '.github/workflows/ci.yml').read_text()
    for name in ('named-healthcheck', 'named-transaction', 'bind-migrate.sh'):
        assert (ROLE / 'files' / name).is_file()
        assert name in ci
