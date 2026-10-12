# 🏷️ DNSZIBO V4 — Rôle Ansible `bind9`

> Déploiement reproductible et sécurisé d'une architecture BIND 9 multi-sites, avec séparation des réseaux d'administration et de résolution, réplication Primary/Secondary par TSIG, zones autoritaires et forwardées, génération automatique des PTR, sauvegardes et retour arrière transactionnel.

**Mainteneur :** Alfred TCHONDJO · **Organisation :** IRIVEN Group · **Projet :** https://github.com/irivengroup

> **État de qualification :** les contrôles unitaires et statiques du dépôt ne remplacent pas une recette sur RHEL 8. Aucun résultat de CI sur une matrice d'OS ou de versions Ansible n'est attesté ici.

## 📊 Matrice de compatibilité

| Composant | Déclaration du projet | Statut de validation |
|---|---|---|
| OS cible | Enterprise Linux 8 / RHEL 8 | Plateforme déclarée dans `roles/bind9/meta/main.yml` ; validation d'intégration à réaliser |
| Ansible | `min_ansible_version: '2.9'` | Minimum déclaré, compatibilité effective à qualifier ; privilégier une version maintenue |
| Python sur les cibles | Python 3 compatible avec la version d'Ansible retenue | Pré-requis des modules Ansible ; version précise non certifiée |
| BIND | BIND 9 (`named`) | Vérification opérationnelle sur les serveurs requise |
| Contrôleur | Linux avec Ansible, Python et accès SSH | Provisionnement local des secrets TSIG |

## 🔌 Prérequis & Dépendances

- Accès SSH aux quatre hôtes sur l'interface **ADM** et élévation de privilèges (`become: true`).
- Interfaces **BACK** (échanges DNS entre pairs) et **FRONT** (requêtes des clients cloud) configurées et routables ; l'interface ADM ne doit pas servir à la résolution DNS.
- Résolution réseau et accès aux dépôts nécessaires à l'installation des paquets BIND sur RHEL 8.
- `tar`, `sha256sum`, `flock` et outils de validation BIND pour les opérations de sauvegarde et de restauration ; vérifier leur présence sur les cibles.
- Contrôleur disposant de droits d'écriture sur le répertoire privé des secrets TSIG.
- Le fichier `requirements.yml` déclare **aucune collection externe** (`collections: []`). Les modules utilisés appartiennent principalement à `ansible.builtin` ; vérifier les exigences de toute dépendance ajoutée ultérieurement.

```bash
ansible-galaxy collection install -r requirements.yml
```

## 🛠️ Variables du Rôle / Playbook

Les paramètres génériques proviennent de `roles/bind9/defaults/main.yml`. Les listes `bind_frontend_clients`, `bind_backend_clients`, `bind_forward_zones` et `bind_authoritative_zones` y sont vides par défaut (`[]`) ; leurs exemples sont déclarés dans `inventory/group_vars/all.yml` et doivent être adaptés avant production.

| Variable | Type | Description | Valeur par défaut | Obligatoire ? |
|---|---|---|---|---|
| `bind_frontend_clients` | `list[str]` | Adresses IPv4/IPv6 individuelles ou CIDR canoniques autorisés sur FRONT | `[]` | Non |
| `bind_backend_clients` | `list[str]` | Adresses IPv4/IPv6 individuelles ou CIDR canoniques autorisés sur BACK ; vue absente si liste vide | `[]` | Non |
| `bind_forward_zones` | `list[dict]` | Zones forwardées ; champs `domain`, `forwarders` (`address`, `port`), `policy` | `[]` | Non |
| `bind_authoritative_zones` | `list[dict]` | Zones autoritaires ; `domain`, `ttl`, `serial`, `records` | `[]` | Non |
| `bind_enable_cgroups` | `bool` | Activation des limites de ressources systemd | `false` | Non |
| `bind_memory_max` | `str` | Limite mémoire lorsque les cgroups sont activés | `512M` | Non |
| `bind_tasks_max` | `int` | Limite du nombre de tâches systemd | `256` | Non |
| `bind_cpu_quota` | `str` | Quota CPU systemd | `100%` | Non |
| `bind_backup_root` | `str` | Emplacement des sauvegardes BIND | `/var/backups/Bind9Project/bind` | Non |
| `bind_migration_state_dir` | `str` | État et points de reprise de migration | `/var/lib/Bind9Project/bind-migration` | Non |
| `bind_monitoring_enabled` | `bool` | Active la surveillance périodique DNS | `true` | Non |

