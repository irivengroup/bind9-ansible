# Source unique des variables DNSZIBO

| Donnée | Source |
| --- | --- |
| IP front/back, NIC | `inventory/host_vars/<hôte>.yml` |
| Adresse SSH | `inventory/group_vars/all.yml`: dérivée de `bind_back_ip` (surcharge externe possible) |
| Site et rôle DNS | Appartenance aux groupes `site1`, `site3`, `dns_primary`, `dns_secondary` |
| Hôtes homologues | `tasks/tasks.d/site_peers.yml` via les groupes d'inventaire |
| IP homologues | `hostvars[bind_primary_host].bind_back_ip` etc., sans variable recopiée |
| Nom/chemin TSIG par site | `inventory/group_vars/site*.yml` |
| Secret TSIG | Stockage persistant du contrôleur ; `bind_tsig_secret` chargé en mémoire avec `no_log` |
| Valeurs communes | `roles/bind9/defaults/main.yml` |

Les destinations des forwarders sont des serveurs tiers : elles ne sont pas des IP d'hôtes DNSZIBO.
