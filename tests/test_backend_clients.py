from pathlib import Path

from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]
T = ROOT / 'roles/bind9/templates'
ENV = Environment(loader=FileSystemLoader(str(T)))


def render(name, back, front):
    return ENV.get_template(name).render(
        bind_backend_clients=back,
        bind_frontend_clients=front,
        bind_back_ip='10.0.0.2',
        bind_front_ip='192.0.2.2',
        bind_primary_host='p', bind_secondary_host='s',
        bind_tsig_key_name='site-transfer',
        hostvars={'p': {'bind_back_ip': '10.0.0.1'}, 's': {'bind_back_ip': '10.0.0.2'}},
        bind_authoritative_zones=[], bind_managed_zones=[], bind_forward_zones=[],
    )


def test_no_internal_view_when_empty():
    assert 'view "internal"' not in render('40-views.conf.j2', [], [])
    assert 'backend_clients' not in render('20-acl.conf.j2', [], [])
    assert '20-acl.conf' not in render('named.conf.j2', [], [])


def test_internal_view_and_acl_when_nonempty():
    view = render('40-views.conf.j2', ['10.30.0.0/16'], [])
    assert 'view "internal"' in view
    assert 'view "external"' not in view
    assert 'view "peer"' in view
    assert 'allow-recursion { backend_clients; };' in view
    assert 'include "/etc/named/zones/20-forward-zones.conf";' in view
    assert 'acl "backend_clients"' in render('20-acl.conf.j2', ['10.30.0.0/16'], [])
    assert '10.30.0.0/16;' in render('20-acl.conf.j2', ['10.30.0.0/16'], [])
    assert '20-acl.conf' in render('named.conf.j2', ['10.30.0.0/16'], [])


def test_both_views_independent():
    view = render('40-views.conf.j2', ['10.30.0.0/16'], ['198.51.100.0/24'])
    assert 'view "internal"' in view
    assert 'view "external"' in view
    assert view.index('view "peer"') < view.index('view "external"') < view.index('view "internal"')
