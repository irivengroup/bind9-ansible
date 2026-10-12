from pathlib import Path
import yaml
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]
T = ROOT / 'roles/bind9/templates'
ENV = Environment(loader=FileSystemLoader(str(T)))

def render(template, backend=(), frontend=()):
    return ENV.get_template(template).render(
        bind_backend_clients=list(backend), bind_frontend_clients=list(frontend),
        bind_back_ip='10.0.0.1', bind_front_ip='192.0.2.1',
        bind_primary_host='p', bind_secondary_host='s', bind_tsig_key_name='x',
        hostvars={'p': {'bind_back_ip': '10.0.0.1'}, 's': {'bind_back_ip': '10.0.0.2'}})

def test_default_empty():
    defaults = yaml.safe_load((ROOT/'roles/bind9/defaults/main.yml').read_text())
    inventory = yaml.safe_load((ROOT/'inventory/group_vars/all.yml').read_text())
    assert defaults['bind_backend_clients'] == []
    assert inventory['bind_backend_clients'] == []

def test_empty_backend_omits_acl_view():
    assert 'backend_clients' not in render('20-acl.conf.j2', frontend=['192.0.2.0/24'])
    assert 'view "backend"' not in render('40-views.conf.j2', frontend=['192.0.2.0/24'])
    assert 'view "peer"' in render('40-views.conf.j2')

def test_nonempty_backend_uses_back_interface():
    acl = render('20-acl.conf.j2', backend=['10.30.0.0/16'])
    view = render('40-views.conf.j2', backend=['10.30.0.0/16'])
    assert 'acl "backend_clients"' in acl and '10.30.0.0/16;' in acl
    assert 'view "backend"' in view
    assert 'match-destinations { 10.0.0.1; };' in view
    assert 'view "external"' not in view

def test_no_client_acl_include_when_both_empty():
    assert '/etc/named/20-acl.conf' not in render('named.conf.j2')
    assert '/etc/named/20-acl.conf' in render('named.conf.j2', backend=['10.0.0.0/8'])