**Variables d'inventaire indispensables :** `ansible_host`, `bind_back_ip`, `bind_front_ip`, `bind_back_interface`, `bind_front_interface` sont définies par hôte dans `inventory/host_vars/`. Les paramètres TSIG (`bind_tsig_key_name`, `bind_tsig_algorithm`, `bind_tsig_secret_file`) sont définis par site dans `inventory/group_vars/` ; ils ne figurent pas dans les defaults.

**Exemple d’enregistrements directs (masques inverses fixes) :**

```yaml
bind_authoritative_zones:
  - domain: cloud.example.test
    ttl: 300
    serial: 2026101201
    records:
      - { name: app, type: A, value: 192.0.2.100 }
      - { name: api, type: AAAA, value: '2001:db8:1234:5678::10' }
```

Le générateur crée et synchronise automatiquement les zones `in-addr.arpa` et `ip6.arpa` à partir des enregistrements `A`/`AAAA`, sans `bind_reverse_zones` ni `bind_reverse_networks`. Les masques par défaut sont `/24` et `/64`. Le champ `mask` par enregistrement est interdit ; la génération inverse suit exclusivement les préfixes fixes. La suppression d'un enregistrement direct entraîne la suppression du PTR géré correspondant.

## 🔐 Gestion des Secrets (Ansible Vault)

Le rôle actuel utilise un **secret TSIG persistant dans un fichier privé du contrôleur**, défini par `bind_tsig_secret_file`, et peut le générer automatiquement. Ne pas le committer ni exposer son contenu dans les logs. Ansible Vault peut servir à protéger d'autres secrets d'inventaire, mais **déclarer `vault_tsig_secret` ne suffit pas à le brancher automatiquement au rôle** : ce branchement demanderait une adaptation explicite du provisionnement TSIG.

Exemple d'organisation :

```text
inventory/
  group_vars/
    all/
      vault.yml            # chiffré avec ansible-vault
  group_vars/
    site1.yml              # paramètres TSIG non secrets
    site3.yml
  host_vars/
    site1-dns01.yml
    ...
```

```yaml
# inventory/group_vars/all/vault.yml — contenu AVANT chiffrement (exemple)
vault_dns_operations_token: 'REPLACE_WITH_SECRET'  # exemple facultatif, non consommé par le rôle
```

```bash
ansible-vault encrypt inventory/group_vars/all/vault.yml
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --ask-vault-pass
# Variante non interactive :
ansible-playbook -i inventory/hosts.ini playbooks/site.yml \
  --vault-password-file /chemin/securise/vault-password-file
```

Le fichier de mot de passe Vault doit rester hors du dépôt, avec des permissions restrictives.

## 🏷️ Découpage par Tags (Modularité)

| Tag | Fonction | Utilisation recommandée |
|---|---|---|
| `deploy` | Déploiement complet : précontrôles, sauvegarde, configuration, zones, validation, service, contrôle de santé et rollback | `--tags deploy` |
| `preflight` | Validation des entrées, chemins et disponibilité du primaire | Diagnostic préalable uniquement |
| `install` | Installation des paquets BIND | Étape technique, **pas un déploiement autonome** |
| `backup` | Sauvegarde de configuration avant modification | Sauvegarde manuelle |
| `configure` | Configuration, répertoires, TSIG, zones et sécurité | Étape technique, **ne pas exécuter seule en production** |
| `zones` | Écriture des fichiers de zones, dont les zones inverses générées | Sous-étape technique, **pas une opération autonome** |
| `records` | Mise à jour des enregistrements via les fichiers de zones | Sous-étape technique, **pas une opération autonome** |
| `services` | Gestion du service systemd BIND | Maintenance du service |
| `validate` | Validation BIND et fichiers de zones | Diagnostic de la configuration existante |
| `health` | Contrôles DNS et SOA | Diagnostic de résolution |
| `monitor` | Installation et configuration de la surveillance | Maintenance du monitoring |
| `always` | Calcul des pairs et zones automatiques | Préparation commune |

**Sécurité transactionnelle :** utiliser `--tags deploy` pour toute modification de configuration, de zone ou d'enregistrement. Les tags `configure`, `zones` et `records` servent au découpage des tâches, **mais ne constituent pas des opérations transactionnelles autonomes** : une exécution isolée pourrait contourner sauvegarde, contrôles ou commit. Le tag `deploy` conserve le chemin complet avec rollback. La suppression d'une zone ou d'un record s'effectue en modifiant l'état désiré dans l'inventaire puis en relançant `deploy`.

## 🚀 Exemples d'Utilisation

### 1. Structure de l'Inventaire (`inventory/hosts.ini`)

