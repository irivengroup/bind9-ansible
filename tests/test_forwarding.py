from pathlib import Path
import yaml
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/bind9'


def test_forwarding_defaults_unified():
    defaults = yaml.safe_load((ROLE / 'defaults/main.yml').read_text())
    zones = defaults['bind_forward_zones']
    assert len(zones) == 2
    assert {z['domain'] for z in zones} == {'corp.example.test', 'fake.example.test'}
    assert 'bind_fake_dns_server' not in defaults
    assert 'bind_forwarders' not in defaults


def test_zone_template_forwarding_render():
    template = (ROLE / 'templates/20-forward-zones.conf.j2').read_text()
    defaults = yaml.safe_load((ROLE / 'defaults/main.yml').read_text())
    rendered = Environment(undefined=StrictUndefined).from_string(template).render(
        **defaults, bind_role='primary'
    )
    assert 'zone "fake.example.test" IN {' in rendered
    assert '10.20.0.99 port 53;' in rendered
    assert '10.20.0.53 port 53;' in rendered
    assert rendered.count('type forward;') == 2
    assert 'bind_fake_dns' not in template


def test_validation_per_zone():
    text = (ROLE / 'tasks/validate.yml').read_text()
    assert "subelements('forwarders')" in text
    assert "map(attribute='domain')" in text
