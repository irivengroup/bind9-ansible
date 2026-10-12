# 🏷️ Bind9-Ansible

> Déployer et exploiter des paires BIND 9 primaire/secondaire par site, avec séparation ADM/FRONT/BACK, zones autoritaires, forwarding conditionnel, génération PTR et reprise transactionnelle.

**Base exacte :** `bind9-ansible-v4-path-hardened.zip` fournie par l’utilisateur. Cette livraison renomme et documente cette V4 ; aucun code de la V5 locale n’est repris. **Rôle :** `bind9`. **Qualification de production : NON TERMINÉE** ; les preuves et contrôles restants figurent ci-dessous et dans [le rapport de livraison](docs/VALIDATION_LIVRAISON.md).

Le nouveau nom concerne le projet et ses libellés. Les variables `bind_*`, le rôle, les services, les chemins `/var/backups/Bind9Project/`, `/var/lib/Bind9Project/`, le stockage contrôleur `~/.local/share/dnszibo/tsig/` et le marqueur `DNSZIBO-MANAGED-REVERSE` sont conservés pour retrouver les sauvegardes, secrets, transactions et zones des installations existantes.

## 📊 Matrice de compatibilité

| Composant | Contrat observé dans les fichiers | Validation |
|---|---|---|
| OS cible | `validate.yml` exige exactement `RedHat`, version majeure `8` | RHEL 8 ; NON EXÉCUTÉ sur cible réelle |
| Autres distributions | Métadonnée Galaxy `EL: 8`, mais assertion plus restrictive | Rocky, AlmaLinux, CentOS, Ubuntu et RHEL 9 refusés par cette assertion |
| Ansible minimal | `min_ansible_version: '2.9'` dans les métadonnées | Minimum déclaré, compatibilité effective non démontrée |
| Ansible en CI | `ansible-core==2.16.14` | Configuration du workflow, pas résultat de CI attesté |
| Python contrôleur | CI configurée en Python `3.12` ; TSIG utilise `fcntl`, `flock`, `O_NOFOLLOW` | Contrôleur Linux pour le flux fourni ; Windows natif non adapté |
| Python cible | Python 3 pour les scripts ; découverte Ansible `auto_silent` | Version non fixée ; qualifier l’interpréteur avec Ansible et les bindings DNF de la cible |
| BIND | Paquets RHEL `bind`, `bind-utils`, service `named` | Version non épinglée ; vérifier les directives avec les paquets installés |
| Contrôles locaux de cette livraison | Windows et Python 3.14 | Analyse statique seulement ; pas une qualification RHEL |

Le runner Ubuntu de la CI est un contrôleur de tests, pas un serveur DNS supporté. Aucun badge de réussite ni validation sur une matrice d’OS n’est revendiqué.

## 🔌 Prérequis & Dépendances

- Contrôleur Linux, Ansible, Python, accès SSH et stockage privé persistant des clés TSIG. Toutes les commandes Ansible ci-dessous partent de la racine contenant `ansible.cfg`.
- Quatre hôtes RHEL 8 pour l’inventaire fourni, accessibles sur **ADM**, compte SSH avec élévation autorisée (`become: true`). Configurer l’utilisateur via l’inventaire ou `-u`, les clés via SSH ; ajouter `--ask-become-pass` si nécessaire.
- Interfaces FRONT et BACK préconfigurées et routables ; adresses ADM/FRONT/BACK distinctes. Les noms des interfaces sont exigés mais le rôle ne configure pas leurs adresses.
- Dépôts RHEL disponibles. Le rôle installe seulement `bind`, `bind-utils`, `policycoreutils-python-utils` via `ansible.builtin.dnf`.
- Sur les cibles : systemd, Python 3 accessible par `/usr/bin/env python3`, bindings DNF, Bash, GNU tar avec ACL/xattrs/SELinux, `flock`, `sha256sum`, `find`, `sort`, `sed`, `grep`, `mktemp`, `restorecon`, `dig`, `named-checkconf`, `named-checkzone`. Tous ces outils ne sont pas installés explicitement.
- Espace disque pour sauvegardes complètes et snapshot, plus une copie chiffrée hors hôte des sauvegardes et secrets contrôleur.
- `requirements.yml` déclare `collections: []` et `meta/main.yml` déclare `dependencies: []` : aucune collection externe ni rôle dépendant. Aucune commande d’installation Galaxy n’est nécessaire pour cette livraison.

