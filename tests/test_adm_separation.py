from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]

def test_adm_address_only_in_host_vars():
    all_vars = yaml.safe_load((ROOT / 'inventory/group_vars/all.yml').read_text()) or {}
    assert 'ansible_host' not in all_vars
    for f in (ROOT / 'inventory/host_vars').glob('*.yml'):
        values = yaml.safe_load(f.read_text())
        assert set(values) == {'ansible_host', 'bind_front_ip', 'bind_back_ip'}
        assert len({values['ansible_host'], values['bind_back_ip'], values['bind_front_ip']}) == 3

def test_bind_templates_never_use_management_address():
    templates = ROOT / 'roles/bind9/templates'
    for f in templates.glob('*.j2'):
        assert 'ansible_host' not in f.read_text(), f
