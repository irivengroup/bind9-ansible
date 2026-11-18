"""Validate IPv4 client/service addresses and typed IPv4/IPv6 DNS record data."""
import ipaddress
import re


def valid_client_address(value):
    if not isinstance(value, str) or value != value.strip():
        return False
    try:
        if "/" in value:
            ipaddress.IPv4Network(value, strict=True)
        else:
            ipaddress.IPv4Address(value)
        return True
    except (ValueError, TypeError):
        return False


class FilterModule:
    def filters(self):
        return {"valid_client_address": valid_client_address, "valid_ip_address": valid_ip_address,
                "valid_dns_record": valid_dns_record, "valid_dns_ttl": valid_dns_ttl}


def valid_ip_address(value):
    if not isinstance(value, str) or value != value.strip():
        return False
    try:
        ipaddress.IPv4Address(value)
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


def valid_dns_ttl(value):
    """Accept bounded DNS TTLs without permitting zone-file interpolation.

    Preserve decimal seconds and BIND's case-insensitive W/D/H/M/S syntax.
    RFC 2181 section 8 limits the resulting TTL to 2**31-1 seconds; booleans,
    control characters and implicit numeric coercions are never DNS durations.
    """
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return False
    text = str(value)
    if len(text) > 128 or not re.fullmatch(r'(?:[0-9]+|(?:[0-9]+[WDHMSwdhms])+)', text):
        return False
    scale = {'': 1, 'w': 604800, 'd': 86400, 'h': 3600, 'm': 60, 's': 1}
    return sum(int(amount) * scale[unit.lower()] for amount, unit in
               re.findall(r'([0-9]+)([WDHMSwdhms]?)', text)) <= 2147483647