Provisionner séparément les flux : SSH sur ADM, DNS UDP/TCP 53 depuis les clients autorisés vers FRONT/BACK, échanges DNS des pairs sur BACK et accès BACK aux forwarders sur leurs ports. Le rôle ne gère aucun pare-feu, routage, délégation DNS ou résolveur client.

## 🛠️ Variables du Rôle / Playbook

### Defaults exacts — `roles/bind9/defaults/main.yml`

| Variable | Type | Description | Valeur par défaut | Obligatoire ? |
|---|---|---|---|---|
| `bind_frontend_clients` | liste de chaînes | IP/CIDR canoniques autorisés sur FRONT ; liste vide = vue absente | `[]` | Non |
| `bind_backend_clients` | liste de chaînes | IP/CIDR canoniques autorisés sur BACK ; liste vide = vue absente | `[]` | Non |
| `bind_forward_zones` | liste d’objets | Zones forwardées dans la vue cliente BACK | `[]` | Non |
| `bind_authoritative_zones` | liste d’objets | Zones directes et enregistrements désirés | `[]` | Non |
| `bind_enable_cgroups` | booléen | Active les limites systemd ; faux retire le fichier de limites | `false` | Non |
| `bind_memory_max` | chaîne | `MemoryMax` si limites activées | `512M` | Non |
| `bind_tasks_max` | entier | `TasksMax` | `256` | Non |
| `bind_cpu_quota` | chaîne | `CPUQuota` | `100%` | Non |
| `bind_backup_root` | chaîne | Répertoire cible des sauvegardes complètes | `/var/backups/Bind9Project/bind` | Non |
| `bind_migration_state_dir` | chaîne | Ancien paramètre de baseline ; **non consommé par les tâches actuelles** | `/var/lib/Bind9Project/bind-migration` | Non, sans effet dans le playbook actuel |
| `bind_monitoring_enabled` | booléen | Installe/active la surveillance ; faux saute les tâches mais ne désactive pas un timer existant | `true` | Non |
| `bind_primary_group` | chaîne | Groupe des primaires | `dns_primary` | Non |
| `bind_secondary_group` | chaîne | Groupe des secondaires | `dns_secondary` | Non |

### Variables obligatoires d’inventaire

| Variable | Type | Description | Valeur par défaut / exemple fourni | Obligatoire ? |
|---|---|---|---|---|
| `ansible_host` | chaîne | IP ADM pour SSH | Aucune ; `10.10.1.11` pour site1-dns01 | Oui |
| `bind_front_ip` | chaîne | IP d’écoute FRONT | Aucune ; `192.0.2.11` pour site1-dns01 | Oui |
| `bind_back_ip` | chaîne | IP d’écoute et source BACK | Aucune ; `10.20.1.11` pour site1-dns01 | Oui |
| `bind_front_interface` | chaîne | Interface FRONT, distincte de BACK | Aucune ; `ens192` dans l’inventaire | Oui |
| `bind_back_interface` | chaîne | Interface BACK | Aucune ; `ens224` dans l’inventaire | Oui |
| `bind_site_name` | chaîne | Nom exact du groupe de site contenant l’hôte | Aucune ; `site1` ou `site3` | Oui |
| `bind_tsig_key_name` | chaîne | Identité TSIG : premier caractère alphanumérique, puis lettres/chiffres/points/tirets/underscores, maximum 128 caractères | Aucune ; `site1-transfer` ou `site3-transfer` | Oui |
| `bind_tsig_algorithm` | chaîne | Seul algorithme accepté | Aucune ; `hmac-sha256` dans chaque site | Oui |
| `bind_tsig_secret_file` | chaîne | Chemin absolu du secret **sur le contrôleur** | Aucune ; `{{ lookup("env", "HOME") }}/.local/share/dnszibo/tsig/site1.key` ou `site3.key` | Oui |

`bind_tsig_secret` est un fait calculé à partir du fichier contrôleur, pas une entrée Vault. Ne pas surcharger les faits internes `bind_primary_host`, `bind_secondary_host`, `bind_generated_reverse_zones`, `bind_managed_zones`, `bind_transaction_zones`, ni les variables de résultats de commandes.

