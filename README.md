# 🏷️ Bind9-Ansible

> Déployer et exploiter une paire BIND 9 par site, avec résolution privée, réplication TSIG, loopback natif RHEL et routes de retour FRONT ajoutées sans reconfigurer le réseau existant.

Le point d'entrée est `playbooks/bind9-deploy.yml`. Le rôle produit `/etc/named.conf` depuis `roles/bind9/templates/named.conf.j2`, selon la structure native EL8 : **sans vues**, zones officielles chargées, **DNSSEC optionnel, désactivé par défaut** et lancement natif de BIND, avec écoute DNS IPv4 uniquement et transport sortant IPv4/IPv6. Les entrées clientes sont **`bind_cloud_clients`** (FRONT) et **`bind_internal_clients`** (BACK). Leur fusion calculée **`bind_allowed_clients`** alimente l’ACL BIND **`allowed_clients`**. Une liste peut être vide, jamais les deux. Les routes retour ne concernent que les clients cloud. Seules les ACL consommées sont générées : FRONT local, deux pairs BACK et leur agrégat BACK, loopback et clients déclarés. NXDOMAIN hors périmètre est conservé. La gestion des routes reste **désactivée par défaut**. Quand DNSSEC est activé, les suffixes privés non signés explicitement déclarés utilisent une exception renouvelée via RNDC natif et systemd. En mode `false`, ces composants ne sont pas déployés. La qualification sur vos serveurs reste **NON TERMINÉE** ; voir [VALIDATION_RHEL8.md](docs/VALIDATION_RHEL8.md) et [DNSSEC.md](docs/DNSSEC.md).

## 📊 Matrice de compatibilité

| Composant | Périmètre et preuve |
|---|---|
| OS cible | **Red Hat Enterprise Linux 8**, demandé ; déploiement réel **NON EXÉCUTÉ**. Le garde du rôle accepte `RedHat` de version majeure ≥ 8 ; cela ne qualifie pas RHEL 9/10. Rocky, AlmaLinux et Ubuntu sont refusés par ce garde. |
| Ansible | Ligne **2.9** demandée ; contrôles locaux : **EXÉCUTÉ**, avec **2.9.27**. Aucune affirmation sur tous les autres correctifs 2.9. |
| Python cible | **3.6.8**, fourni par l'utilisateur ; **EXÉCUTÉ** sur l'interpréteur extrait EL8 : compilation des Python de production, contrôles des scripts Python DNS restants ; le routage ne contient plus de code Python personnalisé. Modules Ansible sur le serveur réel : **NON EXÉCUTÉ**. |
| Contrôleur de test | Linux, Python **3.8.20**, Jinja2 **2.10.3** et **3.0.3**, Ansible **2.9.27**. |
| BIND de laboratoire | **9.11.36-16.el8_10.16**, binaires natifs RPM AlmaLinux EL8 exécutés en laboratoire Linux isolé, et **9.18.24** : chacun 109 configurations chargées et 282 contrôles DNS, dont signatures valides/invalides. Ce n'est pas une recette système RHEL. |
| NetworkManager de laboratoire | **1.46.0** ; ajout natif, persistance et reprise exécutés. La version installée sur les cibles reste à qualifier. |
| Transport DNS / routes | Écoute DNS IPv4 uniquement ; échanges sortants et forwarders IPv4/IPv6. Données AAAA/PTR IPv6 conservées ; routes retour gérées uniquement en IPv4. |

La matrice GitHub Actions contient Ansible 2.9.27, 2.13.13, 2.14.18 et 2.16.14 avec plusieurs Python de contrôleur. Sa présence n'est pas une preuve d'exécution CI ni de compatibilité OS. Ces runtimes anciens imposés par le contexte demandent une maintenance contrôlée des dépendances.

Version minimale déclarée dans les métadonnées du rôle : **Ansible 2.9**. La version effectivement utilisée pour cette recette est **2.9.27**.

## 🔌 Prérequis & dépendances

