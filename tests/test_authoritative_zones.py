from pathlib import Path
import yaml
from jinja2 import Environment, FileSystemLoader
ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles' / 'bind9'

def test_zone_defaults():
    defaults = yaml.safe_load((ROLE/'defaults/main.yml').read_text())
    assert len(defaults['bind_authoritative_zones']) >= 2
    assert not set(x['domain'] for x in defaults['bind_authoritative_zones']) & set(x['domain'] for x in defaults['bind_forward_zones'])

def test_both_roles_render_all_zones():
    env = Environment(loader=FileSystemLoader(str(ROLE/'templates')))
    zones = yaml.safe_load((ROLE/'defaults/main.yml').read_text())['bind_authoritative_zones']
    for role in ('primary', 'secondary'):
        text = env.get_template('10-authoritative-zones.conf.j2').render(bind_authoritative_zones=zones,bind_role=role,bind_tsig_name='transfer',bind_secondary_back_ip='10.1.1.2',bind_primary_back_ip='10.1.1.1',bind_back_ip='10.1.1.2',bind_forward_zones=[])
        for zone in zones:
            assert f'zone "{zone["domain"]}"' in text
            assert f'{role if role == "primary" else "secondary"};' in text

def test_primary_file_render():
    env = Environment(loader=FileSystemLoader(str(ROLE/'templates')))
    z = yaml.safe_load((ROLE/'defaults/main.yml').read_text())['bind_authoritative_zones'][0]
    text = env.get_template('authoritative.zone.j2').render(auth_zone=z,bind_primary_front_ip='192.0.2.1',bind_secondary_front_ip='192.0.2.2')
    assert ' IN SOA ' in text and 'app IN A ' in text
