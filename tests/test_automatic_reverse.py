import importlib.util
from pathlib import Path
import pytest

path = Path(__file__).resolve().parents[1] / "roles/bind9/filter_plugins/reverse_dns.py"
spec = importlib.util.spec_from_file_location("reverse_dns", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def zone(records):
    return {"domain": "cloud.example.test", "serial": 2026101201, "records": records}


def test_ipv4_zone_automatic():
    zones = module.automatic_reverse_zones([zone([{"name": "app", "type": "A", "value": "192.0.2.100"}])])
    assert zones[0]["domain"] == "2.0.192.in-addr.arpa"
    assert zones[0]["records"] == [{"name": "100", "type": "PTR", "value": "app.cloud.example.test."}]


def test_ipv6_zone_automatic():
    zones = module.automatic_reverse_zones([zone([{"name": "api", "type": "AAAA", "value": "2001:db8:1234::7"}])])
    assert zones[0]["domain"].endswith(".ip6.arpa")
    assert zones[0]["records"][0]["value"] == "api.cloud.example.test."


def test_deleted_records_disappear():
    assert module.automatic_reverse_zones([zone([])]) == []


def test_conflicting_ptr_rejected():
    with pytest.raises(ValueError, match="Conflicting PTR"):
        module.automatic_reverse_zones([zone([{"name": "a", "type": "A", "value": "192.0.2.100"}, {"name": "b", "type": "A", "value": "192.0.2.100"}])])


def test_non_address_record_ignored():
    assert module.automatic_reverse_zones([zone([{"name": "alias", "type": "CNAME", "value": "app.cloud.example.test."}])]) == []


@pytest.mark.parametrize("mask", [8, 16, 24, 32, 25, None, "//24", "24junk"])
def test_explicit_ipv4_mask_rejected(mask):
    with pytest.raises(ValueError, match="Per-record mask is unsupported"):
        module.automatic_reverse_zones([zone([{"name": "app", "type": "A", "value": "192.0.2.100", "mask": mask}])])


def test_explicit_ipv6_mask_rejected():
    with pytest.raises(ValueError, match="Per-record mask is unsupported"):
        module.automatic_reverse_zones([zone([{"name": "api", "type": "AAAA", "value": "2001:db8::7", "mask": 64}])])


def test_malformed_record_collection_rejected():
    with pytest.raises(ValueError, match="must be a list"):
        module.automatic_reverse_zones([{"domain": "example.test", "records": "invalid"}])