- Contrôleur Linux avec Ansible, Python, accès SSH aux adresses ADM et droit `become: true` sur les cibles. Aucun rôle externe ni collection Galaxy supplémentaire n'est utilisé.
- Cibles : Python utilisable par Ansible, DNF et ses bindings Python, systemd, SELinux, dépôts RHEL accessibles. Le rôle installe `bind`, `bind-utils` et `policycoreutils-python-utils`.
- FRONT, BACK et ADM sont **déjà configurés**, avec des adresses distinctes. NetworkManager gère FRONT, son profil actif contient la passerelle IPv4 existante ; `nmcli` et `ip` avec sortie JSON sont présents. Le rôle n'installe ni ne reconfigure NetworkManager.
- Réseau : SSH sur ADM ; DNS UDP/TCP 53 vers les interfaces et résolveurs autorisés ; TCP 53 entre les pairs BACK pour les transferts, UDP 53 pour NOTIFY. Le pare-feu, les IP et `/etc/resolv.conf` restent sous votre gestion.
- Fichiers natifs du paquet présents : `/etc/named.rfc1912.zones`, `/var/named/named.localhost`, `named.loopback`, `named.empty`. Le rôle les vérifie sans les remplacer. L'include natif `/etc/crypto-policies/back-ends/bind.config` doit exister et résoudre vers un fichier régulier ; la politique cryptographique du système est conservée.
- Administration native : `/usr/sbin/rndc`, systemd et `named-setup-rndc.service` dans tous les modes. DNSSEC activé seulement : `/etc/named.root.key` officiel actualisé et `/usr/bin/python3`. Le rôle démarre ce générateur pour disposer d'une clé d'administration `/etc/rndc.key` distincte de TSIG. Un `/etc/rndc.conf` personnalisé ne remplace pas cette exigence. RNDC écoute uniquement sur 127.0.0.1:953. Le drop-in commun conserve le lancement natif IPv4/IPv6 et ne surcharge que le reload RNDC ; `listen-on-v6 { none; };` ferme l’écoute DNS IPv6. Vérifier l’absence d’options externes `-4`/`-6` contradictoires et conserver le chemin canonique `/etc/named.conf`. Voir [TRANSPORT_DNS.md](docs/TRANSPORT_DNS.md).
- Un primaire et un secondaire par site. Déploiement primaire avant secondaire, via `dns_deployment_order`, `serial: 1`, `order: inventory`, `any_errors_fatal: true`.
- Horloges des pairs synchronisées, notamment pour la fenêtre de validité TSIG. Le rôle ne configure pas le service de synchronisation horaire.
- Espace disque pour les archives BIND ; sauvegardes protégées et exportées selon votre politique. Le rôle garde 30 jours d'archives, sans plafond en nombre.

`ansible.cfg` choisit `auto_silent` pour l'interpréteur. Si nécessaire, déclarer `ansible_python_interpreter: /usr/bin/python3` dans les variables de vos hôtes et vérifier que ce chemin correspond réellement à votre Python 3.6.8 et que les modules DNF fonctionnent.

## 🛠️ Variables du rôle / playbook

Les valeurs ci-dessous correspondent aux fichiers inspectés. « Absente » signifie qu'aucune valeur n'est fournie dans `defaults/main.yml`.