Les exemples d’inventaire surchargent les defaults : FRONT contient `198.51.100.0/24` et `203.0.113.0/24` ; BACK reste vide. Zones autoritaires : `cloud.example.test` et `services.example.test`, TTL 300, serial 2026101101. Forwarders : `10.20.0.53` pour `corp.example.test`, `10.20.0.99` pour `fake.example.test`, port 53. **Sans ACL BACK, aucune vue cliente de forwarding n’est rendue.** Remplacer les exemples avant utilisation réelle.

### Contrat des zones et enregistrements

| Champ | Type / défaut | Comportement |
|---|---|---|
| Zone autoritaire `domain` | chaîne obligatoire | Suffixe DNS valide, unique, sans chevauchement avec les zones forwardées |
| `serial` | entier ou chaîne numérique obligatoire | 1 à 4294967295 ; augmenter lors d’un changement de zone directe |
| `ttl` | entier, défaut `300` | Non négatif ; le template inverse remplace une valeur fausse telle que 0 par 300 |
| `records` | liste, défaut `[]` | Objets `name`, `type`, `value` |
| Record `name` | chaîne obligatoire | `@`, `*` ou nom relatif selon le validateur ; les jokers A/AAAA ne passent pas le générateur PTR |
| Record `type` | chaîne obligatoire | `A`, `AAAA`, `CNAME`, `TXT`, `MX`, `PTR`, `SRV`, `CAA` |
| Record `value` | valeur non vide | Insérée directement dans BIND ; pas d’échappement automatique TXT/MX/SRV/CAA, validation BIND indispensable |
| Record `mask` | interdit | Génération inverse fixe : IPv4 `/24`, IPv6 `/64` |
| Zone forwardée `domain` | chaîne obligatoire | Pas de `.` ni joker ; suffixes forwardés imbriqués refusés |
| `policy` | chaîne, défaut `only` | Seule valeur admise ; template `forward only` |
| `forwarders` | liste non vide | Chaque objet contient `address` obligatoire et `port` de 1 à 65535, défaut 53 |

```yaml
bind_authoritative_zones:
  - domain: cloud.example.test
    ttl: 300
    serial: 2026101201
    records:
      - {name: app, type: A, value: 192.0.2.100}
      - {name: api, type: AAAA, value: '2001:db8:1234:5678::10'}
      - {name: '@', type: TXT, value: '"service DNS de test"'}
bind_backend_clients:
  - 10.40.0.0/24
bind_forward_zones:
  - domain: corp.example.test
    policy: only
    forwarders:
      - {address: 10.20.0.53, port: 53}
```

Le template ajoute SOA, NS et les A de `ns1`/`ns2` à partir des IP FRONT. Les PTR dérivent uniquement des A/AAAA déclarés dans `records`, pas des A ajoutés par le template. Deux noms différents pour une même IP provoquent un conflit PTR. Le générateur utilise le maximum des serials des zones contributrices ; le reconciliateur préserve ou augmente ensuite le serial selon les changements réels. La délégation des espaces inverses relève de l’exploitant ; RFC 2317 n’est pas implémentée.

Les fichiers inverses obsolètes ne sont supprimés que s’ils portent le marqueur géré historique. Retirer une zone directe enlève sa déclaration mais ne nettoie pas automatiquement son ancien fichier. Les secondaires écrivent leurs zones dans `/var/named/slaves`.

## 🔐 Gestion des Secrets (Ansible Vault)

### TSIG réellement implémenté

Le rôle crée ou vérifie 32 octets aléatoires encodés en Base64 dans un fichier privé du contrôleur. Répertoire `0700`, fichier créé `0600`, vérification du propriétaire et des permissions du fichier existant, refus des liens symboliques et verrou `flock`. Les deux pairs d’un site lisent le même fichier. Utiliser une identité et un chemin distincts par site ; l’unicité intersite n’est pas entièrement imposée par le code.

La cible reçoit `/etc/named/10-tsig.conf` en `root:named`, mode `0640`. Les tâches manipulant le secret utilisent `no_log: true`. Les sauvegardes peuvent contenir ce secret : chiffrer et restreindre leurs copies. TSIG authentifie les échanges ; leur chiffrement n’est pas implémenté par le projet.