```ini
[site1]
site1-dns01
site1-dns02

[site3]
site3-dns01
site3-dns02

[dns_primary]
site1-dns01
site3-dns01

[dns_secondary]
site1-dns02
site3-dns02

[dns_deployment_order]
site1-dns01
site3-dns01
site1-dns02
site3-dns02
```

```yaml
# inventory/host_vars/site1-dns01.yml — adresses d'exemple
ansible_host: 10.10.1.11       # ADM : SSH uniquement
bind_back_ip: 10.20.1.11       # BACK : pairs DNS
bind_front_ip: 192.0.2.11      # FRONT : clients cloud
bind_back_interface: ens224
bind_front_interface: ens192
```

```yaml
# inventory/group_vars/site1.yml
bind_site_name: site1
bind_tsig_key_name: site1-transfer
bind_tsig_algorithm: hmac-sha256
bind_tsig_secret_file: "{{ lookup('env', 'HOME') }}/.local/share/dnszibo/tsig/site1.key"
```

### 2. Appel du Playbook (`playbooks/site.yml`)

```yaml
---
- name: Deploy DNSZIBO BIND 9
  hosts: dns_deployment_order
  become: true
  gather_facts: true
  order: inventory
  serial: 1
  any_errors_fatal: true
  roles:
    - bind9
```

L'ordre Primary puis Secondary est intentionnel : il facilite la disponibilité des zones sources avant les transferts.

### 3. Commandes d'Exécution (CLI)

```bash
# Préflight : inventaire et syntaxe
ansible-inventory -i inventory/hosts.ini --graph
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --syntax-check

# Déploiement complet
ansible-playbook -i inventory/hosts.ini playbooks/site.yml

# Vérifications ciblées (configuration existante)
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags validate
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags health

# Exclure la configuration de surveillance
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --skip-tags monitor

# Dry-run indicatif : peut être incomplet pour les opérations de scripts/TSIG
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --check --diff
```

### Sauvegarde, restauration et reprise

Avant modification, `bind-backup.sh` réalise une sauvegarde complète de `/etc/named.conf`, `/etc/named/` et `/var/named/`, au maximum une par heure UTC, avec conservation des **cinq dernières archives** et empreinte SHA-256. Le snapshot transactionnel de `named-transaction` est indépendant de cette rétention horaire.

```bash
sudo /usr/local/sbin/bind-backup.sh backup
sudo /usr/local/sbin/bind-backup.sh list

# Restauration : planifier une fenêtre de maintenance et vérifier le nom exact
sudo systemctl stop named
sudo /usr/local/sbin/bind-backup.sh restore \
  --archive named-config-YYYYMMDDTHHMMSSZ.tar.gz
sudo named-checkconf -z /etc/named.conf
sudo systemctl start named
```

Ne pas assimiler la restauration d'une sauvegarde horaire à un retour exact à l'état précédent si plusieurs changements ont été effectués durant la même heure. Voir `docs/BACKUP_AND_RECOVERY.md`.

## 🧪 Tests & Qualité du Code (CI/CD)

Le dépôt contient des tests Python et un workflow GitHub Actions (`.github/workflows/ci.yml`) configuré avec `ansible-core==2.16.14` et Python 3.12 sur un runner Ubuntu. La présence de ce workflow ne prouve pas que son exécution a réussi ni que BIND a été validé sur RHEL 8. Exécuter les contrôles disponibles localement :

```bash
python3 -m pytest -q
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --syntax-check
ansible-lint playbooks/site.yml roles/bind9
```

**Molecule :** framework recommandé pour des tests de convergence, d'idempotence et de restauration sur des cibles de test RHEL 8. Le dépôt examiné ne fournit pas de scénario Molecule certifié ; la commande suivante est un objectif d'intégration, **pas une preuve de tests Molecule déjà réussis** :

```bash
molecule test
```

Avant mise en production, ajouter une recette incluant `named-checkconf -z`, `named-checkzone`, requêtes `dig` sur FRONT/BACK, refus des accès interdits, transferts TSIG, suppression/modification de PTR, validation SOA et restauration d'archives avec ACL/SELinux.

## 🤝 Contribution & Bonnes Pratiques

Créer une branche dédiée, conserver les secrets hors Git, modifier les variables au bon niveau (`host_vars` pour les interfaces/IP, `group_vars` pour les paramètres de site), puis lancer les tests, `ansible-lint` et le contrôle de syntaxe avant commit. Les modifications des zones DNS doivent être testées sur un environnement isolé avant leur promotion. Ouvrir une pull request avec la portée du changement, les risques et la procédure de retour arrière.