| Variable | Type | Description | Valeur par défaut | Obligatoire ? |
|---|---|---|---|---|
| `bind_dnssec_active` | booléen ou chaîne `auto` | `false` → validation `no`, aucun déploiement DNSSEC ; `true` → `yes` avec ancres natives ; `auto` → validation automatique | `false` | Non |
| `bind_cloud_clients` | liste de chaînes | Clients arrivant par FRONT ; seules destinations candidates aux routes retour FRONT | `[]` | Oui si internes vides |
| `bind_internal_clients` | liste de chaînes | Clients arrivant par BACK ; aucune route créée pour eux | `[]` | Oui si cloud vide |
| `bind_allowed_clients` | liste de chaînes calculée | Fusion sans doublons des deux entrées ; ACL DNS commune, à ne pas surcharger | Fait calculé à chaque exécution | Non, ne pas déclarer |
| `bind_forward_zones` | liste d'objets | Suffixes envoyés uniquement aux forwarders déclarés | `[]` | Non |
| `bind_authoritative_zones` | liste d'objets | Zones primaires, records et serials ; PTR générés automatiquement | `[]` | Non |
| `bind_front_route_metric` | entier | Métrique des **nouvelles** routes FRONT ; les routes existantes gardent la leur | `100` | Non |
| `bind_front_routes_enabled` | booléen | Active explicitement les ajouts de routes dans `deploy` / `routes` ; ne désactive pas `routes-recover` | `false` | Non |
| `bind_backup_root` | chemin absolu | Archives, empreintes et rétention | `/var/backups/Bind9-Ansible/bind` | Non |
| `bind_monitoring_enabled` | booléen | Autorise l'installation du moniteur lorsque le tag `monitor` est sélectionné | `false` | Non |
| `bind_primary_group` | chaîne | Groupe identifiant les primaires | `dns_primary` | Non |
| `bind_secondary_group` | chaîne | Groupe identifiant les secondaires | `dns_secondary` | Non |
| `bind_environment` | chaîne | Environnement ; fourni dans `inventory/<env>/group_vars/all.yml` | Absente | Oui |
| `bind_site_name` | chaîne | Groupe du site local | Absente | Oui |
| `ansible_host` | adresse | Adresse ADM pour SSH, distincte de FRONT et BACK | Absente | Oui |
| `bind_front_ip` | chaîne IPv4 | Adresse DNS FRONT déjà affectée à l'hôte | Absente | Oui |
| `bind_back_ip` | chaîne IPv4 | Adresse DNS BACK / réplication déjà affectée à l'hôte | Absente | Oui |
| `bind_tsig_key_name` | chaîne | Identifiant de clé du site | Absente | Oui |
| `bind_tsig_algorithm` | chaîne | Algorithme admis par le rôle : `hmac-sha256` | Absente | Oui |
| `bind_tsig_secret_file` | chemin Linux absolu | Secret persistant du site sur le **contrôleur** | Absente ; exemples sous `$HOME/.local/share/bind9-ansible/tsig/<env>/<site>.key` | Oui |
| `bind_tsig_secret` | chaîne Base64 | Secret d'au moins 256 bits, chargé depuis le fichier contrôleur par le rôle | Fait généré | Ne pas le surcharger pour le déploiement |

`bind_primary_host`, `bind_secondary_host`, `bind_generated_reverse_zones`, `bind_managed_zones`, les plans de routes et les serials attendus sont des faits internes, pas des entrées d'inventaire à maintenir.

Les deux entrées attendent des IP IPv4 ou CIDR canoniques ; les listes sont plates.
L’une ou l’autre peut être `[]`, mais les deux vides sont refusées avant toute
modification, y compris avec des tags partiels. `bind_allowed_clients` est
recalculée avec `(bind_cloud_clients + bind_internal_clients) | unique | list`.
Ne pas la définir dans l’inventaire ni avec `-e` : une surcharge contradictoire
est refusée. La fusion ne sert jamais de liste de destinations de routes.

```yaml
bind_cloud_clients:
  - 192.0.2.0/24
  - 10.150.1.23
bind_internal_clients:
  - 192.168.20.25
  - 10.30.1.0/24
```

Les inventaires livrés gardent `bind_internal_clients: []` tant que les réseaux
internes réels sont inconnus. Ces adresses sont uniquement des exemples.
Le loopback et les pairs BACK restent des ACL techniques distinctes. Voir
[CLIENTS_UNIQUES.md](docs/CLIENTS_UNIQUES.md) pour les règles et la migration.

### Schéma des zones