Le fichier contrôleur reste **en clair protégé par permissions**, et doit contenir du Base64, pas du contenu chiffré Ansible Vault. Ne pas passer `bind_tsig_secret` en extra-vars. Ne pas supprimer le fichier pour provoquer une rotation : la rotation coordonnée n’est pas implémentée.

### Utilisation facultative de Vault pour Ansible

Aucune variable `vault_*` n’est consommée directement par le rôle. Vault peut protéger le mot de passe sudo d’Ansible. Le dépôt contient déjà `inventory/group_vars/all.yml` : pour utiliser `group_vars/all/vault.yml`, convertir cette organisation au lieu de garder les deux simultanément.

```bash
test -f inventory/group_vars/all.yml &&
  test ! -e inventory/group_vars/all &&
  mkdir inventory/group_vars/all &&
  mv inventory/group_vars/all.yml inventory/group_vars/all/main.yml
ansible-vault create inventory/group_vars/all/vault.yml
```

Dans l’éditeur Vault, saisir `vault_ansible_become_password` et sa valeur réelle ; le fichier sauvegardé sera chiffré. Ajouter seulement cette liaison claire dans `inventory/group_vars/all/main.yml` :

```yaml
ansible_become_password: "{{ vault_ansible_become_password }}"
```

L’adaptation est facultative et **non appliquée à l’inventaire livré**. Elle exige d’ajuster les tests qui lisent explicitement `inventory/group_vars/all.yml`. Avec sudo sans mot de passe, Vault n’est pas requis pour ce flux. Aucun secret factice ou fichier chiffré inutilisable n’est livré.

```bash
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --ask-vault-pass

# Fichier externe existant, privé et exclu de Git.
test -r "$HOME/.config/ansible/vault-password" &&
ansible-playbook -i inventory/hosts.ini playbooks/site.yml \
  --vault-password-file "$HOME/.config/ansible/vault-password"
```

Conserver les clés SSH, mots de passe Vault, secrets TSIG et sauvegardes hors dépôt. Restreindre le fichier de mot de passe Vault au compte d’exécution.

## 🏷️ Découpage par Tags (Modularité)

| Tag | Description réelle | Scénario d’usage type |
|---|---|---|
| `always` | Pairs, génération et assemblage des zones | Préparation commune ; ne pas exclure |
| `deploy` | TSIG, précontrôles, paquets, sauvegarde, configuration, zones, validation, service, santé, commit et rescue | Cycle de modification ; **n’inclut pas `monitor`** |
| `preflight` | Entrées, chemins et disponibilité du primaire | Non autonome : dépend notamment du fait TSIG préparé ailleurs |
| `install` | Installation DNF | Préparation sur RHEL qualifié ; ne déclenche pas les assertions de plateforme |
| `backup` | Installation et appel du script de sauvegarde | Sauvegarde ponctuelle, inventaire complet requis |
| `configure` | TSIG, répertoires, transaction, ACL, configuration, zones, sécurité | Ne pas lancer seul : `bind_transaction_zones` préparé sous `deploy`, commit absent |
| `zones` | Fichiers de zones, nettoyage inverse et serials | Non autonome : dépend du snapshot et de la configuration |
| `records` | Même sous-ensemble que `zones` | Modifier l’inventaire puis exécuter `deploy` |
| `validate` | Vérifications BIND et zones | Contrôler un serveur déjà configuré |
| `services` | Démarrage named, vidage des handlers, contrôle actif | Maintenance d’une configuration déjà vérifiée |
| `health` | Requêtes SOA et comparaison au serial désiré | Contrôle d’un serveur déjà déployé |
| `monitor` | Script, service et timer de santé | Installation de surveillance après déploiement |

Pas de tag `config`, `security`, `tsig` ou `rollback`. Les sous-tags ne résolvent pas leurs dépendances. Pour modifier le DNS, utiliser l’exécution sans filtre ou `--tags deploy,monitor`. Une sélection arbitraire n’offre pas la transaction complète.

## 🚀 Exemples d’Utilisation

### 1. Structure de l’Inventaire (`inventory/hosts.ini`)

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

| Hôte | ADM / `ansible_host` | FRONT | BACK |
|---|---|---|---|
| site1-dns01 | 10.10.1.11 | 192.0.2.11 | 10.20.1.11 |
| site1-dns02 | 10.10.1.12 | 192.0.2.12 | 10.20.1.12 |
| site3-dns01 | 10.11.1.11 | 192.0.2.21 | 10.30.1.11 |
| site3-dns02 | 10.11.1.12 | 192.0.2.22 | 10.30.1.12 |

