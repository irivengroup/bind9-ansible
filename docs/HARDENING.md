# Bind9Project security hardening

First-run migration is destructive and must be tested on cloned RHEL 8 hosts. A verified archive is created outside BIND trees before cleanup. On a first-run failure, the play attempts to restore the original configuration. Backups are root-only and must be copied to off-host immutable storage.

The role does not modify NetworkManager firewall zone assignments. Validate them before execution and keep SSH access on an independent management path. Existing firewalld direct/policy/rich rules must be audited manually because they can supersede Bind9Project restrictions.

Store `bind_tsig_secret` only in Ansible Vault. Rotate it using a coordinated maintenance window. Never print or commit secrets. Run `named-checkconf -z`, query tests and AXFR/IXFR checks on both servers.

Caveats: source validation and CI are not substitutes for real BIND, SELinux and firewall tests. In particular, forwarding with view-level recursion requires a dedicated functional test.
