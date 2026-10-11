> **V4 — version de qualification technique, pas encore certifiée production.** Voir [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md).

# Bind9Project — Ansible BIND 9 / RHEL 8

Deploy two BIND 9 instances: primary and secondary, each with front/back interfaces, modular ACL/options/views/zones, TSIG AXFR/IXFR, conditional forwarding, SELinux labels and optional systemd cgroup limits.

## Requirements

Ansible Core 2.12+ on controller; RHEL 8 managed nodes; SSH sudo; RHEL repositories containing `bind`, `bind-utils`, `policycoreutils-python-utils`.

## Security and configuration

1. Replace the example IPs, subnets, domain names and zone records in `inventory/hosts.ini` and `roles/bind9/defaults/main.yml`.
2. Generate one shared secret with `tsig-keygen -a hmac-sha256 dns-transfer` and extract the `secret` value. Store it as `bind_tsig_secret` in **Ansible Vault**. Never commit it in cleartext.
3. Run `ansible-playbook playbooks/site.yml --ask-vault-pass -e @vault.yml` (where `vault.yml` is encrypted).
4. Restrict network flows with infrastructure firewalls: front UDP/TCP 53 from approved clients only; back UDP/TCP 53 between DNS peers and to the two resolvers; deny other ingress. A BIND ACL alone does not enforce interface-specific client access; enforce with firewall.
5. When updating zone records, increment `bind_zone_serial` to trigger secondary refresh.

## Behavior

- External view handles approved clients and TSIG-authenticated peer traffic.
- Root zone is locally authoritative and empty: queries outside configured local and forwarded zones do not recurse to Internet.
- `recursion yes` in external view is necessary for `type forward` zones. No general recursive Internet resolution is provided.
- TSIG is used for authenticated zone transfers and NOTIFY; the same secret must exist on both nodes.
- Secondary zone files under `/var/named/slaves` are written by named and must not be deployed as static files.
- SELinux uses RHEL standard paths and `restorecon`.
- Optional systemd cgroups are disabled by default (`bind_enable_cgroups: true` enables).

## Validation

On RHEL 8: `named-checkconf -z /etc/named.conf`, `systemctl status named`, `journalctl -u named -n 100`, `dig @192.0.2.11 app.cloud.example.test A`, `dig @192.0.2.12 app.cloud.example.test A`, `dig @192.0.2.11 host.corp.example.test A`, `dig @192.0.2.11 www.example.org A`, `ls -lZ /var/named/slaves`.

## Important operational caveats

- The role manages firewalld when enabled; interface-to-zone assignments must be provisioned beforehand.
- Zone transfer/NOTIFY success depends on BIND view matching and peer network reachability. Validate end-to-end on both RHEL 8 hosts before production.
- The templates and example data are provided as an implementation baseline, not a claim of runtime validation.


## firewalld (DNS front/back)

Install the required collection: `ansible-galaxy collection install -r requirements.yml`.
Before running the role, assign each network interface to its appropriate existing
firewalld zone via NetworkManager, without moving the SSH management interface.
The role checks this association and aborts rather than silently changing it.

Configure per host: `bind_front_interface`, `bind_back_interface`,
`bind_firewalld_front_zone`, `bind_firewalld_back_zone`, and optionally
`bind_firewalld_front_clients`, `bind_firewalld_primary_back_ip`,
`bind_firewalld_secondary_back_ip`. Defaults use `bind_external_clients`
and the primary/secondary back IPs. Set `bind_firewalld_enabled: false`
to opt out. `bind_firewalld_manage_zones: true` creates zones but does not
assign interfaces. Do not use zones with target ACCEPT or broad direct/nftables
rules bypassing this policy. Existing rules are not removed.

The firewall rules apply to incoming DNS traffic only. Forwarding queries
leave through back; replies are accepted by connection tracking. Front
allows UDP/TCP 53 only from listed client CIDRs; back allows TCP/53 from
secondary to primary and UDP/53 from primary to secondary, denying all other
inbound DNS requests. TSIG still authenticates DNS transfers.

Validate on both hosts with `firewall-cmd --list-all --zone=public` and
`firewall-cmd --list-all --zone=internal`, and test DNS/AXFR/NOTIFY.

## Unified conditional forwarding

Both the internal resolver and the independent Fake DNS are standard BIND `type forward` zones. Configure each suffix with its own destination list in `bind_forward_zones`. The Fake DNS is not deployed by this role. A suffix includes the apex and all subdomains; do not prepend `*.`.

```yaml
bind_forward_zones:
  - domain: corp.example.test
    policy: only
    forwarders:
      - address: 10.20.0.53
        port: 53
  - domain: fake.example.test
    policy: only
    forwarders:
      - address: 10.20.0.99
        port: 53
```

A second forwarder in the same zone is **not** guaranteed to be a standby. The local root zone prevents general Internet recursion. Forwarding requires recursion enabled for authorized clients in the matching view.

## Task orchestration

`roles/bind9/tasks/main.yml` imports `validate`, `packages`, `directories`,
`configuration`, `zones`, `security`, `verify`, `firewalld`, and `service`.
The configuration directory is `/etc/named`. Back up and remove any
previous `/etc/named/modular` deployment only after validating the new service.
The old directory is not automatically deleted to avoid destroying unrelated files.

**Security:** the external view matches TSIG requests on the back interface for
zone transfers. A firewall limits TCP/53 to the secondary on the primary and
UDP/53 to the primary on the secondary. Do not grant TSIG keys to clients.
The fake DNS is a separate pre-existing server, not installed by this role.

## First-run destructive migration (mandatory backup)

On the first run, the role backs up the complete legacy `/etc/named.conf`, `/etc/named/` and `/var/named/` trees into `/var/backups/Bind9Project/bind`, verifies the tar archive and SHA-256, and **only then removes the old trees** before installing its managed configuration. The migration is guarded by a persistent marker; later playbook runs are non-destructive and idempotent. See `docs/BACKUP_AND_RECOVERY.md` for the exact rollback procedure. Backups contain sensitive TSIG material and must be stored securely and copied off-host.

## Ordre des fichiers modulaires

Les modules sont préfixés de 10 à 50 dans l’ordre logique de chargement : `10-tsig.conf`, `20-acl.conf`, `30-options.conf`, `40-views.conf`, `50-authoritative-zones.conf`. Le dernier est inclus depuis `40-views.conf`, au sein de la vue `external`, et non directement depuis `named.conf`.


## Mise à jour non destructive

Le rôle conserve les fichiers existants et actualise seulement les chemins gérés par Ansible. Voir `docs/INCREMENTAL_UPDATES.md`. Les anciennes instructions de suppression/restauration automatique ne s’appliquent plus.

### Point d'entrée BIND minimal

`/etc/named.conf` orchestre uniquement les quatre modules `10-tsig.conf`, `20-acl.conf`, `30-options.conf` et `40-views.conf`. Les fichiers de zones sont inclus au sein des vues. Voir [docs/NAMED_ORCHESTRATION.md](docs/NAMED_ORCHESTRATION.md).
