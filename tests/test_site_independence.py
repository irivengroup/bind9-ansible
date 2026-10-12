from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]

def test_no_hardcoded_site_names_in_tasks():
    for task in (ROOT / "roles/bind9/tasks").rglob("*.yml"):
        assert "site1" not in task.read_text()
        assert "site3" not in task.read_text()

def test_sites_declare_their_group():
    for site in ("site1", "site3"):
        data = yaml.safe_load((ROOT / f"inventory/group_vars/{site}.yml").read_text())
        assert data["bind_site_name"] == site

def test_peer_resolution_uses_declared_group():
    text = (ROOT / "roles/bind9/tasks/tasks.d/site_peers.yml").read_text()
    assert "bind_site_name in group_names" in text
    assert "groups[bind_site_name]" in text
