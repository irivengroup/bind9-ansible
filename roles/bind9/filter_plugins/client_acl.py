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
        return {"valid_client_address": valid_client_address}
