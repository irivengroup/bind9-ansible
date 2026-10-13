from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_external_client_validation_iterates_every_entry():
    source = (ROOT / 'roles/bind9/tasks/tasks.d/validate.yml').read_text()
    section = source.split('- name: Validate individual client ACL entries', 1)[1].split('- name: Validate supported platform', 1)[0]
    assert 'loop: "{{ (bind_frontend_clients | default([])) + (bind_backend_clients | default([])) }}"' in section
    assert 'item | valid_client_address' in section


def test_global_recursion_and_transfer_denied_but_external_view_opt_in():
    templates = ROOT / 'roles/bind9/templates'
    options = (templates / '30-options.conf.j2').read_text()
    views = (templates / '40-views.conf.j2').read_text()
    assert 'recursion no;' in options
    assert 'allow-transfer { none; };' in options
    external = views.split('view "external" IN {', 1)[1].split('view "deny"', 1)[0]
    assert 'recursion yes;' in external
    assert '20-forward-zones.conf' in external