Adresses d’exemple à adapter avec les interfaces avant exécution :

```yaml
# inventory/host_vars/site1-dns01.yml
ansible_host: 10.10.1.11
bind_front_ip: 192.0.2.11
bind_back_ip: 10.20.1.11
bind_front_interface: ens192
bind_back_interface: ens224
```

```yaml
# inventory/group_vars/site1.yml
bind_site_name: site1
bind_tsig_key_name: site1-transfer
bind_tsig_algorithm: hmac-sha256
bind_tsig_secret_file: '{{ lookup("env", "HOME") }}/.local/share/dnszibo/tsig/site1.key'
```

Chaque site exige exactement un primaire et un secondaire ; chaque hôte appartient à un seul rôle DNS. Ajouter un site implique son groupe, ses `group_vars`, deux `host_vars` et les groupes transverses, avec les primaires avant les secondaires. Le rôle ne configure pas une adresse virtuelle ni la bascule des résolveurs clients.

### 2. Appel du Playbook (`playbooks/site.yml`)

```yaml
---
- name: Deploy Bind9-Ansible BIND9 on both sites
  hosts: dns_deployment_order
  become: true
  gather_facts: true
  order: inventory
  serial: 1
  any_errors_fatal: true
  roles:
    - bind9
```

Ordre fourni : site1 primaire, site3 primaire, site1 secondaire, site3 secondaire. Un échec arrête la progression, sans transaction globale sur les quatre hôtes. Le contrôle du primaire depuis un secondaire appelle `dig` **avant l’installation des paquets** : prévoir `bind-utils` sur les secondaires neufs, sinon cette étape peut échouer.

### 3. Commandes d’Exécution (CLI)

Depuis la racine extraite, après adaptation de l’inventaire et préparation SSH/sudo :

```bash
ansible-inventory -i inventory/hosts.ini --graph
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --syntax-check
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --list-tags
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --list-hosts

# Exécution complète, surveillance comprise.
ansible-playbook -i inventory/hosts.ini playbooks/site.yml

# Contrôle ciblé d’un serveur déjà installé.
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags validate

# Cycle DNS puis surveillance.
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags deploy,monitor

# Exclure la configuration de surveillance.
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --skip-tags monitor

# Limiter un site sans retirer ses pairs de l’inventaire.
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --limit site1
```

Exclure `monitor` ou mettre son activation à faux ne désactive pas un timer existant.

### 4. Dry-run / Check mode

```bash
# Seulement sur une copie de laboratoire préparée.
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --check --diff
```

**NON QUALIFIÉ comme simulation sûre de bout en bout.** Les scripts n’ont pas de mode simulation. Les commandes peuvent être sautées tandis que lookups et expressions sont évalués ; secret absent ou résultat manquant peuvent faire échouer le parcours. Le `rescue` contient un redémarrage systemd : ne pas garantir l’absence d’effet de bord sans recette. Les diffs peuvent exposer la topologie et les données DNS. Pour une inspection sans déploiement, préférer `--syntax-check`, `--list-hosts` et `--list-tags`, qui ne prouvent cependant pas la validité opérationnelle.

## 🛡️ Architecture et sécurité effectives

- `/etc/named.conf` inclut TSIG, ACL si nécessaire, options et vues ; les modules de zones sont dans `/etc/named/zones/`.
- Vue `peer` sur BACK, sélectionnée en premier : IP des pairs ou clé TSIG, zones autoritaires et racine locale. Une requête issue d’une IP de pair utilise cette vue sans forwarding.
- Vue `backend`, si ACL non vide : récursion/cache réservés aux clients BACK, zones autoritaires partagées et forwarding conditionnel.
- Vue `external`, si ACL non vide : autoritaire uniquement sur FRONT, récursion/cache interdits, aucun forwarding.
- Vue finale `deny` refusant les autres clients. La racine locale vide empêche la résolution Internet générale ; elle ne remplace pas le pare-feu.
- Écoute IPv4 sur FRONT/BACK ; `listen-on-v6 { none; };`. Les données AAAA/PTR IPv6 n’activent pas un transport DNS IPv6.
- `dnssec-validation no` explicite ; validation et signature DNSSEC non fournies.
- Transferts autorisés par clé sur le primaire, interdits depuis le secondaire. NOTIFY sortant signé ; `allow-notify` entrant du secondaire est exprimé par IP, pas par une ACL exigeant explicitement la clé. Qualifier les cas positifs/négatifs.
- `restorecon` réapplique les contextes standards. L’absence de `unlabeled_t` n’atteste pas toute la politique SELinux.
- L’inventaire reste une entrée administrative de confiance : les contrôles ne constituent pas une validation exhaustive de toutes les valeurs DNS, IP et limites systemd.

