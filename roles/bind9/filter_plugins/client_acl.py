"""Validate literal IPv4/IPv6 addresses or canonical CIDR networks."""
import ipaddress


def valid_client_address(value):
    if not isinstance(value, str) or value != value.strip():
        return False
    try:
        if "/" in value:
            ipaddress.ip_network(value, strict=True)
        else:
            ipaddress.ip_address(value)
        return True
    except (ValueError, TypeError):
        return False


class FilterModule:
    def filters(self):
        return {"valid_client_address": valid_client_address, "valid_ip_address": valid_ip_address, "valid_dns_record": valid_dns_record}


def valid_ip_address(value):
    if not isinstance(value, str) or value != value.strip():
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def valid_dns_record(record):
    """Reject control characters and validate address RDATA before BIND rendering."""
    if not isinstance(record, dict):
        return False
    value = record.get('value')
    kind = record.get('type')
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return False
    if kind in ('A', 'AAAA'):
        try:
            return ipaddress.ip_address(value).version == (4 if kind == 'A' else 6)
        except ValueError:
            return False
    return True
