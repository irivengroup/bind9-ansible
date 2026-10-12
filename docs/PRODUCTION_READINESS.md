# Bind9Project — Production readiness / qualification

## Implemented in V4

- Incremental Ansible deployment; modular `/etc/named.conf` entrypoint and `/etc/named/zones` includes.
- Per-deployment backup of managed files, checksum, locked state, rollback on Ansible block failure.
- Crash/interruption fail-closed: subsequent runs stop on pending deployment; operator must recover.
- `named-checkconf -z`, `named-checkzone`, service status, SOA verification with expected serial.
- Periodic local DNS checks using a systemd timer and journald.
- Four nodes, ordered inventory, site-specific primary/secondary transfer topology.

## Not yet qualified / required before production

1. **RHEL 8 lab validation:** install BIND and run Ansible syntax, check-mode and full playbook twice; ensure zero unintended changes and functional idempotence. The deployment snapshot controller currently reports changes on each run; optimize change detection before production.
2. **Full pre-activation staging:** current V4 validates after installing managed files but before reloading; the on-disk BIND config can temporarily be invalid. For stronger guarantees, implement a shadow configuration tree with all includes and zones rewritten and validate before promotion.
3. **Full metadata recovery:** V4 managed-file rollback preserves POSIX ownership and modes, but not extended ACLs/xattrs; `restorecon` reapplies standard SELinux labels. Verify custom ACLs and labels, or extend metadata-preserving rollback.
5. **TSIG and network:** run AXFR/IXFR/NOTIFY positive and negative tests from each site; reject unauthorized transfers, recursion and DNS traffic from unapproved sources.
6. **Multisite serial consistency:** enforce matching zone contents and SOA serials across both primaries; compare remote DNS responses during acceptance.
7. **Failure injection:** test malformed zone, missing TSIG, unavailable forwarder, interrupted Ansible, DNS process failure, loss of each NIC and entire site.
9. **Monitoring:** connect timer failures and stale SOA serials to central monitoring and alerting; add transfer/forwarding latency metrics and retention.
10. **Recovery:** restore pending transaction with the same `--zone` list; verify restored service and file checksums. The script is installed at `/usr/local/sbin/Bind9Project-transaction`.

## Recovery example

```bash
sudo /usr/local/sbin/Bind9Project-transaction status
sudo /usr/local/sbin/Bind9Project-transaction restore \
  --zone=cloud.example.test --zone=services.example.test
sudo restorecon -RF /etc/named.conf /etc/named /var/named
sudo systemctl restart named
sudo named-checkconf -z /etc/named.conf
```

Never run `begin` on an unresolved pending transaction. Archive baseline lives under `/var/backups/Bind9Project/bind`.

## Lab test matrix

| Test | Expected result |
| --- | --- |
| Initial deploy | All four services active; zones resolve |
| Repeat deploy | No DNS service disruption; inspect changed tasks |
| Bad SOA / syntax | Deployment fails; previous files restored |
| SIGKILL during deployment | Pending marker blocks next run; operator restores |
| Secondary initial sync | Serial eventually matches local primary |
| Unauthorized AXFR | REFUSED / transfer denied |
| External Internet recursion | Not available |
| Front-to-back ACL bypass | Blocked |
| Primary failure | Secondary serves authoritative data |
| Site outage | Clients can reach the other site through their configured DNS resolvers |

**Status: implementation for lab qualification; not production certified.**