### PATH persistant

`/etc/profile.d/bind9-sbin-path.sh` ajoute `/usr/local/sbin` si ce composant exact est absent. Prise en compte à la prochaine session chargeant `/etc/profile`, ou dans un shell compatible :

```bash
. /etc/profile.d/bind9-sbin-path.sh
```

Cela ne modifie ni le `secure_path` de sudo ni tous les shells non interactifs. La variante avec slash final `/usr/local/sbin/` n’est pas normalisée par le test et peut coexister avec `/usr/local/sbin`.

## 💾 Sauvegarde, exploitation et retour arrière

### Sauvegarde complète

`migration.yml` appelle `bind-backup.sh backup --root ...` avant les changements DNS, mais **après** le provisionnement TSIG et l’installation des paquets. Aucun effacement initial des arborescences n’est effectué. La sauvegarde comprend `/etc/named.conf`, `/etc/named/`, `/var/named/`, propriétaires, ACL, xattrs, contextes SELinux et SHA-256. Réutilisation de l’archive de l’heure UTC courante ; conservation des cinq dernières lors de la création d’une nouvelle.

**Aucun cron ni timer de sauvegarde horaire n’est installé.** La granularité horaire décrit la réutilisation, pas une planification. Les secrets du contrôleur et les unités systemd sont hors de cette sauvegarde. La branche historique `snapshot` du script n’est pas appelée par le rôle et n’est pas la procédure de sauvegarde de cette livraison.

```bash
sudo /usr/local/sbin/bind-backup.sh backup --root /var/backups/Bind9Project/bind
sudo /usr/local/sbin/bind-backup.sh list --root /var/backups/Bind9Project/bind
```

### Transaction par hôte

`named-transaction begin` conserve les fichiers gérés et les zones dans `/var/lib/Bind9Project/deployment/`. Un état `PENDING` bloque une nouvelle transaction. Après configuration, vérification BIND, service et santé, `commit` supprime le snapshot. Un échec dans le bloc protégé déclenche une tentative de restauration et de redémarrage, puis un échec explicite du playbook.

La validation arrive après écriture sur disque, avant rechargement : aucun staging complet. Le rollback ne couvre pas paquets, secrets contrôleur, fichiers secondaires, PATH ou toute la surveillance. Propriétaires et modes POSIX sont restaurés, pas toutes les ACL/xattrs ; `restorecon` est réappliqué. La surveillance est configurée après commit. Ne pas supprimer manuellement les marqueurs pour contourner une transaction interrompue.

### Restauration manuelle

Opération avec interruption DNS : arrêter les déploiements concurrents, choisir une fenêtre de maintenance, conserver une copie externe des archives/snapshots et clés contrôleur, vérifier la disponibilité de l’autre pair. Utiliser un nom réellement retourné par `list` :

```bash
# Sur le serveur concerné, dans Bash.
backup_root=/var/backups/Bind9Project/bind
sudo /usr/local/sbin/bind-backup.sh list --root "$backup_root"
read -r -p 'Nom exact de l’archive à restaurer : ' archive_name
if [[ "$archive_name" =~ ^named-config-[0-9]{8}T[0-9]{6}Z\.tar\.gz$ ]] &&
   sudo test -f "$backup_root/$archive_name"; then
  sudo systemctl stop named &&
  sudo /usr/local/sbin/bind-backup.sh restore --root "$backup_root" --archive "$archive_name" &&
  sudo /usr/sbin/named-checkconf -z /etc/named.conf &&
  sudo systemctl start named &&
  sudo systemctl is-active named
fi
```

En cas d’échec, laisser le service arrêté et diagnostiquer. Le script vérifie SHA-256 et les membres, prend un instantané de secours et tente de le rétablir si extraction ou validation échouent. Il extrait par-dessus l’existant : les fichiers supplémentaires créés après sauvegarde ne sont pas tous supprimés. Ce n’est pas une restauration exacte d’arborescence ; la reprise réelle doit être testée.

