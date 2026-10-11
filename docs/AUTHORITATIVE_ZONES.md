# Authoritative zones

Set `bind_authoritative_zones` to a list of domain/serial/ttl/records definitions.
The same definitions are deployed to each site's primary; secondaries transfer
zones from their local primary using TSIG. Zone file path on primaries:
`/var/named/zones/<domain>.zone`; secondaries: `/var/named/slaves/<domain>.zone`.

Increment SOA `serial` on every zone content change so secondaries update.
Ensure every zone's NS addresses refer to the correct site's DNS nodes, or use
site-neutral service addresses if these zones are publicly delegated. The
example uses per-site NS A records and must be adapted for external delegation.

Do not overlap authoritative zones with forwarding zones. Use only trusted
values for records; validate all generated files with named-checkconf -z.
