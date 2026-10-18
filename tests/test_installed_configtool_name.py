from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_source_keeps_extension_and_installed_command_does_not():
    assert (ROOT / "roles/bind9/files/bind-configtool.sh").is_file()
    migration = (ROOT / "roles/bind9/tasks/tasks.d/deploy/migration.yml").read_text()
    assert "src: bind-configtool.sh" in migration
    assert "dest: /usr/local/sbin/bind-configtool\n" in migration
    assert "path: /usr/local/sbin/bind-configtool.sh" in migration
    assert "state: absent" in migration
    main = (ROOT / "roles/bind9/tasks/tasks.d/deploy/main.yml").read_text()
    assert "/usr/local/sbin/bind-configtool\n" in main
    assert "/usr/local/sbin/bind-configtool.sh" not in main
