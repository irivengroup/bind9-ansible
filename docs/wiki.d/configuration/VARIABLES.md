# Variables publiques et faits calculés

[Documentation](../../index.md) › [Configuration](index.md) › Variables publiques et faits calculés

[Retour au Parent](index.md)

## Sommaire

- [Variables du rôle / playbook](#variables-du-rôle--playbook)

## Variables du rôle / playbook

### Entrées avec valeur par défaut

Les valeurs par défaut proviennent de [defaults/main.yml](../../../roles/bind9/defaults/main.yml).

| Variable | Type | Description | Valeur par défaut | Obligatoire ? |
|---|---|---|---|---|
| `bind_operation_lock_enabled` | booléen YAML strict | Active à `true` la coopération avec le répertoire historique `.operation.lock`. Routes et management gardent toujours leur exclusion de processus ; les déploiements complets exigent une fenêtre exclusive. L’hibernation conserve son verrou historique | `false` | Non |
| `hibernate_deployment` | booléen, ou chaîne CLI minuscule `yes/no/true/false` | À `yes`, crée le sceau après déploiement complet réussi, ou avec le tag dédié `hibernate`. Un sceau existant bloque toujours la reconstruction, même à `no` | `no` | Non |
| `bind_trusted_networks.cloud_clients` | liste plate de chaînes | IP/CIDR IPv4 FRONT ; seules destinations candidates aux routes retour | `[]` | Oui si internes vides |
| `bind_trusted_networks.internal_clients` | liste plate de chaînes | IP/CIDR IPv4 BACK ; aucune route ajoutée | `[]` | Oui si cloud vide |
| `bind_dnssec_active` | booléen ou chaîne `auto` | Validation globale : `false` → `no`, `true` → `yes`, `auto` → `auto` | `auto` | Non |
| `bind_resources_deploy` | requête de ressource ou enveloppe `batch` | Options, ACL, journalisation, zones et enregistrements de l’état neuf ; génération des inverses | `{resource: batch, action: create, operations: []}` | Non |
| `bind_backup_root` | chemin absolu | Archives BIND privées et empreintes | `/var/backups/Bind9-Ansible/bind` | Non |
| `bind_monitoring_enabled` | booléen | Autorise l’installation du moniteur lorsque ses tâches sont sélectionnées | `false` | Non |
| `bind_front_route_metric` | entier | Métrique des nouvelles routes FRONT uniquement | `100` | Non |
| `bind_front_routes_enabled` | booléen YAML | Active les ajouts FRONT ; ne conditionne pas la reprise explicite | `false` | Non |
| `bind_primary_group` | chaîne | Groupe des primaires | `dns_primary` | Non |
| `bind_secondary_group` | chaîne | Groupe des secondaires | `dns_secondary` | Non |

Les cinq types de ressources sont décrits dans le [contrat de déploiement et de gestion](bind/RESSOURCES.md), avec un [lot complet copiable](../../../examples/deployment/batch.yml). Les deux anciennes variables de zones sont refusées, sans alias.

### Entrées obligatoires sans valeur par défaut

| Variable | Type | Description | Valeur par défaut | Obligatoire ? |
|---|---|---|---|---|
| `bind_environment` | chaîne non vide | Environnement commun aux pairs | Aucune | Oui |
| `bind_site_name` | chaîne non vide | Groupe du site contenant les deux pairs | Aucune | Oui |
| `ansible_host` | destination SSH | IPv4 locale ou nom de connexion SSH | Nom d’inventaire utilisé par Ansible | Selon l’inventaire |
| `bind_admin_ip` | chaîne IPv4 unicast | IPv4 locale de la connexion SSH lorsque sa destination Ansible est un nom ou une adresse traduite | `ansible_host` | Selon l’inventaire |
| `bind_listen_interfaces` | liste d'objets `{name, ipaddress?, scope?}` | Écoutes DNS ; rôles `front` et `back` avec plusieurs écoutes | Aucune | Oui pour chaque hôte |
| `bind_front_ip`, `bind_back_ip` | IPv4 résolues par le rôle | Identités internes dérivées des `scope`, ou de l'écoute unique | Résolution réseau | Ne pas les répéter avec le nouveau format |
| `bind_tsig_key_name` | chaîne | Identifiant TSIG ; ne doit pas être `rndc-key` | Aucune | Oui |
| `bind_tsig_algorithm` | chaîne | Algorithme admis : `hmac-sha256` | Aucune | Oui |
| `bind_tsig_secret_file` | chemin absolu sur le contrôleur | Fichier contenant uniquement le secret Base64 persistant | Aucune | Oui |

Les options `ansible_user`, `ansible_port`, `ansible_python_interpreter` et les variables d’élévation sont les entrées standard Ansible de votre environnement ; le rôle ne leur fournit pas de valeurs spécifiques.

Le management reçoit `bind_resources_manage` (requête CRUD, listing ou lot), `bind_manage_checksums` (hôte → SHA-256 de l’état), `bind_manage_plan_checksums` (hôte → empreinte du plan, obligatoire à l’application d’un lot) et `bind_manage_show_values` (booléen, `false` par défaut). Pour la ressource `zone`, l’objet `zone` et les champs de requête sont identiques dans `bind_resources_deploy` et `bind_resources_manage`. L’action reste limitée à la déclaration transmise. Voir [le contrat unitaire](../exploitation/MANAGEMENT.md) et [les lots/listings](../exploitation/MANAGEMENT_LOTS_ET_LISTES.md).

### Faits calculés et clients

Ne pas déclarer ni surcharger `bind_allowed_clients`, `bind_tsig_secret`, `bind_primary_host`, `bind_secondary_host`, `bind_generated_reverse_zones`, `bind_managed_zones`, les plans de routes et serials attendus : ils sont calculés ou chargés par le rôle. Une surcharge contradictoire de l’union cliente est refusée.

Le contrat des deux listes, leurs refus et leur migration sont décrits dans [CLIENTS_UNIQUES.md](reseau/CLIENTS_UNIQUES.md). Les variables résolues FRONT/BACK sont des faits internes, pas des entrées supplémentaires pour les host_vars.


### Contrats spécialisés

Schémas et données : [ressources](bind/RESSOURCES.md). Politique et bascules : [DNSSEC](bind/DNSSEC.md). Écoutes et ADMIN : [interfaces](reseau/INTERFACES_RESEAU.md).

---

Documentation DNSZIBO · contenu harmonisé le 25 novembre 2026.
