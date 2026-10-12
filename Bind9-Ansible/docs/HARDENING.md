# Bind9-Ansible security hardening

The current migration task calls the full backup utility before DNS configuration changes, without deleting the existing BIND trees. Packages and controller TSIG provisioning occur before this backup. Managed-file rollback is per host and covers only its explicit allowlist. Backups contain secrets and require protected off-host storage.


The role derives `bind_tsig_secret` from a private Base64 file on the controller; this file is not an Ansible Vault document. Protect its permissions and backups. Automatic rotation is not implemented. Never print or commit secrets. Run `named-checkconf -z`, query tests and AXFR/IXFR checks on both servers. See the French README for the current contract and validation limits.

Caveats: source validation and CI are not substitutes for real BIND, SELinux and firewall tests. In particular, forwarding with view-level recursion requires a dedicated functional test.
