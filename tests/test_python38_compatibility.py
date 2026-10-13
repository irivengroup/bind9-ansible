"""Guard against syntax unsupported by the project's Python 3.8 floor."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_python_sources_parse_as_python38():
    for directory in ("roles/bind9/files", "roles/bind9/filter_plugins", "tests"):
        for source in (ROOT / directory).rglob("*.py"):
            ast.parse(source.read_text(encoding="utf-8"), filename=str(source), feature_version=(3, 8))


def test_bind_uses_rhel8_compatible_zone_keywords():
    template = (ROOT / "roles/bind9/templates/10-authoritative-zones.conf.j2").read_text(encoding="utf-8")
    assert "type master;" in template
    assert "type slave;" in template
    assert "masters {" in template
    assert "type primary;" not in template
    assert "type secondary;" not in template
    assert "primaries {" not in template
