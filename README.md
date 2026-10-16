# 🏷️ Bind9-Ansible

> Déployer et exploiter une paire de serveurs DNS BIND 9 par site : zones privées autoritatives, forwarding conditionnel, réplication authentifiée et contrôles de santé.

**Point d’entrée :** `playbooks/bind9-deploy.yml` · **Rôle :** `roles/bind9` · **Cible de référence :** RHEL 8, Ansible 2.9 et Python cible 3.6.8.

Le rôle produit une configuration unique `/etc/named.conf`, sans vues BIND. Il conserve les zones localhost officielles, écoute le DNS en IPv4 sur loopback/FRONT/BACK et conserve les capacités IPv4/IPv6 sortantes. Les noms hors des zones déclarées reçoivent une réponse NXDOMAIN par la racine privée. DNSSEC, les routes de retour FRONT et le moniteur périodique sont optionnels, désactivés par défaut.

**Qualification opérationnelle : NON TERMINÉE pour la production.** Cette révision durcit les secrets, sauvegardes/restaurations, chemins privilégiés et routes FRONT. Des contrôles locaux et essais BIND réels sont exécutés en laboratoire ; aucun déploiement complet sur votre paire RHEL 8 n’a été exécuté. La [section validation](#validation) distingue les preuves disponibles des contrôles restant à réaliser ; [DURCISSEMENT_SECURITE.md](docs/DURCISSEMENT_SECURITE.md) décrit les corrections et préconditions de mise à niveau.

Ce guide s’adresse aux équipes d’exploitation. Les commandes du contrôleur supposent Bash sous Linux, depuis la racine du projet. Les commandes de diagnostic système s’exécutent sur le serveur DNS indiqué. Toutes les adresses et tous les domaines d’exemple doivent être adaptés avant utilisation.

## Sommaire

- [Compatibilité](#compatibilite)
- [Architecture et comportement DNS](#architecture)
- [Prérequis et flux réseau](#prerequis)
- [Inventaire](#inventaire)
- [Variables et modèles de zones](#variables)
- [TSIG, secrets et Vault](#secrets)
- [Déploiement](#deploiement)
- [Tags disponibles](#tags)
- [Opérations courantes et routes FRONT](#operations)
- [Sécurité et pare-feu](#securite)
- [Journaux et supervision](#observabilite)
- [Tests de fonctionnement](#tests)
- [Sauvegarde, restauration et rollback](#reprise)
- [Diagnostic des incidents](#diagnostic)
- [Validation et limites](#validation)
- [Livraison et documentation](#livraison)

<a id="compatibilite"></a>
## 📊 Matrice de compatibilité

| Composant | Contrat et preuve disponible |
|---|---|
| OS cible | **Red Hat Enterprise Linux 8**. Déploiement système réel : **NON EXÉCUTÉ**. Le garde accepte `RedHat` de version majeure ≥ 8 ; cela ne qualifie pas les versions supérieures. Rocky Linux, AlmaLinux et Ubuntu sont refusés par ce garde. |
| Ansible minimal déclaré | **2.9**, dans les métadonnées du rôle. Version des contrôles locaux enregistrés : **2.9.27**. Les autres correctifs 2.9 n’ont pas tous été exécutés. |
| Python requis sur les cibles | **3.6.8** dans le contexte retenu. Compilation et essais de composants Python sur un interpréteur extrait EL8 : **EXÉCUTÉ** antérieurement. Modules Ansible/DNF sur votre serveur : **NON EXÉCUTÉ**. |
| Contrôleur des essais enregistrés | Linux, Python **3.8.20**, Ansible **2.9.27**, Jinja2 **2.10.3** et **3.0.3**. |
| BIND de laboratoire | **9.11.36-16.el8_10.16**, binaires RPM AlmaLinux EL8, et **9.18.24**, exécutés en laboratoire Linux isolé. Ce n’est pas une recette système AlmaLinux ou RHEL. |
| NetworkManager de laboratoire | **1.46.0**, testé antérieurement en environnement isolé ; la version cible reste à qualifier pour les routes. |
| Familles d’adresses | Écoute DNS et clients déclarés : **IPv4**. Forwarders, transport sortant et données DNS : **IPv4/IPv6**. Routes gérées : **IPv4 uniquement**. |

Les versions imposées par le contexte nécessitent une politique de maintenance de votre organisation. La CI déclarée couvre aussi Ansible Core 2.13.13, 2.14.18 et 2.16.14 ; son exécution distante et ces combinaisons supplémentaires sont **NON EXÉCUTÉES** dans les preuves de cette livraison.

<a id="architecture"></a>
## Architecture et comportement DNS

| Élément | Fonctionnement réel |
|---|---|
| Site | Un primaire et un secondaire dans le même environnement et le même groupe de site. |
| ADM | Adresse SSH d’administration, distincte des deux adresses DNS. |
| FRONT | Adresse DNS destinée aux clients `bind_cloud_clients` ; seuls ces clients peuvent déclencher une route retour optionnelle. |
| BACK | Adresse DNS destinée aux clients `bind_internal_clients` et aux échanges entre pairs. Aucune route client interne ajoutée. |
| Loopback | ACL `local_loopback` = `127.0.0.1` ; zones natives incluses depuis `/etc/named.rfc1912.zones`. |
| Clients | Union calculée `bind_allowed_clients`, utilisée par l’ACL BIND `user_defined_clients`. Une liste d’entrée peut être vide, jamais les deux. |
| Autorité | Zones directes gérées sur le primaire, copies transférées sur le secondaire ; inverses générées depuis les A/AAAA. |
| Forwarding | `forward only` vers les résolveurs déclarés, sans recours à Internet si ces résolveurs échouent. |
| Hors périmètre | Racine privée `/var/named/empty-root.zone` : NXDOMAIN pour les noms non déclarés ; délégations des suffixes gérés générées par le rôle. |
| Administration | RNDC sur `127.0.0.1:953`, clé native `/etc/rndc.key`, distincte de la clé TSIG du site. |

`listen-on` et `allow-query-on` utilisent les ACL des adresses **locales** du primaire ou du secondaire. Les deux ACL BACK et leur agrégat `bind_servers_back_ip` servent aux règles entre pairs. L’agrégat FRONT inutilisé n’est pas généré.

Les autorisations de source et de destination sont indépendantes dans cette configuration sans vues. Lorsque les deux listes clientes sont renseignées, le rôle ne garantit pas qu’une source cloud ne puisse interroger que FRONT, ni qu’une source interne ne puisse interroger que BACK. Le réseau et ses politiques de sécurité doivent imposer les chemins d’arrivée. Un secondaire authentifié avec sa source BACK peut aussi transférer une zone en contactant une autre adresse d’écoute du primaire. Voir [ACL_SERVEURS.md](docs/ACL_SERVEURS.md).

Le rôle ne configure pas les adresses des interfaces, `/etc/resolv.conf`, le routage général, les forwarders ou la synchronisation horaire. Il conserve le lancement natif de `named` : `listen-on-v6 { none; };` ferme l’écoute DNS IPv6 sans imposer `named -4` ni désactiver IPv6 dans le système.

<a id="prerequis"></a>
## 🔌 Prérequis & dépendances

### Contrôleur

- Linux, Ansible et Python compatibles ; les versions réellement utilisées figurent dans la matrice.
- SSH vers les adresses ADM et élévation sur les cibles (`become: true`). Le compte du contrôleur doit pouvoir créer le répertoire privé TSIG.
- Inventaire complet du site : les variables des deux pairs sont nécessaires même avec `--limit` sur un hôte.
- Aucun rôle Galaxy ni collection externe supplémentaire requis au déploiement. Le routage utilise YAML/Jinja et les outils natifs ; aucun filtre Python de routage personnalisé ni `netaddr` nécessaire.

### Serveurs cibles

- Python accessible à Ansible, DNF et ses bindings Python, systemd, dépôts RHEL accessibles. Le rôle installe **`bind`**, **`bind-utils`**, **`policycoreutils-python-utils`**.
- **Firewalld installé et déjà actif**, avec `firewall-cmd` et `ip`. Le rôle ne l’installe pas et ne le démarre pas ; il autorise DNS immédiatement et de façon permanente dans les zones FRONT/BACK existantes.
- FRONT/BACK déjà affectées, y compris en adresses secondaires, et ADM distincte. Les adresses absentes ou ambiguës sont refusées.
- Fichiers natifs présents : `/etc/named.rfc1912.zones`, `/var/named/named.localhost`, `/var/named/named.loopback`, `/var/named/named.empty`, `/etc/crypto-policies/back-ends/bind.config`. L’include de politique cryptographique doit résoudre vers un fichier régulier.
- RNDC et `named-setup-rndc.service` natifs disponibles. Le rôle utilise ce générateur pour préparer `/etc/rndc.key` dans tous les modes DNSSEC.
- Horloges synchronisées pour TSIG ; espace disque pour zones, archives et journaux.
- **Routes FRONT activées seulement :** NetworkManager actif, `nmcli`, sortie JSON de `ip`, profil FRONT actif contenant sa passerelle IPv4 existante. FRONT/BACK/ADM doivent alors être des interfaces distinctes ; table principale et règles de routage standard, sans VRF ni règles personnalisées.
- **DNSSEC activé seulement :** `/usr/bin/python3`, ancres officielles `/etc/named.root.key` à jour et accès aux données nécessaires à la validation. Le rôle ne remplace pas les ancres de distribution.

`ansible.cfg` utilise la découverte `auto_silent`. Si nécessaire, définir `ansible_python_interpreter: /usr/bin/python3` dans l’inventaire après vérification de ce chemin et des bindings DNF. La contrainte Python 3.6.8 concerne le serveur, pas le contrôleur.

Les paquets sont demandés à l’état `present`, sans version figée dans le rôle. Pour reproduire une installation donnée, maîtriser les versions disponibles dans vos dépôts RHEL et conserver leur référence avec le dossier de changement.

### Flux réseau

| Source → destination | Flux | Usage |
|---|---|---|
| Contrôleur → ADM | SSH, port configuré dans votre environnement | Administration Ansible. |
| Clients autorisés → FRONT/BACK | UDP **et** TCP 53 | Requêtes DNS, réponses volumineuses et reprise TCP. |
| Pairs BACK | TCP 53 ; UDP 53 pour NOTIFY | Transferts et notifications. |
| DNS → forwarders | UDP/TCP sur le port déclaré, défaut 53 ; IPv4 ou IPv6 | Forwarding conditionnel. |
| Loopback → loopback | TCP 953 | RNDC local ; aucune ouverture distante. |

Firewalld est géré selon la [section sécurité](#securite). Les pare-feu externes, règles cloud et politiques d’acheminement doivent être préparés dans votre environnement. Les ACL BIND ne remplacent pas ces contrôles réseau.

<a id="inventaire"></a>
## Inventaire et environnements

Les inventaires fournis sont `inventory/dev`, `inventory/preprod`, `inventory/prod`. `ansible.cfg` sélectionne la production par défaut : **toujours préciser `-i` dans les procédures**. N’utiliser qu’un environnement par exécution.

Extrait cohérent du site `site1` de production ; l’inventaire livré contient également `site3` :

```ini
[site1]
site1-dns01
site1-dns02

[dns_primary]
site1-dns01

[dns_secondary]
site1-dns02

[dns_deployment_order]
site1-dns01
site1-dns02
```

Chaque hôte appartient à exactement un groupe de rôle ; chaque site comporte exactement un primaire et un secondaire. Placer le primaire avant son secondaire dans `dns_deployment_order`. Le playbook applique `serial: 1`, `order: inventory`, `any_errors_fatal: true`.

`inventory/prod/group_vars/all.yml` :

```yaml
---
bind_environment: prod
```

`inventory/prod/host_vars/site1-dns01.yml` :

```yaml
---
ansible_host: 10.10.1.11
bind_front_ip: 192.0.2.11
bind_back_ip: 10.20.1.11
```

`inventory/prod/host_vars/site1-dns02.yml` :

```yaml
---
ansible_host: 10.10.1.12
bind_front_ip: 192.0.2.12
bind_back_ip: 10.20.1.12
```

`inventory/prod/group_vars/site1.yml` — exemple complet avec **deux zones forwardées** et une autorité :

```yaml
---
bind_site_name: site1
bind_tsig_key_name: prod-site1-transfer
bind_tsig_algorithm: hmac-sha256
bind_tsig_secret_file: "{{ lookup('env', 'HOME') }}/.local/share/bind9-ansible/tsig/prod/site1.key"

bind_cloud_clients:
  - 198.51.100.0/24
  - 203.0.113.25
bind_internal_clients: []

bind_dnssec_active: false
bind_front_routes_enabled: false
bind_monitoring_enabled: false

bind_forward_zones:
  - domain: corp.site1.prod.example.test
    policy: only
    dnssec_validate: false
    forwarders:
      - address: 10.11.0.53
        port: 53
  - domain: apps.site1.prod.example.test
    policy: only
    dnssec_validate: false
    forwarders:
      - address: 10.11.0.54
        port: 53

bind_authoritative_zones:
  - domain: site1.prod.example.test
    ttl: 300
    serial: 2026101601
    records:
      - name: app
        type: A
        value: 192.0.2.101
```

Les forwarders doivent réellement servir le SOA des suffixes déclarés et répondre en UDP/TCP. Ces enfants forwardés sont délégués sous l’autorité parente. Si aucune autorité n’est nécessaire, définir `bind_authoritative_zones: []`. Un [exemple rendu primaire/secondaire avec deux zones forwardées](examples/deux-zones-forwardees/README.md) est livré séparément.

<a id="variables"></a>
## 🛠️ Variables du rôle / playbook

### Entrées avec valeur par défaut

Les **11 valeurs par défaut** proviennent de [defaults/main.yml](roles/bind9/defaults/main.yml).

| Variable | Type | Description | Valeur par défaut | Obligatoire ? |
|---|---|---|---|---|
| `bind_cloud_clients` | liste plate de chaînes | IP/CIDR IPv4 FRONT ; seules destinations candidates aux routes retour | `[]` | Oui si internes vides |
| `bind_internal_clients` | liste plate de chaînes | IP/CIDR IPv4 BACK ; aucune route ajoutée | `[]` | Oui si cloud vide |
| `bind_dnssec_active` | booléen ou chaîne `auto` | Validation globale : `false` → `no`, `true` → `yes`, `auto` → `auto` | `false` | Non |
| `bind_forward_zones` | liste d’objets | Suffixes forwardés et résolveurs associés | `[]` | Non |
| `bind_authoritative_zones` | liste d’objets | Autorités, serials et records ; génération des inverses | `[]` | Non |
| `bind_backup_root` | chemin absolu | Archives BIND privées et empreintes | `/var/backups/Bind9-Ansible/bind` | Non |
| `bind_monitoring_enabled` | booléen | Autorise l’installation du moniteur lorsque ses tâches sont sélectionnées | `false` | Non |
| `bind_front_route_metric` | entier | Métrique des nouvelles routes FRONT uniquement | `100` | Non |
| `bind_front_routes_enabled` | booléen YAML | Active les ajouts FRONT ; ne conditionne pas la reprise explicite | `false` | Non |
| `bind_primary_group` | chaîne | Groupe des primaires | `dns_primary` | Non |
| `bind_secondary_group` | chaîne | Groupe des secondaires | `dns_secondary` | Non |

### Entrées obligatoires sans valeur par défaut

| Variable | Type | Description | Valeur par défaut | Obligatoire ? |
|---|---|---|---|---|
| `bind_environment` | chaîne non vide | Environnement commun aux pairs | Aucune | Oui |
| `bind_site_name` | chaîne non vide | Groupe du site contenant les deux pairs | Aucune | Oui |
| `ansible_host` | adresse ADM | Destination SSH distincte de FRONT/BACK | Aucune | Oui |
| `bind_front_ip` | chaîne IPv4 unicast | Adresse FRONT locale déjà configurée | Aucune | Oui |
| `bind_back_ip` | chaîne IPv4 unicast | Adresse BACK locale déjà configurée | Aucune | Oui |
| `bind_tsig_key_name` | chaîne | Identifiant TSIG ; ne doit pas être `rndc-key` | Aucune | Oui |
| `bind_tsig_algorithm` | chaîne | Algorithme admis : `hmac-sha256` | Aucune | Oui |
| `bind_tsig_secret_file` | chemin absolu sur le contrôleur | Fichier contenant uniquement le secret Base64 persistant | Aucune | Oui |

Les options `ansible_user`, `ansible_port`, `ansible_python_interpreter` et les variables d’élévation sont les entrées standard Ansible de votre environnement ; le rôle ne leur fournit pas de valeurs spécifiques.

### Faits calculés et clients

Ne pas déclarer ni surcharger `bind_allowed_clients`, `bind_tsig_secret`, `bind_primary_host`, `bind_secondary_host`, `bind_generated_reverse_zones`, `bind_managed_zones`, les plans de routes et serials attendus : ils sont calculés ou chargés par le rôle. Une surcharge contradictoire de l’union cliente est refusée.

`bind_allowed_clients` fusionne et déduplique les listes cloud et interne. Elles contiennent des IP IPv4 ou CIDR canoniques, sans objets `name`, `type`, `cloud` ou `premise`. Une IP seule est acceptée et devient un `/32` pour les routes. Clients IPv6, préfixes `/0` et formes ambiguës sont refusés. **Les deux listes vides provoquent un échec avant les modifications du rôle**, même avec des tags partiels.

Exemple de liste interne en complément ou en remplacement de la liste cloud :

```yaml
bind_internal_clients:
  - 192.168.20.25
  - 10.30.1.0/24
```

Les anciens noms `bind_frontend_clients`, `bind_backend_clients`, `bind_dnssec_enabled`, `bind_dnssec_validation` ne sont pas le contrat de cette livraison. Retirer leurs définitions et utiliser les noms actuels. Migration : [CLIENTS_UNIQUES.md](docs/CLIENTS_UNIQUES.md).

### Modèle des zones

| Objet / champ | Contrat |
|---|---|
| Autorité : `domain` | Suffixe valide, sans wildcard ; zones natives réservées. |
| Autorité : `serial` | Obligatoire, valeur décimale positive, maximum `4294967295`. Préférer un entier YAML ; publier un serial supérieur à celui servi lorsque données ou délégations changent. |
| Autorité : `ttl`, `records` | Défauts : `300` et `[]`. |
| Record : `name`, `type`, `value` | Types : `A`, `AAAA`, `CNAME`, `TXT`, `MX`, `PTR`, `SRV`, `CAA`. Fournir le RDATA BIND complet, avec guillemets TXT et points terminaux des noms absolus lorsque requis. |
| Inverses | PTR automatiques : zones IPv4 `/24`, IPv6 `/64`. Champ `mask` par record et PTR contradictoires refusés. Serials inverses réconciliés avec l’état existant. |
| Forward : `domain`, `forwarders` | Suffixe et liste non vide ; chaque entrée a une `address` littérale IPv4/IPv6 unicast. |
| Forward : `policy` | `only`, valeur par défaut et seule politique admise. |
| Forwarder : `port` | Défaut `53` ; entier ou chaîne décimale, intervalle 1–65535. Boucles vers les adresses locales ou des pairs sur 53 refusées. |
| Forward : `dnssec_validate` | Booléen YAML, défaut `true`. `false` déclare une exception privée uniquement si DNSSEC global est actif. La chaîne `"false"` est refusée pour ce champ. |

Doublons sans distinction de casse, collisions natives, forwards imbriqués, forwards englobant une autorité et autorités masquées par un forward sont refusés. Les enfants forwardés sous une autorité sont pris en charge par délégation NS/glue générée. Les noms NS générés limitent les suffixes autoritatifs à 249 caractères et forwardés à 235. Les fichiers sont ensuite soumis aux contrôles BIND avant activation ; cela ne remplace pas la recette métier de vos données.

### DNSSEC optionnel

| `bind_dnssec_active` | Directive BIND | Composants spécifiques |
|---|---|---|
| `false` — défaut | `dnssec-validation no;` | Aucun helper, hook, service ou timer DNSSEC installé sur une cible neuve ; anciens composants gérés retirés. Ancres natives et journal d’appartenance existant conservés. |
| `true` | `dnssec-validation yes;` | Ancres natives incluses explicitement, helper d’exceptions et unités de renouvellement. |
| `auto` | `dnssec-validation auto;` | Ancres automatiques via `bindkeys-file`, même gestion des exceptions privées. |

Le booléen YAML `false` sans guillemets est valide. Les chaînes CLI exactes `true` et `false` sont aussi acceptées pour cette entrée publique ; nombres, valeurs nulles et autres chaînes sont refusés. Le contrôle utilise `type_debug`, compatible Jinja 2.10.

Avec DNSSEC actif, un suffixe forward `dnssec_validate: false` et ses descendants reçoivent une **ancre négative de confiance** (NTA) native : durée sept jours, renouvellement quotidien et au démarrage/reload. Ces suffixes n’ont pas de garantie de validation DNSSEC. Le helper utilise RNDC et `/var/lib/Bind9-Ansible/dnssec-owned.json` ; les NTA manuelles ne sont pas revendiquées. En mode global `false`, aucune exception privée n’est déployée. Détails : [DNSSEC.md](docs/DNSSEC.md).

<a id="secrets"></a>
## 🔐 TSIG, secrets et Ansible Vault

### Cycle de vie de la clé

Au début de `deploy` ou `configure`, après validation clients/DNSSEC et identification des pairs, `tsig-secret.py` s’exécute sur le **contrôleur**, sans élévation. Il crée **32 octets aléatoires encodés en Base64** si le fichier manque, sinon vérifie et réutilise la clé existante. Le répertoire est `0700`, le fichier `0600`, avec contrôle des ancêtres/propriétaires, refus des liens et fichiers spéciaux, lecture bornée et verrou de 30 secondes. La publication atomique n’expose pas de clé partiellement écrite. La clé n’est pas régénérée à chaque passage.

Le helper crée les répertoires privés manquants après contrôle de chaque ancêtre. Un répertoire existant non sûr est refusé ; le rôle ne modifie plus ses permissions avant ces contrôles.

Les deux pairs doivent utiliser le même nom et le même chemin de clé ; chaque environnement/site a son propre secret. Le rôle le charge avec `no_log` puis le rend dans `/etc/named.conf`, `root:named 0640`. Configuration et sauvegardes sont donc sensibles.

**Base64 est un encodage ; l’authentification repose sur HMAC-SHA256 et le secret aléatoire de 256 bits.** Le fichier contrôleur contient seulement le Base64. `tsig-keygen` produit un bloc de clé BIND complet : ne pas placer ce bloc dans `bind_tsig_secret_file`. La clé RNDC native a un usage distinct.

Sauvegarder le secret contrôleur dans un stockage chiffré et restreint. Sa perte, son remplacement ou un changement de chemin peuvent désynchroniser les pairs. La rotation coordonnée avec coexistence de deux clés n’est pas automatisée : préparer un changement et un retour arrière des deux serveurs. Ne pas supprimer le fichier pour « forcer » une rotation en production.

### Usage exact de Vault

Vault peut protéger `ansible_password` et `ansible_become_password`. Le rôle **ne consomme pas `vault_tsig_secret`** et ne déchiffre pas un fichier TSIG brut chiffré par Vault. Ne pas appliquer `ansible-vault encrypt` à `bind_tsig_secret_file` : son lecteur attend du Base64 dans un fichier privé.

Sur le contrôleur, sans secret dans les arguments :

```bash
mkdir -p secrets
chmod 700 secrets
ansible-vault create secrets/prod.vault.yml
```

Saisir dans l’éditeur les seules variables de connexion/élévation nécessaires, puis :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --limit site1 --tags deploy -e @secrets/prod.vault.yml --ask-vault-pass
```

Garder `secrets` et les fichiers privés hors dépôt/ZIP. Privilégier l’authentification SSH par clé selon votre politique. Ne pas afficher la clé, utiliser `dig -y`, lancer `named-checkconf -p` ou exporter des variables complètes dans un ticket : ces sorties peuvent révéler les secrets. SHA-256 contrôle l’intégrité d’une archive ; il ne la chiffre pas et ne prouve pas seul sa provenance.

<a id="deploiement"></a>
## Déploiement et critères de succès

### 1. Préparer le changement

Identifier environnement/site, IP existantes, flux autorisés, forwarders et clients de recette. Adapter l’inventaire, vérifier l’ordre des pairs, relever les SOA servis. Conserver la version précédente, les variables, secrets et sauvegardes persistantes nécessaires au retour arrière.

Sur le contrôleur :

```bash
ansible --version
ansible-inventory -i inventory/prod/hosts.ini --graph
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --syntax-check
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --list-hosts --limit site1
ansible -i inventory/prod/hosts.ini site1 -m ping
ansible -i inventory/prod/hosts.ini site1 -b -m command -a 'id -u'
```

Attendus : versions prévues, deux hôtes du site, connexion réussie et UID `0` pour la dernière commande. `ping` vérifie transport Ansible/Python, pas le DNS. Ces contrôles ne sont pas une simulation complète du déploiement.

### 2. Déployer la paire

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --limit site1 --tags deploy
```

Ajouter `--ask-become-pass` si sudo l’exige, ou utiliser les variables Vault protégées. Ne pas lancer de déploiements concurrents sur le site.

Le parcours génère/charge TSIG, valide entrées et chemins, installe les paquets, autorise DNS dans firewalld, contrôle le primaire avant son secondaire et les fichiers natifs, puis sauvegarde BIND. Il prépare les répertoires et capture les composants runtime avant le bloc protégé. Ce bloc applique les routes activées, runtime, DNSSEC, configuration, zones, labels SELinux, vérifications BIND, service et santé DNS. Les routes nouvelles sont committées après réussite des contrôles DNS et réseau.

Un changement de drop-in peut **redémarrer** `named` ; les changements ordinaires utilisent le rechargement prévu par les tâches. Prévoir une fenêtre adaptée. L’exécution est séquentielle : l’échec arrête les hôtes suivants, mais ne restaure pas les hôtes déjà déployés.

### 3. Recetter et observer

Appliquer les [tests de fonctionnement](#tests) localement, puis depuis de vrais clients autorisés/non autorisés. Vérifier journaux, SOA entre pairs, transferts, refus d’accès, firewalld runtime/permanent et routes activées. Conserver le résultat Ansible sans secret et les preuves de recette.

Critères d’arrêt : configuration rejetée, service inactif, localhost incorrect, serial inattendu, forwarding indisponible, transfert externe réussi, route ADM/BACK modifiée, rollback incomplet ou contrôle ambigu non résolu. Un retour `dig` nul ne suffit pas à prouver une réponse DNS correcte.

**Le déploiement complet en `--check` n’est pas qualifié de bout en bout.** La prévisualisation dédiée des routes est documentée plus loin. Un `preflight` isolé ne provisionne ni les utilitaires ni le fait TSIG dont il a besoin ; ne pas l’utiliser sur une cible neuve en supposant le contraire.

<a id="tags"></a>
## 🏷️ Tags disponibles

| Tag | Périmètre et précautions |
|---|---|
| `deploy` | Parcours DNS complet, firewalld inclus, routes/DNSSEC selon paramètres, sauvegarde, contrôles et rollback du bloc protégé. Moniteur à sélectionner séparément. |
| `firewall` | Ajout de `dns` manquant dans les zones FRONT/BACK, runtime et permanent ; firewalld déjà actif requis. |
| `routes` | Retours cloud IPv4 si activés, avec observation, journal et vérification ; aucun déploiement DNS. |
| `routes-recover` | Reprise explicite d’un journal interrompu ; marqué `never`, exclu du parcours normal. |
| `preflight` | Entrées, chemins et disponibilité du primaire ; suppose les outils et le secret TSIG fournis pour cette exécution. |
| `install` | Installation des trois paquets RHEL. |
| `backup` | Installation de l’outil, sauvegarde forcée et rétention ; outils requis déjà disponibles. |
| `configure` | TSIG, répertoires, runtime, DNSSEC conditionnel, configuration, zones et SELinux ; pas de transaction complète isolément. |
| `zones`, `records` | Données de zones ; ne remplace pas `deploy`. |
| `validate` | Fichiers loopback natifs, configuration et zones BIND. |
| `services` | Activation et application des changements de service. |
| `health` | Loopback UDP/TCP, SOA autoritatifs avec serial attendu, SOA forwardés UDP/TCP ; dépend des faits/outils du déploiement. |
| `monitor` | Installation/activation du moniteur si `bind_monitoring_enabled: true`. |
| `always` | Validation DNSSEC/clients, découverte des pairs, calcul des zones et type d’activation des routes, même avec un tag ciblé. |

**Procédure normale : `--tags deploy`.** Les tags techniques partiels ne garantissent pas ensemble sauvegarde, activation, faits nécessaires et santé. Ne pas utiliser `--skip-tags always`. Sans `--tags`, le moniteur est aussi sélectionné si son booléen est vrai ; `routes-recover` reste exclu. Avec `--tags deploy`, ajouter explicitement `monitor` pour installer le moniteur.

<a id="operations"></a>
## Opérations courantes

### Modifier clients, zones ou records

1. Modifier les variables du site ; garder au moins une liste cliente non vide.
2. Relever les SOA et incrémenter le serial de toute autorité dont données ou délégations changent.
3. Exécuter les contrôles préalables puis `--tags deploy` sur la paire, primaire avant secondaire.
4. Vérifier données, réplication et refus d’accès depuis les segments concernés.

Les éditions directes des fichiers générés créent une dérive qui peut être écrasée au prochain passage. Retirer un client ne supprime pas sa route FRONT déjà committée. Ne pas supprimer globalement l’autorisation firewalld `dns` pour révoquer un seul client : modifier sa liste et les contrôles réseau adaptés.

### Activer ou désactiver DNSSEC

Définir `bind_dnssec_active: false`, `true` ou `auto`, puis déployer complètement. Avant activation, qualifier les ancres et chaînes de vos upstreams ; marquer uniquement les suffixes privés non signés avec le booléen `dnssec_validate: false`. En mode actif :

```bash
sudo systemctl status named-private-dnssec.timer --no-pager
sudo journalctl -u named-private-dnssec.service --since '-24 hours' --no-pager
sudo rndc nta -dump
```

L’absence des unités privées est attendue après nettoyage en mode désactivé. Le retour à `false` retire les composants gérés sans effacer les ancres ni le journal d’appartenance. Voir [DNSSEC.md](docs/DNSSEC.md).

### Gérer les routes de retour FRONT

La fonction est **désactivée par défaut**. Elle découvre l’interface FRONT, le profil NetworkManager actif par UUID et sa passerelle IPv4. Elle ajoute seulement les routes manquantes vers `bind_cloud_clients`, dans le profil et le noyau, sans réactivation de connexion, changement de route par défaut ou écriture BACK/ADM. Les clients directement connectés n’exigent pas de route par passerelle ; leur chemin est toutefois vérifié.

Prévisualiser puis appliquer sur un hôte :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --limit site1-dns01 --tags routes --check -v \
  -e '{"bind_front_routes_enabled": true}'

ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --limit site1-dns01 --tags routes \
  -e '{"bind_front_routes_enabled": true}'
```

JSON préserve ici le booléen. `-e bind_front_routes_enabled=true` produit une chaîne avec Ansible 2.9 et n’est pas la forme attendue. Maintenir `bind_front_routes_enabled: true` dans les variables si cette gestion doit rester activée.

Les routes préexistantes gardent leur identité et leur métrique. Une route contradictoire ou un profil ambigu provoque un refus ; aucune ancienne route n’est supprimée pour imposer une nouvelle configuration. Le journal privé est `/var/lib/Bind9-Ansible/front-routes.lock/plan.json`. En cas d’échec pris en charge, seuls les ajouts de l’opération sont retirés ; une interruption brutale laisse le journal.

Le profil, la passerelle, la table principale et les règles sont relus sous verrou avant le premier ajout. Les trois règles standard doivent être dépourvues de sélecteurs supplémentaires ; VRF, marques, destinations conditionnelles et autres politiques sont refusées. Le rollback valide les deux magasins avant toute suppression et refuse les attributs actifs inattendus, les changements de métrique, de passerelle ou de règles. La reprise protège aussi les adresses ADM/FRONT/BACK actuelles et la source SSH actuelle. Un conflit conserve le journal ; aucune atomicité n’est garantie face à un autre outil modifiant le réseau pendant l’opération.

Si `Reject invalid IPv4 observations before any routing write` échoue alors que les passerelles sont définies, lire **la valeur refusée et son origine** : découverte des interfaces/inventaire, routes du profil, plan, noyau ou journal. `ansible_host` doit notamment contenir l’IPv4 ADM littérale pour cette fonction, pas un nom DNS. Le contrôle affiche le type et une représentation échappée des entrées invalides ; il ne les ignore pas et ne choisit aucune autre passerelle automatiquement. Sans la valeur exacte du message initial, la cause de cet incident particulier n’est pas établie.

Après avoir vérifié qu’aucun déploiement n’est actif et inspecté journal, profil et routes, reprendre l’hôte concerné :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --limit site1-dns01 --tags routes-recover
```

Ne pas effacer le verrou pour contourner les contrôles. Une route ou un profil changé depuis l’interruption peut bloquer la reprise, qui conserve alors le journal. Une restauration BIND manuelle ne retire pas les routes committées. Détails : [FRONT_ROUTES.md](docs/FRONT_ROUTES.md).

<a id="securite"></a>
## 🛡️ Sécurité et pare-feu

- Requêtes et cache limités aux ACL prévues ; pairs BACK et clé de transfert n’ouvrent pas la récursion. Loopback reste une ACL technique distincte.
- Mises à jour dynamiques interdites ; transferts globalement refusés. Pour chaque autorité primaire gérée, le transfert exige **source BACK secondaire et TSIG**.
- `notify no` global évite les notifications implicites ; les autorités primaires gérées utilisent `notify explicit` et `also-notify` vers le secondaire BACK avec la clé du site. Leur réplication reste active.
- Version BIND masquée, réponses minimales, politique cryptographique RHEL native conservée ; comportement FIPS à qualifier.
- Chemins gérés contrôlés contre liens symboliques et objets inattendus ; sauvegardes privées ; fichiers localhost sous responsabilité du paquet.

TSIG authentifie les messages concernés ; il ne chiffre pas les échanges DNS. Le projet n’intègre pas de service DNS over TLS ou DNS over HTTPS.

Le rôle installe aussi `/etc/profile.d/bind9-sbin-path.sh` pour ajouter `/usr/local/sbin` au PATH des futures sessions de connexion si ce chemin manque. Les procédures de ce guide utilisent les chemins absolus des outils installés et ne dépendent pas d’une reconnexion.

### Firewalld : immédiatement et de façon permanente

Le service autorisé est **`dns`**, normalement UDP/TCP 53. `named` est le nom systemd. Le rôle découvre séparément les zones runtime/permanentes des interfaces FRONT/BACK, utilise la zone par défaut si nécessaire, ajoute les seuls services manquants puis vérifie les deux états. Aucun reload global, changement d’affectation ou réécriture NetworkManager. RNDC 953 n’est pas ouvert.

Rejouer cette seule opération :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --limit site1 --tags firewall
```

L’autorisation porte sur toute la zone. Si elle est partagée avec ADM, l’autorisation s’y applique aussi ; BIND conserve ses restrictions d’écoute et d’accès. Zones par source, ipsets, rich rules, politiques additionnelles et service `dns` personnalisé exigent une recette spécifique ; le rôle ne les réorganise pas.

Ces autorisations restent après rollback DNS. Un échec entre deux ajouts peut laisser un état partiel, signalé explicitement ; rejouer `firewall` complète les éléments manquants. Voir [FIREWALLD.md](docs/FIREWALLD.md).

### SELinux

Lorsqu’il est actif, le rôle applique les contextes des chemins BIND et assure une règle persistante `/var/log/named(/.*)?` de type `named_log_t`. Il ajoute/corrige cette règle, applique `restorecon` et vérifie le label du répertoire et des trois journaux. Il ne désactive pas SELinux.

```bash
getenforce
sudo ls -Zd /var/log/named /var/log/named/default.log \
  /var/log/named/security.log /var/log/named/xfer.log
sudo semanage fcontext -l | grep -F '/var/log/named'
```

Attendu avec SELinux actif : `named_log_t` sur les quatre objets. Le confinement réel `named_t` reste à tester sur RHEL. Ne pas contourner un refus par `chmod 777`, désactivation SELinux ou règle générée sans analyse.

<a id="observabilite"></a>
## Journaux et supervision

### Journaux BIND

| Fichier | Contenu | Rotation BIND | Volume nominal, actif inclus |
|---|---|---|---|
| `/var/log/named/default.log` | Activité générale, configuration et erreurs | 5 archives de 10 MiB | Environ 60 MiB |
| `/var/log/named/security.log` | Sécurité, refus et catégorie DNSSEC | 5 archives de 10 MiB | Environ 60 MiB |
| `/var/log/named/xfer.log` | Transferts entrants/sortants | 3 archives de 5 MiB | Environ 20 MiB |

Répertoire `named:named 0750`, fichiers préparés en `0640` sans tronquer leurs données. BIND assure la rotation par taille ; `versions` compte les archives en plus du fichier actif. Total nominal : **environ 140 MiB**, avec dépassement ponctuel possible. **Aucune durée d’un mois ou rétention quotidienne n’est garantie.** Aucun logrotate supplémentaire installé.

Après rotation, les droits du nouveau fichier suivent le démon natif ; un fichier `0644` a été observé en laboratoire. La protection du répertoire `0750` est donc essentielle. Une exigence de mode strict à chaque rotation ou de rétention calendaire demande une politique complémentaire à qualifier ; le rôle ne change pas l’UMask globale du service.

Les requêtes unitaires sont désactivées (`querylog no`, catégorie `queries` vers `null`). Des refus peuvent toutefois apparaître dans le canal sécurité. Les messages courants BIND sont dans ces fichiers ; systemd reste utile pour l’unité, les erreurs précoces et les services auxiliaires.

```bash
sudo tail -n 100 /var/log/named/default.log
sudo tail -n 100 /var/log/named/security.log
sudo tail -n 100 /var/log/named/xfer.log
sudo journalctl -u named --since '-30 minutes' --no-pager
sudo du -sh /var/log/named /var/backups/Bind9-Ansible/bind
```

Les traces peuvent contenir des IP et noms DNS : restreindre l’accès et définir export/rétention selon votre politique. Elles sont conservées lors du rollback et exclues des backups BIND. Détails : [JOURNALISATION.md](docs/JOURNALISATION.md). `ansible.cfg` ne définit pas de `log_path` : conserver les comptes rendus Ansible par votre mécanisme protégé, sans exposer de secrets.

### Moniteur périodique optionnel

Définir `bind_monitoring_enabled: true`, puis :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --limit site1 --tags deploy,monitor
```

`named-healthcheck.timer` démarre après environ deux minutes de boot, puis contrôle toutes les minutes, précision dix secondes. Il vérifie `named` actif, localhost A/PTR en UDP/TCP et l’autorité des SOA gérés sur BACK local. Il **ne compare pas les serials entre pairs et ne sonde pas les forwarders** ; ces vérifications relèvent du déploiement ou d’une supervision externe.

```bash
sudo systemctl status named-healthcheck.timer --no-pager
sudo journalctl -u named-healthcheck.service --since '-1 hour' --no-pager
sudo /usr/local/sbin/named-healthcheck 10.20.1.11 site1.prod.example.test
```

La dernière commande concerne le primaire de l’exemple, après installation du moniteur. Retour attendu `0`, message `OK` ; contrôle critique `2`. Aucune notification externe, collecte centrale ou tableau de bord fourni. Faire remonter les échecs et surveiller aussi espace disque, âge du dernier backup, forwarders, écarts SOA et timer DNSSEC actif.

Passer le booléen du moniteur à `false` empêche ses tâches de s’exécuter ; cela **ne désinstalle pas** un timer déjà créé. Pour son retrait, arrêter/désactiver explicitement `named-healthcheck.timer` selon votre procédure de changement.

<a id="tests"></a>
## Tests de fonctionnement

Les commandes utilisent l’exemple `site1` et doivent suivre votre inventaire. Examiner **statut DNS, drapeaux et données** ; timeout et sortie vide ne prouvent jamais un refus de sécurité.

### Sur chaque serveur

```bash
sudo named-checkconf -z /etc/named.conf
sudo systemctl is-active named
sudo rndc status
sudo ss -lnutp

dig -b 127.0.0.1 @127.0.0.1 localhost A +norecurse +notcp +time=3 +tries=1
dig -b 127.0.0.1 @127.0.0.1 localhost A +norecurse +tcp +time=3 +tries=1
dig -b 127.0.0.1 @127.0.0.1 -x 127.0.0.1 +norecurse +notcp +time=3 +tries=1
dig -b 127.0.0.1 @127.0.0.1 -x 127.0.0.1 +norecurse +tcp +time=3 +tries=1

dig @127.0.0.1 corp.site1.prod.example.test SOA +notcp +time=3 +tries=1
dig @127.0.0.1 corp.site1.prod.example.test SOA +tcp +time=3 +tries=1
dig @127.0.0.1 apps.site1.prod.example.test SOA +notcp +time=3 +tries=1
dig @127.0.0.1 apps.site1.prod.example.test SOA +tcp +time=3 +tries=1
dig @127.0.0.1 nom-hors-perimetre.invalid A +time=3 +tries=1
```

Attendus : configuration acceptée, zones chargées, service `active`, RNDC opérationnel. Écoute DNS IPv4 loopback/FRONT/BACK seulement, RNDC sur loopback seulement. Localhost : `NOERROR`, drapeau `aa`, A `127.0.0.1`, PTR `localhost.`. Chaque forward doit retourner le SOA de son suffixe avec `NOERROR` ; `aa` n’est pas exigé pour ces réponses récursives. Le nom hors périmètre doit retourner `NXDOMAIN`. Une résolution Internet générale n’est pas un critère de succès de cette architecture.

Sur le primaire de l’exemple :

```bash
sudo named-checkzone site1.prod.example.test \
  /var/named/zones/site1.prod.example.test.zone
```

Le parcours `deploy` contrôle aussi les inverses et compare les SOA aux serials réellement réconciliés du primaire.

### Depuis les clients

Exemple FRONT, puis comparaison BACK depuis un poste autorisé à joindre ces adresses :

```bash
dig @192.0.2.11 app.site1.prod.example.test A +notcp +time=3 +tries=1
dig @192.0.2.12 app.site1.prod.example.test A +tcp +time=3 +tries=1
dig @10.20.1.11 site1.prod.example.test SOA +norecurse +time=3 +tries=1
dig @10.20.1.12 site1.prod.example.test SOA +norecurse +time=3 +tries=1
```

Attendus : `NOERROR`, `aa` pour les données autoritatives, A `192.0.2.101` et même serial attendu entre pairs. Tester aussi les inverses, chaque type utilisé et chaque forward depuis les segments clients. La récursion sur BACK exige une liste interne déclarée ; un SOA autoritatif ne prouve pas l’accès au cache récursif.

Depuis une source non autorisée pouvant atteindre le service, obtenir un refus DNS explicite ou le blocage prévu par la politique réseau ; distinguer ces niveaux de preuve. Tester UDP et TCP, et l’absence d’écoute DNS sur ADM.

### Réplication et refus AXFR

Comparer les SOA BACK, lire `xfer.log`, puis réaliser le transfert autorisé avec source BACK secondaire et fichier de clé BIND protégé. Ne jamais copier le secret dans la ligne de commande.

L’outil ci-dessous teste depuis un **client FRONT** un AXFR non signé puis signé. Il exige `dig`, une IP source réellement portée par ce client et une clé au format BIND :

```text
bind-configtool check-front-axfr FRONT_DNS_IP ZONE CLIENT_SOURCE_IP ROOT_ONLY_TSIG_KEYFILE
```

Les arguments représentent vos valeurs réelles ; le dernier fichier contient un bloc de clé BIND, pas le fichier Base64 brut du contrôleur. L’exécutable installé sur le serveur est réservé à root ; préparer outil et clé sur un client de recette contrôlé et retirer les copies privées selon votre politique.

| Code | Interprétation |
|---|---|
| `0` | Refus DNS explicite `REFUSED` ou `NOTAUTH` dans les deux essais. |
| `1` | Zone transférée : anomalie bloquante pour ce client externe. |
| `2` | Usage incorrect ou contrôle incomplet/ambigu, erreur réseau ou TSIG ; preuve complémentaire requise. |

`Transfer failed` seul, `BADKEY`/`BADSIG`/`BADTIME` et timeout ne prouvent pas le refus attendu. Certains anciens `dig` n’affichent pas le RCODE AXFR ; l’outil reste alors inconclusif. Le secondaire BACK authentifié est autorisé même vers FRONT : ne pas le prendre comme client externe pour ce test.

### Firewalld et routes

```bash
sudo firewall-cmd --state
sudo firewall-cmd --get-active-zones
sudo firewall-cmd --permanent --list-all-zones
ip -4 route show table main
```

Pour chaque zone FRONT/BACK relevée, passer son nom réel à `--zone` et interroger `--query-service=dns` en runtime puis avec `--permanent` : attendu `yes`, retour `0`. Si les routes sont activées, vérifier leur profil et chemin effectif. Exemple sur le primaire :

```bash
ip -4 route get 198.51.100.10 from 192.0.2.11
```

Attendu : sortie FRONT et passerelle prévue, sans changement des chemins ADM/BACK ou de la route par défaut. Vérifier la permanence après redémarrage en recette. Procédures complémentaires : [TESTS_FONCTIONNELS.md](docs/TESTS_FONCTIONNELS.md), [VALIDATION_RHEL8.md](docs/VALIDATION_RHEL8.md).

<a id="reprise"></a>
## Sauvegarde, restauration et rollback

### Périmètre et rétention

Chaque `deploy` crée une archive distincte **avant modification de la configuration BIND et des zones**. Les paquets, autorisations firewalld et certaines préparations ont déjà été traités ; il ne s’agit pas d’un instantané système avant toute action.

`/usr/local/sbin/bind-configtool` utilise un verrou, un répertoire `0700`, des archives `0600`, des noms UTC uniques et un fichier SHA-256 associé. Rétention : **30 jours**, évaluée à chaque nouvelle sauvegarde, sans plafond en nombre. Aucun backup quotidien ni purge périodique supplémentaire n’est planifié automatiquement.

| Élément | Couverture |
|---|---|
| `/etc/named.conf`, `/etc/named` si présent, `/var/named` | Inclus avec propriétaires, ACL, attributs étendus et métadonnées SELinux via tar. Le TSIG rendu est inclus. |
| Secret TSIG contrôleur, inventaire, version du projet | À sauvegarder séparément dans un stockage protégé. |
| Drop-ins, helper et unités DNSSEC | Absents du backup BIND ; capture en mémoire pour le rescue de `deploy`, sauvegarde persistante à prévoir. |
| `/etc/rndc.key`, `/etc/named.root.key`, fichiers natifs hors périmètre | Absents ; préserver les composants de distribution et une procédure système cohérente. |
| Journaux, base SELinux, firewalld, NetworkManager et routes | Non inclus ; traces/règles de contexte et autorisations DNS conservées par le rollback. |

Sur le serveur, créer puis lister :

```bash
sudo /usr/local/sbin/bind-configtool backup --root /var/backups/Bind9-Ansible/bind
sudo /usr/local/sbin/bind-configtool list --root /var/backups/Bind9-Ansible/bind
```

Une racine existante doit déjà être root `0700`, sans ancêtre symbolique ou non fiable ; l’outil refuse de changer les droits d’un répertoire général fourni par erreur. Les archives et empreintes importées doivent être privées, root, régulières, avec un seul lien. Les sources/destinations BIND ne doivent contenir ni lien physique/symbolique ni fichier spécial. Les empreintes doivent décrire une seule archive et les TAR utiliser des chemins canoniques sans doublon. Préparation et migration : [DURCISSEMENT_SECURITE.md](docs/DURCISSEMENT_SECURITE.md).

`backup` annonce `BACKUP_CREATED` suivi du chemin ; `list` contrôle les empreintes et affiche les noms, plus récents d’abord. Adapter `--root` à `bind_backup_root` si modifié. `backup --force` est accepté ; chaque appel crée déjà une archive. Un retour non nul impose de traiter l’erreur. Ces commandes ne démarrent ni n’arrêtent `named`.

Exporter archives et empreintes hors serveur dans un stockage chiffré. Qualifier les backups à chaud, notamment les journaux des secondaires. Définir et mesurer **RPO** (perte maximale admissible) et **RTO** (délai de rétablissement) ; aucune valeur n’est garantie par le rôle.

### Restauration manuelle de la même version

Impact : remplacement des fichiers BIND et interruption du service. Préconditions : archive/empreinte contrôlées, runtime cohérent, sauvegarde récente exportée, pair de secours fonctionnel, accès ADM et opérateur autorisé. Un retour de version ou de mode DNSSEC exige aussi les sauvegardes runtime persistantes ; la commande BIND ne les restaure pas.

La procédure Bash demande un **nom exact retourné par `list`** et sauvegarde l’état courant avant d’arrêter les services. Exécuter sur un hôte à la fois et adapter la racine si nécessaire :

```bash
(
  set -euo pipefail
  backup_root=/var/backups/Bind9-Ansible/bind
  sudo /usr/local/sbin/bind-configtool list --root "$backup_root"
  read -r -p 'Nom exact de l’archive à restaurer : ' archive_name
  [[ $archive_name =~ ^named-config-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\.tar\.gz$ ]]
  sudo test -f "$backup_root/$archive_name"
  sudo /usr/local/sbin/bind-configtool backup --root "$backup_root"
  # Le nouveau backup applique la rétention ; vérifier encore la sélection.
  sudo test -f "$backup_root/$archive_name"
  sudo /usr/local/sbin/bind-configtool list --root "$backup_root"

  renewal_was_active=false
  if systemctl is-active --quiet named-private-dnssec.timer; then
    renewal_was_active=true
  fi
  for unit in named-private-dnssec.timer named-private-dnssec.service; do
    if systemctl cat "$unit" >/dev/null 2>&1; then
      sudo systemctl stop "$unit"
    fi
  done
  sudo systemctl stop named
  sudo /usr/local/sbin/bind-configtool restore \
    --root "$backup_root" --archive "$archive_name"
  sudo named-checkconf -z /etc/named.conf
  sudo systemctl start named
  if "$renewal_was_active"; then
    sudo systemctl start named-private-dnssec.timer
  fi
  sudo systemctl is-active named
)
```

Une erreur arrête cette procédure ; le service peut rester arrêté et exiger une intervention. Ne pas redémarrer une configuration/runtime incohérents. Rejouer ensuite localhost, zones, forwarding et SOA, contrôler les logs et labels. Une erreur de relabellisation est désormais bloquante ; contrôler les labels avant remise en service. Si la récupération de sécurité échoue, son archive privée est conservée et son chemin signalé.

`restore` vérifie empreinte, chemins et types des membres, extrait puis exécute `named-checkconf -z`. Il retire uniquement les fichiers de zones marqués par le projet et absents de l’archive ; les non-marqués sont préservés. Si la restauration échoue, il tente de réextraire une archive de sécurité de l’état immédiatement précédent ; cela ne garantit ni retour système complet ni remise en service.

### Rollback automatique et limites

Un échec dans le bloc DNS protégé du déploiement complet retire les ajouts de routes encore possédés par l’opération, puis restaure BIND sur l’hôte courant. Le rescue suspend le renouvellement privé, arrête `named`, restaure les fichiers runtime capturés et l’archive exacte, redémarre BIND puis reprend le timer s’il était actif. L’exécution reste en échec pour investigation.

La capture runtime couvre six chemins : `bind9-runtime.conf`, ancien `bind9-dnssec.conf`, `bind9-z-dnssec.conf` sous `/etc/systemd/system/named.service.d`, `/usr/local/sbin/bind-private-dnssec` et les deux unités `/etc/systemd/system/named-private-dnssec.service` / `.timer`. Elle conserve droits et état antérieur du timer, mais reste **en mémoire du contrôleur**. Arrêt brutal ou perte SSH ne garantissent pas l’exécution du rescue.

Le rollback ne couvre pas les hôtes déjà déployés, les paquets, toutes les actions précédant le bloc protégé, les autorisations DNS firewalld ou les logs. Une restauration manuelle ne retire pas les routes committées. Un rollback réseau incomplet conserve le journal et signale l’erreur ; traiter cette reprise avant un nouveau changement. Voir [OPERATIONS.md](docs/OPERATIONS.md) et [FRONT_ROUTES.md](docs/FRONT_ROUTES.md).

<a id="diagnostic"></a>
## Diagnostic des incidents

Relever l’hôte, l’heure, la version, la dernière archive, la tâche Ansible et son message exact. Conserver les preuves utiles sans clé ni configuration complète contenant des secrets. Utiliser ADM et cibler le serveur concerné.

| Symptôme | Contrôle et action |
|---|---|
| `named` ne démarre pas | `named-checkconf -z`, `systemctl status named`, journal systemd puis `default.log`. Vérifier fichiers natifs, RNDC, crypto-policy, droits et labels. |
| Vérification localhost échouée | Refaire les quatre requêtes A/PTR UDP/TCP avec source `127.0.0.1` ; vérifier statut, `aa`, données, include et fichiers officiels. Ne pas les remplacer par des zones artisanales. |
| SOA secondaire ancien/absent | Comparer SOA BACK et serial primaire ; vérifier TCP 53, horloges, clé partagée et `xfer.log`. Ne pas ouvrir globalement les transferts. |
| `SERVFAIL` sur un forward | Interroger directement son forwarder UDP/TCP, vérifier port et route ; avec DNSSEC, lire `security.log`, ancres et NTA attendues. Ne pas élargir une exception à tous les domaines. |
| `NXDOMAIN` hors périmètre | Attendu par la racine privée ; déterminer si le suffixe devait être déclaré. |
| Client reçoit `REFUSED` | Vérifier source réelle, listes, destination et autorisations récursives. Une traduction réseau peut modifier la source vue par BIND. |
| Timeout FRONT uniquement | Contrôler UDP/TCP 53, firewalld et pare-feu externes, puis retour avec source FRONT. Les clients internes ne déclenchent aucune route FRONT. |
| Plan de routes vide ou sortie invalide | Conserver l’erreur ; vérifier IP, profil actif, passerelle et prérequis. Les observations vides/malformées doivent échouer explicitement. |
| Verrou de routes présent | Vérifier absence d’exécution active, inspecter journal/réseau puis `routes-recover`. Ne pas supprimer le verrou à l’aveugle. |
| Journaux absents | Vérifier répertoire, droits `named`, labels et disque ; lire systemd. Sur RHEL avec l’outil disponible : `sudo ausearch -m AVC -ts recent`. |
| Firewalld inactif/partiel | Rétablir service et zones selon votre politique ; rejouer `firewall`, vérifier runtime et permanent. Le rôle ne démarre pas un pare-feu arrêté. |
| Assertion DNSSEC/routes | Utiliser les noms publics et types attendus ; préférer JSON pour les booléens `-e`. Ne pas retirer les assertions. |

Si la cause reste inconnue, arrêter le changement, maintenir le pair sain et appliquer une restauration préparée. Confirmer le service depuis les réseaux clients : un test local réussi ne clôt pas un incident de connectivité.

<a id="validation"></a>
## Validation, tests du projet et limites

### Preuves disponibles

**EXÉCUTÉ en laboratoire pour cette révision :** contrôles des sources et inventaires, suites Ansible 2.9/Jinja 2.10 et 3.0, tests de non-régression des secrets/archives/chemins, workflows de restauration isolée, contrôles FRONT et essais DNS avec BIND EL8 et 9.18. Les résultats exacts et le périmètre de chaque preuve sont dans [DELIVERY.json](DELIVERY.json) et [DURCISSEMENT_SECURITE.md](docs/DURCISSEMENT_SECURITE.md).

Les tests de restauration utilisent les vrais tar, gzip, fichiers et empreintes, dans des espaces de montage privés ; systemd, BIND et SELinux y sont des doubles. Les tests DNS utilisent des démons réels dans des espaces réseau isolés. Les assertions d’inventaire emploient des facts RHEL simulés. Aucune de ces preuves ne vaut déploiement complet sur vos serveurs.

Les workflows de journalisation/SELinux et firewalld de la livraison antérieure restent des preuves héritées lorsque non rejoués ; ils ne sont pas présentés comme exécutés à nouveau. La couverture concerne les modules Python mesurés, dont le générateur TSIG, avec le seuil de 98 % conservé. Elle ne couvre pas exhaustivement YAML, le réseau ou le système. Les 109 configurations et 282 contrôles DNS par moteur ne prouvent pas toutes les combinaisons d’inventaire ou de pannes.

### Rejouer les contrôles

Sur Linux de recette, avec les dépendances de [.github/workflows/ci.yml](.github/workflows/ci.yml) et un environnement Python isolé adapté :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --syntax-check
python -m pytest -q tests \
  --ignore=tests/test_front_routes_ansible.py \
  --ignore=tests/test_dnssec_workflows.py \
  --ignore=tests/test_logging_ansible.py \
  --ignore=tests/test_firewall_ansible.py \
  --ignore=tests/test_configtool_workflows.py \
  --ignore=tests/test_front_route_hardening_ansible.py
python tests/integration_preflight.py
```

Les workflows privilégiés exigent un **laboratoire jetable**, root et leurs prérequis. Certains manipulent des chemins système ou substituent des commandes ; ne pas exécuter ces suites sous root sur un DNS de production. La CI décrit les suites routes/DNSSEC et le seuil de couverture.

Les lanceurs ci-dessous isolent leur réseau avant les essais. Prérequis : Linux root, `unshare`, `ip`, BIND, Ansible et dépendances Python ; le test de routes requiert aussi NetworkManager et D-Bus. Aucun paquet n’est installé par ces lanceurs. Préparer un répertoire contenant `dig`, `rndc`, `named-checkconf`, `named-checkzone`, `dnssec-keygen`, `dnssec-signzone`, et un répertoire de fichiers natifs contenant `named.localhost`, `named.loopback`, `named.empty`, `named.rfc1912.zones`, `named.root.key`, `bind.config`, issus d’une distribution et d’une politique compatibles avec le BIND testé. Ces fichiers ne sont pas fournis comme fixtures dans le ZIP.

Les deux essais BIND exigent les trois arguments explicites suivants ; saisir les chemins réels de votre laboratoire :

```bash
read -r -p 'Répertoire des outils BIND : ' test_bin_dir
read -r -p 'Répertoire des fichiers natifs : ' test_native_dir
read -r -p 'Chemin absolu du binaire named : ' test_named
sudo --preserve-env=PATH env "PYTHON=$(command -v python)" \
  bash tests/run_integration_loopback.sh \
  --bin-dir "$test_bin_dir" --native-dir "$test_native_dir" --named "$test_named"
sudo --preserve-env=PATH env "PYTHON=$(command -v python)" \
  bash tests/run_integration_logging.sh \
  --bin-dir "$test_bin_dir" --native-dir "$test_native_dir" --named "$test_named"
sudo --preserve-env=PATH env "PYTHON=$(command -v python)" \
  bash tests/run_integration_front_routes.sh
```

### Contrôles avant production

Raison commune des contrôles non exécutés : aucune paire de vos serveurs RHEL 8 accessible dans cet environnement. Prérequis : paire de recette représentative, inventaire réel, clients autorisés/non autorisés et accès ADM de secours.

| Statut | Procédure | Attendu et risque restant |
|---|---|---|
| **NON EXÉCUTÉ** | Exécuter `deploy`, recette DNS puis deuxième `deploy` avec les vrais Python/DNF et services RHEL. | Données/réponses stables, aucune mutation imprévue ; nouveau backup attendu à chaque passage. Écarts possibles de paquets, versions et état système. |
| **NON EXÉCUTÉ** | Tester SELinux enforcing, systemd, firewalld, trafic des deux segments puis reboot de recette, un hôte à la fois. | Écritures `named_t` autorisées, labels/services/timers corrects, DNS/routes persistants, ADM/BACK préservés. Restent politiques locales et comportements de boot. |
| **NON EXÉCUTÉ** | Tester chaînes DNSSEC réelles, suffixes privés, renouvellement NTA sur plus de sept jours, ancres RFC5011 et FIPS si utilisé. | Validation selon politique, exceptions limitées et renouvelées. Restent expirations, chaînes upstream et crypto-policy réelle. |
| **NON EXÉCUTÉ** | Provoquer en recette un échec protégé, une perte de contrôleur et une restauration manuelle avec backups persistants. | Cohérence données/runtime, retrait des routes de l’opération, pair sain disponible, RPO/RTO mesurés. Risque de reprise partielle et perte d’état mémoire. |
| **NON EXÉCUTÉ** | Mesurer charge représentative, latence, débit, disque, rotation et export des logs ; tester pression disque. | Objectifs/alertes d’exploitation respectés. Performance et durée de conservation ne sont actuellement pas démontrées. |
| **NON EXÉCUTÉ** | Exécuter la matrice CI dans votre dépôt et conserver les résultats. | Contrôles de chaque combinaison réussis ; présence du workflow seule insuffisante. |

Détails : [VALIDATION_RHEL8.md](docs/VALIDATION_RHEL8.md), [VALIDATION_ROUTES_NATIVES.md](docs/VALIDATION_ROUTES_NATIVES.md), [JOURNALISATION.md](docs/JOURNALISATION.md), [FIREWALLD.md](docs/FIREWALLD.md). **La qualification opérationnelle reste NON TERMINÉE tant que les contrôles applicables à votre environnement ne sont pas exécutés et leurs écarts résolus.**

<a id="livraison"></a>
## Livraison, traçabilité et documentation

```text
Bind9-Ansible/
├── ansible.cfg
├── inventory/                 # dev, preprod, prod : groupes, sites, hôtes
├── playbooks/bind9-deploy.yml  # point d’entrée séquentiel
├── roles/bind9/               # tâches, templates, scripts, filtres, defaults
├── examples/                  # rendus primaire/secondaire à deux forwards
├── tests/                     # tests, workflows, intégration
├── docs/                      # procédures et rapports
├── .github/workflows/ci.yml
├── DELIVERY.json              # provenance, périmètre, preuves
├── SOURCE-MANIFEST.sha256     # empreintes des fichiers livrés
└── README.md
```

Cette révision part de `Bind9-Ansible-readme-exploitation.zip`, SHA-256 `918b49f30fa9cbcb18842bf9862e3c82ff295fee5d2ce6b3a81b0b877cc75262`. Elle apporte les corrections de sécurité décrites dans [DURCISSEMENT_SECURITE.md](docs/DURCISSEMENT_SECURITE.md) ; les inventaires, le template BIND et les contrats publics sont conservés. Les archives précédentes restent intactes.

Le ZIP source possède une empreinte SHA-256 externe. Sur le contrôleur Linux, depuis le dossier contenant ZIP et empreinte :

```bash
sha256sum -c Bind9-Ansible-durci-securite-routes.zip.sha256
```

Après extraction, depuis la racine du projet :

```bash
sha256sum -c SOURCE-MANIFEST.sha256
```

Attendu : toutes les entrées correspondent. Le manifeste couvre les fichiers sauf lui-même. Secrets générés, caches, environnements virtuels, journaux et artefacts de laboratoire sont exclus ; les données de test synthétiques restent dans les tests. Les empreintes ne remplacent pas un canal de distribution authentifié.

| Besoin | Document |
|---|---|
| Exploitation, migration et runtime | [OPERATIONS.md](docs/OPERATIONS.md) |
| Recette DNS et RHEL | [TESTS_FONCTIONNELS.md](docs/TESTS_FONCTIONNELS.md), [VALIDATION_RHEL8.md](docs/VALIDATION_RHEL8.md) |
| Clients et ACL | [CLIENTS_UNIQUES.md](docs/CLIENTS_UNIQUES.md), [ACL_SERVEURS.md](docs/ACL_SERVEURS.md) |
| Transport et zones natives | [TRANSPORT_DNS.md](docs/TRANSPORT_DNS.md), [LOOPBACK.md](docs/LOOPBACK.md) |
| Routes et reprise | [FRONT_ROUTES.md](docs/FRONT_ROUTES.md), [VALIDATION_ROUTES_NATIVES.md](docs/VALIDATION_ROUTES_NATIVES.md) |
| DNSSEC et types | [DNSSEC.md](docs/DNSSEC.md), [DURCISSEMENT_DNSSEC.md](docs/DURCISSEMENT_DNSSEC.md) |
| Logs et labels | [JOURNALISATION.md](docs/JOURNALISATION.md) |
| Pare-feu DNS | [FIREWALLD.md](docs/FIREWALLD.md) |
| Historique santé DNS | [CORRECTION_CONTROLES_DNS.md](docs/CORRECTION_CONTROLES_DNS.md) |

Les rapports spécialisés peuvent décrire une étape antérieure : leurs chiffres ne remplacent pas les métadonnées courantes. En cas d’écart, examiner le code livré et `DELIVERY.json` avant intervention. Aucun support, SLO/SLA ou astreinte n’est défini ; l’équipe exploitante doit désigner propriétaire, escalade, objectifs et dépôt de suivi.

**Licence :** métadonnées du rôle `proprietary`. Aucun texte détaillant les droits d’usage/redistribution n’est fourni ; les faire préciser par le propriétaire avant redistribution.