Pour une transaction interrompue : `sudo /usr/local/sbin/named-transaction status`, puis préparer `restore` avec la liste complète des `--zone` du snapshot, **anciennes zones inverses supprimées comprises**. Lire `pending.json` en compte autorisé et comparer la liste des chemins à l’inventaire ; ne pas exécuter son contenu comme du code. Après restauration : contrôle BIND, redémarrage et contrôles DNS. La liste des seules zones directes ne suffit pas.

### Surveillance et reprise

`named-healthcheck.timer` démarre deux minutes après le boot puis toutes les minutes, précision 10 s. Il contrôle named et les SOA autoritaires locaux, mais ni les serials intersites, ni le forwarding, ni les alertes centrales.

```bash
sudo systemctl status named named-healthcheck.timer
sudo journalctl -u named -n 100
sudo journalctl -u named-healthcheck.service -n 100
sudo /usr/local/sbin/named-transaction status
```

Définir et mesurer la perte de données acceptable (RPO) et le délai de reprise (RTO). Aucun engagement chiffré de disponibilité, délai de transfert ou reprise n’est établi par le code.

## 🧪 Tests & Qualité du Code (CI/CD)

### Workflow fourni

`.github/workflows/ci.yml` utilise `ubuntu-latest`, `actions/checkout@v4`, `actions/setup-python@v5`, Python 3.12 et installe `ansible-core==2.16.14 pytest pyyaml`. Il exécute syntaxe Ansible, pytest, compilation des tests, syntaxe des deux scripts Bash et compilation de `named-transaction`.

Il ne fournit ni couverture/seuil, ni ansible-lint, scénario Molecule, matrice RHEL, audit des dépendances, installation réelle ou publication. Pytest/PyYAML, le runner et les références majeures des actions ne forment pas un environnement totalement figé. Les tests inspectent principalement les fichiers ou rendent des templates ; certains exercent le générateur PTR et le provisionnement TSIG local.

### Commandes à reproduire sous Linux

Avec Python 3.12, depuis la racine du projet :

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install ansible-core==2.16.14 pytest pyyaml
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --syntax-check
python -m pytest -q tests
python -m compileall -q tests roles/bind9/filter_plugins
python -m py_compile roles/bind9/files/named-transaction \
  roles/bind9/files/tsig-secret.py roles/bind9/files/reverse-serial.py
