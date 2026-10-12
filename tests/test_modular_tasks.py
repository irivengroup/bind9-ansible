from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "roles/bind9/tasks"


def test_main_imports_all_stages():
    main = (TASKS / "main.yml").read_text()
    for name in ("validate", "packages", "directories", "configuration", "zones", "security", "verify", "service"):
        assert f"import_tasks: tasks.d/{name}.yml" in main
        assert (TASKS / "tasks.d" / f"{name}.yml").is_file()


def test_all_bind_paths_use_named_d():
    role = ROOT / "roles/bind9"
    for path in role.rglob("*.yml"):
        assert "/etc/named/modular" not in path.read_text()
    for path in role.rglob("*.j2"):
        assert "/etc/named/modular" not in path.read_text()
    assert '/etc/named/zones/10-authoritative-zones.conf' in (role / "templates/40-views.conf.j2").read_text()


def test_single_internal_forwarder_and_fake_dns():
    defaults = (ROOT / "roles/bind9/defaults/main.yml").read_text()
    assert "domain: corp.example.test" in defaults
    assert "domain: fake.example.test" in defaults


def test_named_config_is_checked_before_service_activation():
    main = (TASKS / "main.yml").read_text()
    assert main.index("verify.yml") < main.index("service.yml")
