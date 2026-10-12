from pathlib import Path

import pytest
from jinja2 import Environment, StrictUndefined

TEMPLATES = Path(__file__).resolve().parents[1] / 'roles' / 'bind9' / 'templates'


@pytest.mark.parametrize('name,context', [
    ('20-acl.conf.j2', {}),
    ('20-forward-zones.conf.j2', {}),
    ('10-authoritative-zones.conf.j2', {'group_names': ['dns_primary']}),
])
def test_optional_zone_and_client_lists_can_be_undefined(name, context):
    template = Environment(undefined=StrictUndefined).from_string((TEMPLATES / name).read_text())
    rendered = template.render(**context)
    assert isinstance(rendered, str)


def test_empty_zone_records_do_not_require_records_key():
    template = Environment(undefined=StrictUndefined).from_string((TEMPLATES / 'authoritative.zone.j2').read_text())
    zone = {'domain': 'example.org', 'serial': 2026101201}
    peers = {'primary': {'bind_front_ip': '192.0.2.1'}, 'secondary': {'bind_front_ip': '192.0.2.2'}}
    rendered = template.render(auth_zone=zone, hostvars=peers, bind_primary_host='primary', bind_secondary_host='secondary')
    assert 'SOA' in rendered