bash -n roles/bind9/files/bind-backup.sh roles/bind9/files/named-healthcheck
```

Installer séparément une version d’`ansible-lint` compatible avec l’environnement choisi, puis :

```bash
ansible-lint playbooks/site.yml roles/bind9
```

**Molecule :** aucun scénario `molecule/` n’est livré. Créer un scénario de convergence, idempotence et reprise sur cibles RHEL 8 de laboratoire (systemd, SELinux, réseaux), puis lancer `molecule test`. Cette commande n’est pas une preuve de tests existants ou réussis.

### Bilan de livraison — 12 octobre 2026

| Contrôle | Statut | Résultat ou raison / procédure / risque restant |
|---|---|---|
| Inspection des sources V4 | EXÉCUTÉ | Tâches, defaults, inventaire, scripts, templates et CI examinés |
| Syntaxe Python et intégrité ZIP | EXÉCUTÉ | Résultats dans `docs/VALIDATION_LIVRAISON.md` |
| Sous-ensemble sans dépendances externes | EXÉCUTÉ | 31 fonctions de tests existantes appelées directement, sans pytest : 31 réussites ; 36 fichiers Python compilés en mémoire |
| Exhaustivité des tableaux | EXÉCUTÉ | 13 defaults et 12 tags présents ; 68 fichiers critiques identiques à l’archive source |
| Comparaison avant/après | EXÉCUTÉ | Documentation et libellés ; logique, inventaire, defaults et tests conservés |
| Pytest | NON EXÉCUTÉ | Dépendances absentes, téléchargement bloqué par la résolution de pypi.org ; commandes Linux ci-dessus, attendre tests réussis ; non-régression complète non attestée |
| YAML/Ansible et rendu intégré | NON EXÉCUTÉ | Ansible/PyYAML/Jinja2 absents ; contrôleur avec dépendances CI requis, commande syntax-check ci-dessus ; attendre absence d’erreurs, risque d’intégration restant |
| Bash | NON EXÉCUTÉ | Bash ne démarre pas localement (code Windows `-1073741502`, également sur `--version`) ; `bash -n` ci-dessus sous Linux, attendre code 0 ; syntaxe shell non attestée ici |
| Lint | NON EXÉCUTÉ | Outil absent ; installer puis commande ci-dessus, traiter tous les diagnostics ; qualité idiomatique non attestée |
| Molecule | NON EXÉCUTÉ | Outil/scénario/cibles absents ; préparer puis `molecule test`, attendre convergence/reprise ; recette automatisée manquante |
| Couverture | NON EXÉCUTÉ | Instrumentation/seuil absents ; définir le périmètre puis mesurer avec pytest-cov sur Linux ; chemins non testés possibles |
| RHEL/BIND/SELinux/réseau/rollback | NON EXÉCUTÉ | Aucun laboratoire connecté ; matrice ci-dessous à exécuter ; fonctionnement et reprise réels non qualifiés |

Les nombres de tests des anciens `docs/VERIFICATION*.md` sont des résultats historiques fournis avec l’archive, non réexécutés ici.

### Recette avant production

| Scénario | Procédure et résultat attendu |
|---|---|
| Installation | Quatre RHEL 8 isolés, prérequis et inventaire adaptés ; cycle complet, named actif, état CLEAN |
| Réexécution | Deuxième cycle, réponses stables et clés conservées ; examiner les changed (transactions déclarées changées à chaque cycle) |
| BIND | `sudo /usr/sbin/named-checkconf -z /etc/named.conf`, puis `named-checkzone` pour chaque zone primaire ; codes 0 |
| FRONT/BACK | Depuis clients autorisés/refusés, requêtes `dig` UDP et TCP ; autoritaire sur FRONT, forwarding seulement BACK autorisé, refus ailleurs |
| ADM/IPv6 | Absence d’écoute DNS ADM/IPv6, pare-feu externe vérifié et SSH maintenu |
| TSIG | AXFR/IXFR/NOTIFY positifs et négatifs sur les deux sites ; bons serials secondaires et refus des transferts non autorisés |
| PTR | Ajouter/modifier/retirer un A/AAAA ; vérifier PTR, nettoyage des inverses gérés et serials des deux pairs |
| Échecs | Zone invalide, forwarder indisponible, interruption Ansible, panne named ; échec explicite et reprise contrôlée |
| Restauration | Copie d’archive corrompue refusée, archive intacte restaurée ; contenus, propriétaires, ACL et SELinux vérifiés |
| Perte d’un pair/site | Clients préparés à utiliser l’autre serveur continuent la résolution autoritaire ; mesurer l’interruption |

**Limite déduite du code :** `health.yml` compare le serial à `bind_managed_zones`, alors que `reverse-serial.py` peut conserver ou augmenter un serial différent dans le fichier inverse. Tester notamment suppression de PTR et changement de serial direct sans changement PTR : le contrôle de santé peut échouer et déclencher un rollback. Aucune correction fonctionnelle de ce mécanisme n’est incluse dans cette livraison documentaire.

## 🤝 Contribution & Bonnes Pratiques

Créer une branche dédiée ; garder IP/interfaces dans `host_vars`, paramètres de site dans ses `group_vars` et données communes dans `all.yml`. Augmenter les serials directs, ajouter une non-régression pour chaque correction, exécuter tests et linter avant commit, faire relire le diff et documenter les changements de contrats ainsi que leur retour arrière.

Ne pas committer secrets, Vault déchiffré, archives de serveurs, caches ou environnements Python. Modifier les chemins persistants ou marqueurs exige une migration explicite. Pour une archive source, exclure `.venv`, `__pycache__`, `.pytest_cache`, journaux, secrets et fichiers temporaires ; vérifier le ZIP et joindre son SHA-256.

## 📄 Licence

`roles/bind9/meta/main.yml` déclare **`proprietary`**. Aucun texte détaillé de licence n’est fourni ; ne pas attribuer une licence MIT ou Apache. Auteur et organisation déclarés : Alfred TCHONDJO, IRIVEN Group. Les conditions de réutilisation restent à préciser par le titulaire des droits.
