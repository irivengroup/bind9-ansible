# 🏷️ Bind9-Ansible — Rôle Ansible `bind9`

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


| Variable | Type | Description | Valeur par défaut | Obligatoire ? |
|---|---|---|---|---|
| `bind_frontend_clients` | `list[str]` | Adresses IPv4/IPv6 individuelles ou CIDR canoniques autorisés sur FRONT | `[]` | Non |
| `bind_backend_clients` | `list[str]` | Adresses IPv4/IPv6 individuelles ou CIDR canoniques autorisés sur BACK ; vue `internal` créée seulement si non vide | `[]` | Non |
| `bind_forward_zones` | `list[dict]` | Zones forwardées ; champs `domain`, `forwarders` (`address`, `port`), `policy` | `[]` | Non |
| `bind_authoritative_zones` | `list[dict]` | Zones autoritaires ; `domain`, `ttl`, `serial`, `records` | `[]` | Non |
| `bind_backup_root` | `str` | Emplacement des sauvegardes BIND | `/var/backups/Bind9-Ansible/bind` | Non |
| `bind_migration_state_dir` | `str` | État et points de reprise de migration | `/var/lib/Bind9-Ansible/bind-migration` | Non |
| `bind_monitoring_enabled` | `bool` | Active la surveillance périodique DNS | `true` | Non |

**Variables d'inventaire indispensables :** `ansible_host`, `bind_back_ip`, `bind_front_ip` sont définies par hôte dans `inventory/host_vars/`. Les paramètres TSIG (`bind_tsig_key_name`, `bind_tsig_algorithm`, `bind_tsig_secret_file`) sont définis par site dans `inventory/group_vars/` ; ils ne figurent pas dans les defaults.

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
ansible-playbook -i inventory/hosts.ini playbooks/bind9-deploy.yml --ask-vault-pass
# Variante non interactive :
ansible-playbook -i inventory/hosts.ini playbooks/bind9-deploy.yml \
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
```

```yaml
# inventory/group_vars/site1.yml
bind_site_name: site1
bind_tsig_key_name: site1-transfer
bind_tsig_algorithm: hmac-sha256
bind_tsig_secret_file: "{{ lookup('env', 'HOME') }}/.local/share/bind9-ansible/tsig/site1.key"
```

### 2. Appel du Playbook (`playbooks/bind9-deploy.yml`)

```yaml
---
- name: Deploy Bind9-Ansible BIND 9
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
ansible-playbook -i inventory/hosts.ini playbooks/bind9-deploy.yml --syntax-check

# Déploiement complet
ansible-playbook -i inventory/hosts.ini playbooks/bind9-deploy.yml

# Vérifications ciblées (configuration existante)
ansible-playbook -i inventory/hosts.ini playbooks/bind9-deploy.yml --tags validate
ansible-playbook -i inventory/hosts.ini playbooks/bind9-deploy.yml --tags health

# Exclure la configuration de surveillance
ansible-playbook -i inventory/hosts.ini playbooks/bind9-deploy.yml --skip-tags monitor

# Dry-run indicatif : peut être incomplet pour les opérations de scripts/TSIG
ansible-playbook -i inventory/hosts.ini playbooks/bind9-deploy.yml --check --diff
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
ansible-playbook -i inventory/hosts.ini playbooks/bind9-deploy.yml --syntax-check
ansible-lint playbooks/bind9-deploy.yml roles/bind9
```

**Molecule :** framework recommandé pour des tests de convergence, d'idempotence et de restauration sur des cibles de test RHEL 8. Le dépôt examiné ne fournit pas de scénario Molecule certifié ; la commande suivante est un objectif d'intégration, **pas une preuve de tests Molecule déjà réussis** :

```bash
molecule test
```

Avant mise en production, ajouter une recette incluant `named-checkconf -z`, `named-checkzone`, requêtes `dig` sur FRONT/BACK, refus des accès interdits, transferts TSIG, suppression/modification de PTR, validation SOA et restauration d'archives avec ACL/SELinux.

## 🤝 Contribution & Bonnes Pratiques

Créer une branche dédiée, conserver les secrets hors Git, modifier les variables au bon niveau (`host_vars` pour les interfaces/IP, `group_vars` pour les paramètres de site), puis lancer les tests, `ansible-lint` et le contrôle de syntaxe avant commit. Les modifications des zones DNS doivent être testées sur un environnement isolé avant leur promotion. Ouvrir une pull request avec la portée du changement, les risques et la procédure de retour arrière.

## 🔎 Politique réseau et DNS inverse


Déclarer les clients FRONT et BACK dans `inventory/group_vars/all.yml`. Les listes
vides n'activent aucune vue client ; `peer` et `deny` restent présentes.

```yaml
bind_frontend_clients:
  - 198.51.100.0/24
