"""Deterministic reverse zones derived exclusively from authoritative A/AAAA records."""
import ipaddress
import re


def automatic_reverse_zones(zones):
    if not isinstance(zones, list):
        raise ValueError("Authoritative zones must be a list")
    result = {}
    pointers = {}
    pointer_zones = {}
    for zone in zones:
        if not isinstance(zone, dict) or not isinstance(zone.get("domain"), str) or not zone["domain"].strip():
            raise ValueError("Each authoritative zone requires a nonempty domain")
        domain = zone["domain"].rstrip(".")
        if not isinstance(zone.get("records", []), list):
            raise ValueError(f"Records for {domain} must be a list")
        for record in zone.get("records", []):
            if not isinstance(record, dict):
                raise ValueError(f"Record in {domain} must be a mapping")
            if not isinstance(record.get("type", ""), str):
                raise ValueError(f"Record type in {domain} must be a string")
            if record.get("type", "").upper() not in ("A", "AAAA"):
                continue
            address = ipaddress.ip_address(record["value"])
            if (record["type"].upper() == "A") != (address.version == 4):
                raise ValueError("Address family does not match record type")
            if "mask" in record:
                raise ValueError("Per-record mask is unsupported; reverse zones use /24 for A and /64 for AAAA")
            prefix = 24 if address.version == 4 else 64
            network = ipaddress.ip_network(f"{address}/{prefix}", strict=False)
            if address.version == 4:
                labels = str(network.network_address).split(".")[:prefix // 8]
                reverse_domain = ".".join(reversed(labels)) + ".in-addr.arpa"
            else:
                nibbles = network.network_address.exploded.replace(":", "")[:prefix // 4]
                reverse_domain = ".".join(reversed(nibbles)) + ".ip6.arpa"
            pointer = address.reverse_pointer
            label = "@" if pointer == reverse_domain else pointer[:-(len(reverse_domain) + 1)]
            name = record["name"]
            fqdn = (domain + "." if name == "@" else name if name.endswith(".") else name + "." + domain + ".")
            if not re.fullmatch(r"(?i)(?:[a-z0-9_-]+\.)+[a-z0-9_-]+\.", fqdn):
                raise ValueError("Invalid PTR target: " + fqdn)
            if pointer in pointers and pointers[pointer] != fqdn:
                raise ValueError("Conflicting PTR for " + str(address))
            if pointer in pointer_zones and pointer_zones[pointer] != reverse_domain:
                raise ValueError(f"Conflicting reverse masks for {address}")
            pointer_zones[pointer] = reverse_domain
            pointers[pointer] = fqdn
            entry = result.setdefault(reverse_domain, {"domain": reverse_domain, "ttl": zone.get("ttl", 300), "serial": int(zone["serial"]), "records": [], "reverse": True})
            entry["serial"] = max(entry["serial"], int(zone["serial"]))
            if not any(item["name"] == label for item in entry["records"]):
                entry["records"].append({"name": label, "type": "PTR", "value": fqdn})
    domains = sorted(result)
    for index, parent in enumerate(domains):
        for child in domains[index + 1:]:
            if child.endswith("." + parent) or parent.endswith("." + child):
                raise ValueError(f"Overlapping automatic reverse zones require explicit DNS delegation: {parent}, {child}")
    for entry in result.values():
        entry["records"].sort(key=lambda item: item["name"])
    return [result[key] for key in sorted(result)]


class FilterModule:
    def filters(self):
        return {"automatic_reverse_zones": automatic_reverse_zones}