- Zone autoritative : `domain`, `serial` obligatoires ; `ttl` vaut `300` par défaut ; `records` vaut `[]`. Serial entier positif ≤ 4294967295. Incrémenter le serial lors de toute modification des données autoritatives ou des délégations.
- Record : `name`, `type`, `value`. Types admis : `A`, `AAAA`, `CNAME`, `TXT`, `MX`, `PTR`, `SRV`, `CAA`. Fournir le RDATA BIND complet, avec les guillemets TXT et noms absolus terminés par un point lorsque nécessaires. Les adresses et caractères de contrôle sont vérifiés ; BIND vérifie ensuite les fichiers générés.
- Les records A génèrent des zones inverses `/24`, les AAAA des zones `/64`. Les champs `mask` par record sont refusés. Les PTR contradictoires sont refusés. Le serial inverse est conservé ou avancé selon les différences avec la sauvegarde réelle.
- Zone forward : `domain`, `forwarders` non vides ; `policy: only` uniquement. Chaque serveur a `address` littérale unicast **IPv4 ou IPv6** et `port`, entier ou chaîne numérique, défaut `53`, intervalle 1–65535. Une destination visant l'un des pairs DNS sur son port 53 est refusée pour éviter une boucle.
- `dnssec_validate` : booléen par zone forward, défaut `true`. Ce champ n’a d’effet que lorsque `bind_dnssec_active` vaut `true` ou `auto`. `false` déclare alors un suffixe privé non signé et ses descendants ; le rôle gère une NTA native, valable sept jours et renouvelée quotidiennement. Avec la validation globale `false`, aucune exception n’est déployée et aucun marqueur privé n’est rendu. Les chaînes `"false"` dans ce champ par zone restent refusées. Voir [DNSSEC.md](docs/DNSSEC.md).
- Les zones natives sont réservées. Les doublons sans distinction de casse, forwards imbriqués, forwards englobant une autorité et données autoritatives cachées sous un suffixe forward sont refusés. Les enfants forwardés d'une autorité sont permis avec délégation NS/glue générée dans le parent le plus proche ; les autres ont une délégation dans la racine privée.
- Limites de longueur dues aux noms NS générés : 249 caractères pour une autorité, 235 pour un suffixe forward. Les ACL `/0`, clients IPv6 et entrées ambiguës sont refusés.

Voir les exemples réellement livrés dans `inventory/dev/group_vars/site1.yml` et [VALIDATION_RHEL8.md](docs/VALIDATION_RHEL8.md).

Un [exemple complet avec deux zones forwardées et une liste cliente unique](examples/deux-zones-forwardees/README.md) fournit le named.conf rendu, la racine privée et les variables d'entrée.

Cette livraison consolide les sources et utilise les mêmes sélecteurs locaux pour `listen-on` et `allow-query-on` : ACL du primaire sur le primaire, ACL du secondaire sur le secondaire. Les deux rendus sont fournis dans l'exemple. La liste cliente est lue une seule fois dans le template ; un rendu vide ne produit aucune référence d’ACL indéfinie et le déploiement exige une liste non vide. La passe globale contrôle également les variables affectées, résultats enregistrés, imports de tâches, assets, handlers et filtres exportés ; voir [VERIFICATION_GLOBALE.md](docs/VERIFICATION_GLOBALE.md).

## 🏷️ Tags disponibles

| Tag | Effet / précaution |
|---|---|
| `deploy` | Parcours complet : TSIG, contrôles, paquets, sauvegarde, routes si activées, configuration, zones, RNDC, DNSSEC et renouvellement si activés, SELinux, validation, service et santé ; rollback protégé |
| `routes` | Routes de retour IPv4 FRONT uniquement si activées, avec contrôles et journal ; aucun déploiement DNS |
| `routes-recover` | Reprise explicite d'un journal de routes interrompu ; marqué `never`, exclu du parcours normal |
| `preflight` | Validation d'entrées, chemins et disponibilité du primaire ; nécessite déjà les utilitaires et le secret TSIG fourni pour cette exécution |
| `install` | Installation des trois paquets RHEL |
| `backup` | Installation de l'outil et sauvegarde forcée, avec rétention ; requiert les utilitaires BIND |
| `configure` | TSIG, répertoires, configuration, zones, runtime systemd, DNSSEC conditionnel et labels ; isolé, ne garantit pas sauvegarde, activation des exceptions ou santé |
| `zones`, `records` | Déploiement des données de zones ; ne remplace pas `deploy` |
| `validate` | Fichiers loopback natifs, `named-checkconf` et `named-checkzone` |
| `services` | Activation/rechargement du service `named` |
| `health` | Réponses loopback, SOA exacts des zones et SOA des suffixes forwardés en UDP/TCP |
| `monitor` | Installation/activation du service et timer santé si `bind_monitoring_enabled: true` ; cadence une minute |
| `always` | Identification des pairs et calcul des zones ; exécuté aussi avec un tag ciblé |

Utiliser `deploy` pour un changement DNS cohérent. Ne pas lancer `configure`, `zones`, `records` ou `services` seuls sur une installation de production. Une exécution sans `--tags` lance le parcours complet ; `routes-recover` reste exclu. `--tags deploy` seul n'installe pas le moniteur, même si son booléen est vrai : ajouter `monitor` explicitement.