bind_backend_clients:
  - 10.30.0.0/16
```

La vue `external` reçoit les clients FRONT, la vue `internal` les clients BACK.
Toutes deux permettent la récursion uniquement aux clients autorisés, incluent
`20-forward-zones.conf` et consultent les zones autoritaires via `in-view "peer"`.
Les transferts de zones sont interdits dans ces deux vues ; seuls les transferts
TSIG explicitement autorisés sur les zones Primary de `peer` sont possibles.
Les PTR sont générés automatiquement en IPv4 /24 et IPv6 /64.

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


## Politique des vues DNS (FRONT/BACK)

- `peer` : échanges Primary/Secondary sur BACK, transferts TSIG par zone.
- `external` : uniquement si `bind_frontend_clients` est non vide ; écoute FRONT, récursion contrôlée et forwarding.
- `internal` : uniquement si `bind_backend_clients` est non vide ; écoute BACK, récursion contrôlée et forwarding.
- `deny` : refus de toutes les autres requêtes.

La politique globale reste `recursion no`, `allow-transfer { none; }` et
`dnssec-validation auto`. Les vues client autorisées définissent `recursion yes`.


## Vue interne conditionnelle (BACK)

`bind_backend_clients: []` par défaut : aucune ACL `backend_clients` ni vue `internal` n'est générée. Une liste non vide d'adresses individuelles ou de CIDR canoniques active une vue `internal` écoutant exclusivement sur `bind_back_ip`, avec récursion et cache autorisés uniquement pour cette ACL. Elle utilise les zones autoritaires partagées depuis `peer` via `16-internal-authoritative-zones.conf` et les zones forwardées de `20-forward-zones.conf`. Les transferts restent interdits dans cette vue ; seuls les transferts TSIG dans `peer` sont autorisés. La vue `peer` doit apparaître avant `internal` pour intercepter les communications entre homologues.

```yaml
bind_backend_clients:
  - 10.30.0.0/16
  - 10.30.0.53
```

### Condition minimale des clients DNS

Au moins une des listes `bind_frontend_clients` et `bind_backend_clients` doit être non vide. Le rôle interrompt le déploiement lors des validations si les deux listes sont vides. Chaque vue client reste facultative individuellement : la vue `external` est créée uniquement si `bind_frontend_clients` est renseignée, et la vue `internal` uniquement si `bind_backend_clients` est renseignée.

### Inclusion des ACL

`/etc/named/20-acl.conf` est toujours inclus dans `named.conf` et déployé par le rôle. Les ACL `frontend_clients` et `backend_clients` restent chacune conditionnelles à leur liste respective. La validation préalable interdit que les deux listes soient vides simultanément.


## Inventaires isolés par environnement et site

Le rôle utilise désormais `bind_environment` et `bind_site_name` pour contrôler
l'appartenance des homologues Primary/Secondary. Le même nom de site peut exister
indépendamment en production, développement et préproduction.

Les inventaires sont fournis dans `inventory/prod/`, `inventory/dev/` et
`inventory/preprod/`. Chaque environnement possède ses propres `hosts.ini`,
`group_vars/` (ACL, zones, forwarders, TSIG) et `host_vars/` (adresses).
Les adresses, zones et réseaux fournis sont **des exemples à remplacer**.

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --limit site1 --tags deploy
ansible-playbook -i inventory/dev/hosts.ini playbooks/bind9-deploy.yml --limit site1 --tags deploy
ansible-playbook -i inventory/preprod/hosts.ini playbooks/bind9-deploy.yml --tags deploy
```

