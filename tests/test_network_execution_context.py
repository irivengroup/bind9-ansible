from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "roles/bind9/tasks/tasks.d"


def test_peer_network_checks_run_on_managed_secondary():
    text = (TASKS / "deploy/primary_readiness.yml").read_text()
    assert text.count('delegate_to: "{{ inventory_hostname }}"') == 2
    assert "delegate_to: localhost" not in text
    assert '      - -b\n      - "{{ bind_back_ip }}"' in text


def test_health_queries_use_managed_host_back_address():
    text = (TASKS / "deploy/health.yml").read_text()
    assert '"{{ bind_back_ip }}"' in text
    assert '"@{{ bind_back_ip }}"' in text
    assert "delegate_to: localhost" not in text


def test_controller_delegation_limited_to_local_secret_provisioning():
    for path in TASKS.rglob("*.yml"):
        if path.name != "tsig.yml":
            assert "delegate_to: localhost" not in path.read_text(), path.name
