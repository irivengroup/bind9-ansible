# Automatic reverse DNS

`bind_reverse_zones` and `bind_reverse_networks` are not required. Bind9-Ansible derives IPv4 /24 and IPv6 /64 reverse zones from authoritative A/AAAA records. Adding, editing, or deleting A/AAAA records updates the desired reverse zones and PTRs. A removed last PTR removes its DNSZIBO-managed reverse zone; unrelated reverse files are not deleted. Conflicting PTR ownership aborts deployment. The primary publishes generated zones and the secondary receives them via TSIG-protected transfer.

