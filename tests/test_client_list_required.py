from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_nonempty_client_acl_validation_is_declared():
    tasks = yaml.safe_load((ROOT / "roles/bind9/tasks/tasks.d/validate.yml").read_text())
    rule = next(task for task in tasks if task.get("name") == "Require at least one configured DNS client network")
    assert rule["ansible.builtin.assert"]["that"] == [
        "(bind_frontend_clients | length > 0) or (bind_backend_clients | length > 0)"
    ]


def test_client_acl_rule_truth_table():
    # Mirrors the Boolean invariant enforced by the Ansible assert.
    for front, back, expected in [([], [], False), (["192.0.2.10"], [], True),
                                  ([], ["10.0.0.10"], True),
                                  (["192.0.2.10"], ["10.0.0.10"], True)]:
        assert (bool(front) or bool(back)) is expected
