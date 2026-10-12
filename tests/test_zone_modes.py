from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined
import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles' / 'bind9'

def test_three_zone_modes():
    env = Environment(loader=FileSystemLoader(str(ROLE / 'templates')), undefined=StrictUndefined)
    peer = {'p': {'bind_back_ip': '10.1.1.1'}, 's': {'bind_back_ip': '10.1.1.2'}}
    forward = [{'domain': 'corp.example.test', 'forwarders': [{'address': '10.0.0.53'}]}]
    auth = [{'domain': 'cloud.example.test'}]
    for fzones, azones in ((forward, []), ([], auth), (forward, auth), ([], [])):
        conf = env.get_template('20-forward-zones.conf.j2').render(bind_forward_zones=fzones, bind_authoritative_zones=azones)
        assert conf.count('type forward;') == len(fzones)
        assert conf.count('in-view "peer";') == len(azones) + 1
        peer_conf = env.get_template('10-authoritative-zones.conf.j2').render(bind_authoritative_zones=azones, group_names=['dns_primary'], bind_tsig_key_name='transfer', hostvars=peer, bind_secondary_host='s')
        assert peer_conf.count('type primary;') == len(azones) + 1

def test_no_obsolete_client_name():
    for f in (ROLE / 'templates').glob('*.j2'):
        assert 'external_clients' not in f.read_text()
    assert 'bind_external_clients' not in (ROLE / 'defaults/main.yml').read_text()
    validate = (ROLE / 'tasks/tasks.d/validate.yml').read_text()
    assert 'bind_forward_zones | length > 0' not in validate
    assert 'bind_authoritative_zones | length > 0' not in validate


def test_healthcheck_accepts_no_authoritative_zones():
    script = (ROLE / 'files/named-healthcheck').read_text()
    assert '[[ $# -ge 1 ]]' in script
    assert '[[ $# -ge 2 ]]' not in script
