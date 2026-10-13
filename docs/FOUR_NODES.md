# Four-node, two-site deployment

Two independent primary/secondary pairs: zibo-x1 and zibo-x3.
Site-specific back IP addresses are defined in inventory/group_vars.
**Important:** This does not replicate zones between sites. Each site's primary is
managed from the same Ansible zone data; secondaries use local-site AXFR/IXFR.
All IP addresses and interface names are illustrative and must be replaced.
The TSIG secret is currently shared by the four nodes; use separate keys per site
in production by defining bind_tsig_secret in site-scoped encrypted Vault vars.
The role's destructive first migration requires a tested recovery plan.
Check `ansible-inventory -i inventory/hosts.ini --graph` before deployment.