## 🗂️ Inventaire et démarrage

```text
inventory/
  dev/{hosts.ini,group_vars/,host_vars/}
  preprod/{hosts.ini,group_vars/,host_vars/}
  prod/{hosts.ini,group_vars/,host_vars/}
playbooks/bind9-deploy.yml
roles/bind9/
```

Chaque site appartient aux groupes de rôle ; l'ordre du groupe `dns_deployment_order` place ses primaires avant ses secondaires. Les exemples contiennent site1/site2 en dev et site1/site3 en préproduction/production. Utiliser **un environnement par exécution**, remplacer les adresses et suffixes d'exemple, conserver un secret distinct par environnement/site. Ne pas mélanger plusieurs inventaires lors d'un déploiement.

Depuis la racine du projet sur le contrôleur Linux :

```bash
ansible --version
ansible-inventory -i inventory/dev/hosts.ini --graph
ansible-playbook -i inventory/dev/hosts.ini playbooks/bind9-deploy.yml --syntax-check
ansible -i inventory/dev/hosts.ini dns_deployment_order -m ping
ansible-playbook -i inventory/dev/hosts.ini playbooks/bind9-deploy.yml --limit site1 --tags deploy
```

Le primaire d'un site doit être opérationnel avant de déployer son secondaire. En cas de mise à niveau depuis la livraison loopback, les exemples incrémentent le serial autoritatif à `2026101401` pour publier les nouvelles délégations. Appliquer la même discipline à vos propres zones, avec un serial supérieur au serial réellement servi.

## 🔐 Secrets et Ansible Vault

Le rôle génère atomiquement une clé TSIG persistante sur le contrôleur, la partage entre les deux pairs du site et la charge avec `no_log`. Le répertoire contrôleur est `0700`, la clé est privée, `/etc/named.conf` est `root:named 0640`. Les backups contiennent cette clé dans la configuration : ils sont sensibles malgré leur empreinte SHA-256, qui n'est pas un chiffrement.

**Vault n'est pas intégré comme fournisseur TSIG** : le déploiement recharge `bind_tsig_secret` depuis `bind_tsig_secret_file`. Ne pas chiffrer ce fichier brut avec Vault, car son outil de génération lit une chaîne Base64. Ne pas annoncer qu'une variable `vault_tsig_secret` est consommée par le rôle.

Vault peut protéger les variables Ansible standard de connexion et de sudo. Exemple de procédure, sans secret dans les arguments ou dans le dépôt :

```bash
mkdir -p secrets
chmod 700 secrets
ansible-vault create secrets/prod.vault.yml
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --tags deploy -e @secrets/prod.vault.yml --ask-vault-pass
```

Dans l'éditeur Vault, définir uniquement les variables nécessaires, telles que `ansible_become_password` ou `ansible_password`. La clé SSH est préférable lorsque prévue par votre environnement. Un préflight isolé exige `bind_tsig_secret` fourni par une source protégée ; il ne provisionne pas automatiquement ce fait. Protéger les secrets hors Git et ZIP ; ne jamais utiliser `dig -y`, ni afficher la clé ou une configuration complète contenant `secret` dans les journaux.

## 🛡️ Sécurité et fonctionnement DNS

Les ACL `bind_primary_back_ip`, `bind_secondary_back_ip` et `bind_servers_back_ip` sont générées pour les deux pairs BACK du site courant. Seule l’ACL FRONT locale est déclarée : `bind_primary_front_ip` sur le primaire, `bind_secondary_front_ip` sur le secondaire. L’agrégat FRONT inutilisé est supprimé. Elles remplacent les IP dans les listes d'accès compatibles ; l'écoute utilise les ACL individuelles du rôle local. Ce sont des noms BIND, sans nouvelles variables d'inventaire. Les directives de transport nécessitant une IP explicite la conservent. Membres et applications : [ACL_SERVEURS.md](docs/ACL_SERVEURS.md).