## 🔎 Politique réseau et DNS inverse

### Clients DNS internes sur BACK (`bind_backend_clients`)

Déclarer les adresses IP ou réseaux autorisés dans `inventory/group_vars/all.yml` :

```yaml
bind_backend_clients: []  # Aucun client BACK par défaut
# Exemple :
# bind_backend_clients:
#   - 10.30.0.0/16
#   - 10.30.0.53
#   - 2001:db8::53
```

La valeur par défaut du rôle (`defaults/main.yml`) est `[]`. Si la liste est vide,
DNSZIBO ne rend ni l'ACL `backend_clients` ni la vue `backend`. Les requêtes
des clients BACK sont refusées par la vue `deny`, tandis que la vue `peer`
continue à assurer les échanges Primary/Secondary et les transferts TSIG.
Si elle est non vide, les clients de la liste sont acceptés **uniquement sur
l'adresse BACK** ; la vue BACK utilise les zones autoritaires partagées et les
zones de forwarding configurées. L'interface ADM reste hors du service DNS.

Les vues FRONT et BACK sont indépendantes. Si les deux listes sont vides,
aucune ACL client n'est incluse dans `named.conf`. Les vues client correspondantes
ne sont pas rendues ; seules les vues `peer` et `deny` restent présentes.

### Politique de sécurité des vues et de la résolution inverse

- Les enregistrements A et AAAA produisent automatiquement les PTR avec des zones **/24 IPv4** et **/64 IPv6**. Le champ `mask` par enregistrement est interdit.
- **FRONT** : récursion et cache désactivés ; seuls les enregistrements autoritaires sont accessibles aux clients `bind_frontend_clients`. Les zones forwardées ne sont pas exposées sur FRONT.
- **BACK** : les clients `bind_backend_clients` peuvent accéder aux zones autoritaires et aux zones forwardées ; la récursion est réservée à cette vue.
- Les ACL acceptent des adresses IPv4/IPv6 individuelles sans masque, ainsi que des réseaux CIDR canoniques ; les valeurs incorrectes bloquent le déploiement.
- La vue `peer` demeure réservée aux échanges entre serveurs DNS.

## 📄 Licence

**Propriétaire (`proprietary`)**, conformément à `roles/bind9/meta/main.yml`. Aucune licence open source ne doit être présumée sans décision explicite du titulaire des droits.

© IRIVEN Group — Alfred TCHONDJO.


### Industrialisation multi-sites

Le rôle ne contient aucun nom de site codé en dur. Chaque groupe de site
doit déclarer `bind_site_name` avec le nom exact de son groupe d’inventaire.
Les groupes transverses `dns_primary` et `dns_secondary` déterminent les pairs.
Pour ajouter un site, créer son groupe dans `inventory/hosts.ini`, son fichier
`inventory/group_vars/<site>.yml` avec `bind_site_name: <site>` et les deux
fichiers `host_vars` contenant les IP BACK/FRONT. Ajouter les hôtes aux groupes
`dns_primary`, `dns_secondary` et `dns_deployment_order` (primaires avant secondaires).
Aucune modification de `roles/bind9/tasks/` n'est nécessaire.

### Réutilisation des groupes de rôles DNS

Les noms des groupes Primary et Secondary ne sont pas codés en dur dans les tâches ou templates. Ils sont paramétrés par `bind_primary_group` (défaut `dns_primary`) et `bind_secondary_group` (défaut `dns_secondary`). Les noms de sites sont fournis par `bind_site_name` dans les `group_vars` de chaque site. Les noms des groupes et les valeurs IP dans l’inventaire sont des exemples remplaçables.

```yaml
bind_primary_group: prod_dns_masters
bind_secondary_group: prod_dns_replicas
```

Les chemins système standards BIND (`/etc/named`, `/var/named`) et les noms de services ne sont pas généralisés automatiquement : leur modification exige une refonte coordonnée des scripts de sauvegarde, des transactions et des politiques SELinux.

### PATH des utilitaires d'administration

Le rôle installe `/etc/profile.d/bind9-sbin-path.sh` pour les sessions shell qui chargent `/etc/profile`. Ce fichier ajoute `/usr/local/sbin` au `PATH` **uniquement si ce répertoire est absent**, sans duplication. La prise en compte se fait à la prochaine ouverture de session (ou avec `source /etc/profile.d/bind9-sbin-path.sh`). Les tâches Ansible et les services systemd utilisent des chemins absolus pour les outils critiques et ne dépendent pas du `PATH` interactif.
