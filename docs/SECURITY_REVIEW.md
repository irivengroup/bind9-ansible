# Security review / limitations

- Front and back views are separated by destination and peer source.
- Initial migration verifies a compressed backup before stopping named and deleting old trees.
- The deployment is not an atomic four-node transaction; test restoration on each site.
- External recursion is constrained by the local root zone and conditional forwarding zones; it is **not** equivalent to BIND `recursion no` on the front view.
- The same zone is declared in two views; BIND may maintain distinct secondary copies per view. In production, verify TSIG AXFR/IXFR and the secondary's writable paths on RHEL 8.
- Firewalld zone bindings, existing allow rules, IPv6, upstream egress and SSH reachability require site-specific review.
- Protect Vault secrets and never commit plaintext TSIG keys.
- Never deploy example IP addresses without replacing them.