Sans vues ni `in-view`, chaque zone est déclarée une seule fois. Loopback,
pairs BACK et clients de la fusion peuvent interroger les zones autoritatives
sur les adresses d’écoute autorisées. Pour la récursion et le cache, FRONT est
admis si des clients cloud existent, BACK si des clients internes existent,
et le loopback reste admis. Les pairs BACK et la clé TSIG restent exclus de
la récursion. Ces ACL source et destination sont indépendantes : avec les
deux listes actives, elles autorisent aussi les accès croisés des sources
déclarées. Le routage/filtrage existant doit donc assurer l’arrivée prévue
cloud → FRONT et interne → BACK si une isolation stricte est exigée ; le rôle
ne modifie pas le pare-feu. La racine privée conserve NXDOMAIN hors périmètre.

Le primaire autorise les transferts seulement depuis la source BACK du secondaire **avec** la clé TSIG. Ce pair authentifié peut utiliser toute adresse d'écoute du primaire, y compris FRONT : le retrait des vues supprime le filtre de destination des transferts. Les clients ordinaires restent refusés, même avec la clé. Les secondaires interdisent les transferts sortants, utilisent des masters signés et le primaire envoie des NOTIFY signés depuis BACK. BIND autorise implicitement les NOTIFY des masters ; la signature des transferts protège les données reçues. Une exigence BACK exclusivement doit être assurée par le filtrage réseau existant.

Le rôle n'active pas les mises à jour dynamiques, masque la version annoncée, restaure les labels SELinux et ajoute `/usr/local/sbin` au PATH de connexion via `/etc/profile.d/bind9-sbin-path.sh` uniquement s'il manque. Le pare-feu, le filtrage antispoofing, la capacité et la protection contre saturation restent à qualifier dans votre réseau. TSIG authentifie les échanges ; il ne chiffre pas les zones.

La validation est désactivée par défaut. En mode `auto`, le rôle utilise `bindkeys-file`; en mode `true`, il charge explicitement les ancres natives. Les modes actifs utilisent `/var/named/dynamic`, appartenant à named. Les données natives et la politique cryptographique EL8 ne sont pas modifiées. En mode actif, les exceptions ne concernent que les zones déclarées `dnssec_validate: false` et leurs descendants ; les signatures invalides d’une zone stricte restent rejetées. La racine privée bloque toujours la résolution Internet générale : le renouvellement RFC5011 des clés racine et les chaînes publiques ne sont pas garantis dans cette topologie cloisonnée. Une zone stricte exige une chaîne effectivement accessible ou une politique de confiance privée adaptée ; voir les limites dans [DNSSEC.md](docs/DNSSEC.md).

TSIG et la clé RNDC HMAC-SHA256 restent distincts ; RNDC administratif reste
sur 127.0.0.1 dans tous les modes. `bind_dnssec_active` accepte `false`
(défaut), `true` et `auto`. Le rôle rend respectivement `no`, `yes` et `auto`.
`false` n’installe aucun helper, hook ni unité DNSSEC ; une ancienne installation
gérée est arrêtée et retirée. `true` charge explicitement les ancres natives
RHEL ; `auto` utilise `bindkeys-file`. Les deux modes actifs gèrent les NTA
privées et leur renouvellement. Le journal d’appartenance et les fichiers
officiels sont conservés lors de la désactivation. L’écoute DNS IPv4 et le canal RNDC
restent dans un drop-in commun, séparé des hooks DNSSEC. Une modification des
drop-ins provoque un restart ; le reste utilise reload. Détails, migration et
limites : [DNSSEC.md](docs/DNSSEC.md).

## 🌐 Routes FRONT, sauvegarde et rollback

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --tags routes --check -v \
  -e '{"bind_front_routes_enabled": true}'
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --tags routes \
  -e '{"bind_front_routes_enabled": true}'
