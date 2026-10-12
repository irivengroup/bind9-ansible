# Network policy (external to this role)

DNSZIBO does not manage firewalld, iptables, nftables or network security
groups. Network filtering is an external prerequisite.

- Front: allow UDP/TCP 53 only from authorized DNS clients.
- Back: allow DNS peers and approved conditional forwarders as appropriate;
  zone transfers use TCP/53 and NOTIFY commonly uses UDP/53.
- Ensure SSH administrative connectivity is preserved.
- Validate policy externally before production deployment.

Les listes `bind_frontend_clients` et `bind_backend_clients` acceptent les adresses IPv4/IPv6 individuelles (sans masque) et les réseaux CIDR canoniques.