N'utilisez qu'**un inventaire d'environnement à la fois** : cela évite les
collisions de noms d'hôtes ou de groupes entre environnements. Les secrets TSIG
sont isolés par chemin `<environnement>/<site>.key`. L'ancien inventaire
`inventory/hosts.ini` reste temporairement disponible pour compatibilité.

## Compatibilité Python 3.8+ et BIND RHEL 8+

- Les scripts Python et filtres sont contrôlés par un test de parsing ciblant Python 3.8 ; la CI définit une matrice Python 3.8 à 3.12. Le résultat de cette matrice n'est pas attesté tant qu'elle n'a pas été exécutée sur GitHub Actions.
- Les déclarations de zones BIND utilisent les mots-clés `type master`, `type slave` et `masters`, compatibles avec la génération BIND 9.11 livrée avec RHEL 8.
- Les vues `peer`, `external` et `internal`, les références `in-view`, les règles TSIG et `dnssec-validation auto` restent en place.
- La compatibilité RHEL 9+ doit être confirmée par `named-checkconf -z /etc/named.conf` et des tests de résolution, forwarding, AXFR/IXFR et rollback sur les versions ciblées.
- L'installation et l'exécution réelles sur RHEL 8+ ne sont pas validées par les tests unitaires du dépôt.


### Sécurisation des adresses source et des transferts

- `query-source` n'est pas forcé globalement : le routage détermine la source des requêtes sortantes des vues clientes. Vérifier les règles de routage/pare-feu vers les forwarders.
- `transfer-source` et `notify-source` sont définis **uniquement dans la vue `peer`** et utilisent `bind_back_ip`.
- `allow-transfer { none; };` demeure global et dans la vue `peer`; les zones Primary autorisent explicitement les transferts par clé TSIG.
- Les Secondary n'acceptent les NOTIFY que s'ils sont signés avec la clé TSIG du couple environnement/site.
- Les vues clientes refusent les transferts et la vue `deny` bloque les clients non autorisés.
- **Limite de sécurité importante :** une directive `allow-transfer` par clé TSIG ne lie pas cryptographiquement la requête à une interface réseau. La segmentation BACK et les filtrages réseau TCP/UDP 53 restent nécessaires pour empêcher l'accès aux transferts depuis FRONT. Les vues `in-view` partagent les zones de `peer`; tester explicitement les tentatives AXFR sur FRONT.
- Exécuter `named-checkconf -z /etc/named.conf`, puis vérifier AXFR/NOTIFY signés et non signés sur les interfaces BACK et FRONT avant mise en production.


## Routes de retour FRONT (RHEL 8+)

Le déploiement `--tags deploy` détecte automatiquement l’interface associée à `bind_front_ip`, sa connexion NetworkManager active et sa passerelle IPv4 (`nmcli`). Il ajoute des routes **persistantes et ciblées** pour les réseaux IPv4 déclarés dans `bind_frontend_clients` qui ne sont pas déjà routés via FRONT, sans toucher à la route par défaut portée par BACK. Le script est installé sous `/usr/local/sbin/bind-front-routes`.

Pré-requis : `nmcli`, `ip`, Python 3.8+ et une passerelle IPv4 renseignée sur la connexion FRONT. Si aucune passerelle n’est détectée, le déploiement échoue explicitement plutôt que d’inventer une route. Les CIDR IPv6 sont ignorés par le gestionnaire de routes IPv4 ; les réseaux IPv6 nécessitent une politique de routage distincte. Les ACL sont utilisées comme source déclarative : n’y inclure que les réseaux réellement joignables via FRONT. **Attention** : la modification réseau est persistante et n’est pas comprise dans le rollback transactionnel BIND. Vérifier préalablement la connectivité SSH ADM et les chemins de retour.