```

Ces commandes activent explicitement le booléen, désactivé par défaut. Ne pas utiliser `-e bind_front_routes_enabled=true`, interprété comme chaîne en Ansible 2.9. La première observe le réseau et calcule le plan sans écrire de route ou de journal. Le mode `--check` testé concerne uniquement `routes` ; le déploiement DNS complet en check mode n'est pas qualifié. La seconde ajoute uniquement les routes manquantes via la passerelle du profil actif, avec `nmcli connection modify ... +ipv4.routes` pour la persistance et `ip -4 route add ... via ... dev ... metric ... table main` pour le noyau. Le rollback utilise une suppression exacte avec `ip -4 route del`. Aucun up/down/reapply du profil n'est utilisé par le rôle. Les routes par défaut et les profils BACK/ADM sont conservés. Un réseau client directement connecté ne demande pas de nouvelle route.

Le routage est désormais implémenté exclusivement par des tâches Ansible et des expressions Jinja standard, sans filtre Python personnalisé ni dépendance supplémentaire. La sélection utilise `bind_front_ip`, y compris lorsqu’il s’agit d’une adresse secondaire. Le profil actif est identifié par UUID ; sa passerelle vient de `ipv4.gateway`. Le plan est typé et contrôlé avant toute mutation. Un résultat vide/invalide provoque un refus explicite. Les observations invalides ou une passerelle absente sont refusées avant création du journal ; faute des traces originales, la cause de votre incident reste non déterminée. `-v` donne un résumé exploitable des ajouts proposés.

La table principale IPv4 et le routage classique sont le périmètre pris en charge ; VRF, policy routing, ponts ambiguës, conflits et chemins SSH susceptibles d'être modifiés provoquent un refus explicite. Le journal root est `/var/lib/Bind9-Ansible/front-routes.lock/plan.json`. Un arrêt brutal bloque les nouveaux ajouts et demande une reprise explicite après inspection :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --limit site1-dns01 --tags routes-recover
```

Adapter `--limit` à un nom **réel** de l'inventaire. En déploiement complet, le journal reste présent jusqu'aux contrôles DNS réussis. Un échec du bloc protégé retire uniquement les ajouts actifs/persistants de cette exécution, puis tente la restauration BIND après suspension du renouvellement DNSSEC et arrêt de named. Après restauration et démarrage de named, le timer est relancé seulement s’il était actif. Les routes préexistantes restent en place ; une suppression impossible conserve son journal et produit une erreur explicite. Avec le tag `routes` seul, le commit suit les contrôles réseau.

Le rollback est local à l'hôte en cours, pas une transaction distribuée sur le site : les hôtes déjà déployés ne sont pas automatiquement restaurés. La restauration manuelle `bind-configtool restore` porte sur les fichiers BIND et ne retire pas des routes déjà committées. Détails et commandes : [FRONT_ROUTES.md](docs/FRONT_ROUTES.md), [OPERATIONS.md](docs/OPERATIONS.md).

## 🧪 Tests, exploitation et limites de validation

```bash
python -m pytest -q tests
python tests/integration_preflight.py
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --syntax-check
sudo named-checkconf -z /etc/named.conf
dig -b 127.0.0.1 @127.0.0.1 localhost A +norecurse
dig -b 127.0.0.1 @127.0.0.1 1.0.0.127.in-addr.arpa PTR +tcp +norecurse
```

Les tests contrôleur nécessitent pytest, PyYAML et Jinja2 ; les workflows Ansible de routes demandent Linux root et un contrôleur Ansible local. Les doubles CLI testent l'orchestration ; un autre laboratoire utilise **vraiment** NetworkManager. Les scripts d'intégration créent leurs espaces réseau isolés ; prérequis et procédures dans [VALIDATION_RHEL8.md](docs/VALIDATION_RHEL8.md). `integration_preflight.py` exécute les vraies assertions sur les inventaires avec des faits RHEL simulés. Les commandes de recette et résultats attendus sont dans [TESTS_FONCTIONNELS.md](docs/TESTS_FONCTIONNELS.md).

Après déploiement : vérifier `systemctl status named`, `journalctl -u named`, les réponses locales/FRONT/BACK, l'égalité des SOA entre pairs, les refus AXFR et les routes source FRONT. Le timer facultatif écrit dans le journal systemd ; aucune alerte externe ou supervision centrale n'est fournie. La présence du drapeau `aa`, du bon statut et du bon record compte davantage qu'un code de sortie `dig` nul.

Depuis un client FRONT autorisé avec la clé protégée :

```bash
/usr/local/sbin/bind-configtool check-front-axfr 192.0.2.11 \
  site1.prod.example.test 198.51.100.10 /chemin/protege/tsig.key
```

Adapter les valeurs d'exemple. Codes : 0 = refus DNS explicite dans les deux essais ; 1 = donnée de zone transférée ; 2 = résultat incomplet/ambigu ou problème TSIG. Une panne réseau, une sortie vide ou « Transfer failed » seul ne prouvent pas un refus. L'outil emploie `dig +yaml` si disponible ; certaines versions anciennes de dig ne rendent pas le RCODE d'un AXFR refusé et restent inconclusives. Un résultat 1 bloque la mise en production ; un résultat 2 exige une preuve DNS explicite complémentaire.

