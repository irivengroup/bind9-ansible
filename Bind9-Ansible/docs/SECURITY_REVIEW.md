# Security review / limitations

- Front and back views are separated by destination and peer source.
- The current migration task verifies a compressed backup without stopping named or deleting the old trees.
- The deployment is not an atomic four-node transaction; test restoration on each site.
- FRONT explicitly uses `recursion no`, denies cache access and exposes authoritative zones only. Conditional forwarding is restricted to the BACK client view.
- Authoritative zones belong to the peer view and are shared through `in-view`. Verify TSIG AXFR/IXFR and secondary writable paths on RHEL 8.
- Firewalld zone bindings, existing allow rules, IPv6, upstream egress and SSH reachability require site-specific review.
- TSIG uses a private plaintext Base64 file on the controller, not automatic Vault integration. Protect this file and its backups; never commit it.
- Never deploy example IP addresses without replacing them.