| État | Contrôles |
|---|---|
| **EXÉCUTÉ** | Routage natif sans netaddr ni filtre personnalisé : scénarios Ansible réels, tests d’expressions YAML, idempotence et reprise sélective ; résultats courants dans [CLIENTS_UNIQUES.md](docs/CLIENTS_UNIQUES.md) et DELIVERY.json. Couverture des quatre modules Python restants : 99 %, seuil 98 %. DNS natif EL8 et BIND 9.18 : chacun 109 configurations et 282 contrôles |
| **NON EXÉCUTÉ** | Déploiement RHEL 8 réel et modules Python/DNF, crypto-policy générée/FIPS, SELinux, pare-feu, activation réelle des overrides/timers, redémarrage serveur, renouvellement sur sept jours, RFC5011 et chaînes DNSSEC cibles, deuxième deploy complet, restauration opérationnelle, charge et CI distante |

Les rapports fournissent pour chaque contrôle manquant la raison, les prérequis, la procédure, le résultat attendu et le risque restant. Les 109 configurations couvrent les branches du template ; elles ne prouvent pas toutes les combinaisons arbitraires d'inventaire ni tous les incidents réseau. La sauvegarde à chaud d'un secondaire ne garantit pas un instantané atomique de ses journaux BIND ; qualifier la restauration et votre RPO/RTO avant production.

## 📦 Livraison et traçabilité

Cette correction part de `Bind9-Ansible-consolide-dnssec-transport.zip`, conservée intacte. Le fichier original sur D: n'a pas été relu. `DELIVERY.json` décrit provenance et différences ; `SOURCE-MANIFEST.sha256` décrit les fichiers ; le ZIP possède une empreinte SHA-256 externe. Caches, journaux, runtimes et secrets générés sont exclus. Le helper et les unités DNSSEC ne sont déployés que pour `true`/`auto` ; les transitions et leur rollback sont intégrés au parcours deploy ; le filtre Python de routage est supprimé également du contrôleur. Les scripts Python nécessaires au DNS restent distincts de cette évolution. Les tâches de migration et les fonctions optionnelles utilisées sont conservées. Les déclarations d’ACL sans consommateur sont retirées ; le contrat client distingue cloud et internes, avec fusion calculée pour DNS et routage exclusivement cloud.

Les tests sont fournis avec les sources. Le nom public du projet reste **Bind9-Ansible** et le playbook reste **bind9-deploy.yml**. Les métadonnées du rôle déclarent `license: proprietary` ; aucun texte de licence détaillant les droits n'est fourni dans cette archive.

Cette révision durcit les décisions DNSSEC et utilise `bind_dnssec_active` comme unique entrée publique :
les valeurs `false`, `true`, `auto` et le défaut `false` sont conservés.
Mettre à jour vos inventaires et commandes avec ce nom. Pour activer le mode
automatique, déclarer `bind_dnssec_active: auto`. Les six fichiers
runtime gérés sont capturés en mémoire avant modification et restaurés par le
rescue, avec le précédent état du timer. Cette capture ne remplace pas une
sauvegarde persistante et ne survit pas à l’arrêt du contrôleur.

Les faits de mode et d’activation intermédiaires sont supprimés. Les contrôles
de types sont compatibles avec Jinja 2.10 ; les décisions d’activation prennent
explicitement en compte `auto`, après validation stricte. Les fichiers et blocs
impactés sont détaillés dans [DURCISSEMENT_DNSSEC.md](docs/DURCISSEMENT_DNSSEC.md).
Les preuves et limites sont distinguées dans `DELIVERY.json` et
[VALIDATION_RHEL8.md](docs/VALIDATION_RHEL8.md).

La correction des conditions de santé et du contrôle de la configuration, les parties du code impactées et les preuves courantes figurent dans [CORRECTION_CONTROLES_DNS.md](docs/CORRECTION_CONTROLES_DNS.md). Elle corrige les faux refus dus à Jinja sans désactiver les contrôles DNS. Le fichier réellement présent sur votre serveur reste à vérifier selon la procédure fournie.
