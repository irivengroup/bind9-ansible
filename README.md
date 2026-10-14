# 🏷️ Bind9-Ansible

> Déployer et exploiter une paire BIND 9 par site, avec résolution privée, réplication TSIG, loopback natif RHEL et routes de retour FRONT ajoutées sans reconfigurer le réseau existant.

Le point d'entrée est `playbooks/bind9-deploy.yml`. Le rôle produit une configuration canonique `/etc/named.conf` depuis `roles/bind9/templates/named.conf.j2`. Cette livraison inclut `local_loopback` et les six ACL des adresses FRONT/BACK des serveurs, les sources nettoyées, les tests et les procédures de reprise. La qualification sur vos serveurs reste **NON TERMINÉE**. Les contrôles actuels sont décrits dans [ACL_SERVEURS.md](docs/ACL_SERVEURS.md) ; les preuves précédentes figurent dans [ACL_LOOPBACK.md](docs/ACL_LOOPBACK.md), [VALIDATION_NETTOYAGE.md](docs/VALIDATION_NETTOYAGE.md), [VALIDATION_NAMED.md](docs/VALIDATION_NAMED.md) et [VALIDATION_FRONT_ROUTES.md](docs/VALIDATION_FRONT_ROUTES.md).

## 📊 Matrice de compatibilité

| Composant | Périmètre et preuve |
|---|---|
| OS cible | **Red Hat Enterprise Linux 8**, demandé ; déploiement réel **NON EXÉCUTÉ**. Le garde du rôle accepte `RedHat` de version majeure ≥ 8 ; cela ne qualifie pas RHEL 9/10. Rocky, AlmaLinux et Ubuntu sont refusés par ce garde. |
| Ansible | Ligne **2.9** demandée ; contrôles locaux : **EXÉCUTÉ**, avec **2.9.27**. Aucune affirmation sur tous les autres correctifs 2.9. |
| Python cible | **3.6.8**, fourni par l'utilisateur ; grammaire du script cible contrôlée ; exécution sur cet interpréteur : **NON EXÉCUTÉ**. |
| Contrôleur de test | Linux, Python **3.8.20**, Jinja2 **3.0.3**, Ansible **2.9.27**. |
| BIND de laboratoire | **9.18.24** ; configurations chargées, requêtes UDP/TCP, forwarding et réplication exécutés. La version du paquet BIND RHEL cible reste à relever et tester. |
| NetworkManager de laboratoire | **1.46.0** ; ajout natif, persistance et reprise exécutés. La version installée sur les cibles reste à qualifier. |
| Transport DNS / routes | IPv4. Les enregistrements AAAA et PTR IPv6 sont possibles ; aucune écoute DNS IPv6 ni route de retour IPv6 n'est ajoutée. |

La matrice GitHub Actions contient Ansible 2.9.27, 2.13.13, 2.14.18 et 2.16.14 avec plusieurs Python de contrôleur. Sa présence n'est pas une preuve d'exécution CI ni de compatibilité OS. Ces runtimes anciens imposés par le contexte demandent une maintenance contrôlée des dépendances.

Version minimale déclarée dans les métadonnées du rôle : **Ansible 2.9**. La version effectivement utilisée pour cette recette est **2.9.27**.

## 🔌 Prérequis & dépendances

- Contrôleur Linux avec Ansible, Python, accès SSH aux adresses ADM et droit `become: true` sur les cibles. Aucun rôle externe ni collection Galaxy supplémentaire n'est utilisé.
- Cibles : Python utilisable par Ansible, DNF et ses bindings Python, systemd, SELinux, dépôts RHEL accessibles. Le rôle installe `bind`, `bind-utils` et `policycoreutils-python-utils`.
- FRONT, BACK et ADM sont **déjà configurés**, avec des adresses distinctes. NetworkManager gère FRONT, son profil actif contient la passerelle IPv4 existante ; `nmcli` et `ip` avec sortie JSON sont présents. Le rôle n'installe ni ne reconfigure NetworkManager.
- Réseau : SSH sur ADM ; DNS UDP/TCP 53 vers les interfaces et résolveurs autorisés ; TCP 53 entre les pairs BACK pour les transferts, UDP 53 pour NOTIFY. Le pare-feu, les IP et `/etc/resolv.conf` restent sous votre gestion.
- Fichiers natifs du paquet présents : `/etc/named.rfc1912.zones`, `/var/named/named.localhost`, `named.loopback`, `named.empty`. Le rôle les vérifie sans les remplacer.
- Un primaire et un secondaire par site. Déploiement primaire avant secondaire, via `dns_deployment_order`, `serial: 1`, `order: inventory`, `any_errors_fatal: true`.
- Horloges des pairs synchronisées, notamment pour la fenêtre de validité TSIG. Le rôle ne configure pas le service de synchronisation horaire.
- Espace disque pour les archives BIND ; sauvegardes protégées et exportées selon votre politique. Le rôle garde 30 jours d'archives, sans plafond en nombre.

`ansible.cfg` choisit `auto_silent` pour l'interpréteur. Si nécessaire, déclarer `ansible_python_interpreter: /usr/bin/python3` dans les variables de vos hôtes et vérifier que ce chemin correspond réellement à votre Python 3.6.8 et que les modules DNF fonctionnent.

## 🛠️ Variables du rôle / playbook

Les valeurs ci-dessous correspondent aux fichiers inspectés. « Absente » signifie qu'aucune valeur n'est fournie dans `defaults/main.yml`.

| Variable | Type | Description | Valeur par défaut | Obligatoire ? |
|---|---|---|---|---|
| `bind_frontend_clients` | liste de chaînes | Sources IPv4 autorisées sur FRONT ; destinations des routes de retour | `[]` | Au moins une des deux listes clients non vide |
| `bind_backend_clients` | liste de chaînes | Sources IPv4 autorisées sur BACK | `[]` | Même contrainte |
| `bind_forward_zones` | liste d'objets | Suffixes envoyés uniquement aux forwarders déclarés | `[]` | Non |
| `bind_authoritative_zones` | liste d'objets | Zones primaires, records et serials ; PTR générés automatiquement | `[]` | Non |
| `bind_front_route_metric` | entier | Métrique des **nouvelles** routes FRONT ; les routes existantes gardent la leur | `100` | Non |
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

### Schéma des zones

- Zone autoritative : `domain`, `serial` obligatoires ; `ttl` vaut `300` par défaut ; `records` vaut `[]`. Serial entier positif ≤ 4294967295. Incrémenter le serial lors de toute modification des données autoritatives ou des délégations.
- Record : `name`, `type`, `value`. Types admis : `A`, `AAAA`, `CNAME`, `TXT`, `MX`, `PTR`, `SRV`, `CAA`. Fournir le RDATA BIND complet, avec les guillemets TXT et noms absolus terminés par un point lorsque nécessaires. Les adresses et caractères de contrôle sont vérifiés ; BIND vérifie ensuite les fichiers générés.
- Les records A génèrent des zones inverses `/24`, les AAAA des zones `/64`. Les champs `mask` par record sont refusés. Les PTR contradictoires sont refusés. Le serial inverse est conservé ou avancé selon les différences avec la sauvegarde réelle.
- Zone forward : `domain`, `forwarders` non vides ; `policy: only` uniquement. Chaque serveur a `address` littérale unicast et `port`, entier ou chaîne numérique, défaut `53`, intervalle 1–65535. Une destination visant l'un des pairs DNS sur son port 53 est refusée pour éviter une boucle.
- `dnssec_validate` : booléen par zone forward, défaut **`true`**. Pour vos zones privées non signées confirmées, `false` produit une exception `validate-except` limitée au suffixe et ses descendants. Les six exemples `.test` la déclarent explicitement ; aucune désactivation DNSSEC globale.
- Les zones natives sont réservées. Les doublons sans distinction de casse, forwards imbriqués, forwards englobant une autorité et données autoritatives cachées sous un suffixe forward sont refusés. Les enfants forwardés d'une autorité sont permis avec délégation NS/glue générée dans le parent le plus proche ; les autres ont une délégation dans la racine privée.
- Limites de longueur dues aux noms NS générés : 249 caractères pour une autorité, 235 pour un suffixe forward. Les ACL `/0`, clients IPv6 et entrées ambiguës sont refusés.

Voir les exemples réellement livrés dans `inventory/dev/group_vars/site1.yml` et [VALIDATION_NAMED.md](docs/VALIDATION_NAMED.md).

## 🏷️ Tags disponibles

| Tag | Effet / précaution |
|---|---|
| `deploy` | Parcours complet : TSIG, contrôles, paquets, sauvegarde, routes, configuration, zones, SELinux, validation, service et santé ; rollback protégé |
| `routes` | Seules les routes de retour IPv4 FRONT, avec contrôles et journal ; aucun déploiement de configuration DNS |
| `routes-recover` | Reprise explicite d'un journal de routes interrompu ; marqué `never`, exclu du parcours normal |
| `preflight` | Validation d'entrées, chemins et disponibilité du primaire ; nécessite déjà les utilitaires et le secret TSIG fourni pour cette exécution |
| `install` | Installation des trois paquets RHEL |
| `backup` | Installation de l'outil et sauvegarde forcée, avec rétention ; requiert les utilitaires BIND |
| `configure` | TSIG, répertoires, configuration, zones et labels ; isolé, ne garantit pas sauvegarde/santé |
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

Les ACL `bind_primary_back_ip`, `bind_secondary_back_ip`, `bind_primary_front_ip`, `bind_secondary_front_ip`, `bind_servers_back_ip` et `bind_servers_front_ip` sont générées à partir des deux pairs du site courant. Elles remplacent les IP dans les listes d'accès compatibles ; l'écoute et les destinations des vues utilisent les ACL individuelles du rôle local. Ce sont des noms BIND, sans nouvelles variables d'inventaire. Les directives de transport nécessitant une IP explicite la conservent. Membres et applications : [ACL_SERVEURS.md](docs/ACL_SERVEURS.md).

La sélection des vues dépend de la source **et** de la destination : `peer` sur BACK, `local` depuis/vers 127.0.0.1, `external` sur FRONT, `internal` sur BACK, puis `deny`. Les vues clientes n'existent que si leur liste est non vide. La récursion et les transferts sont refusés globalement ; la récursion autorisée reste limitée aux clients déclarés et aux suffixes forwardés. La racine privée retourne NXDOMAIN pour les autres noms : ce projet ne fournit pas un résolveur Internet général.

Les zones sont partagées par `in-view "peer"`, qui conserve les permissions de la zone d'origine. La règle primaire AXFR exige simultanément la source BACK du secondaire **et** la clé TSIG. La vue FRONT exclut également les adresses BACK des pairs et leur clé de transfert. Les secondaires interdisent les transferts, utilisent des masters signés et le primaire envoie des NOTIFY signés depuis BACK. BIND autorise implicitement les NOTIFY des masters : `allow-notify` ne peut pas réduire cette autorisation implicite ; la signature des transferts protège les données reçues.

Le rôle n'active pas les mises à jour dynamiques, masque la version annoncée, restaure les labels SELinux et ajoute `/usr/local/sbin` au PATH de connexion via `/etc/profile.d/bind9-sbin-path.sh` uniquement s'il manque. Le pare-feu, le filtrage antispoofing, la capacité et la protection contre saturation restent à qualifier dans votre réseau. TSIG authentifie les échanges ; il ne chiffre pas les zones.

Pour les zones privées non signées, conserver l'exception DNSSEC ciblée. Les zones signées, chaînes de confiance privées et rotations TSIG demandent une recette et une procédure dédiées ; aucun support universel de ces scénarios n'est annoncé ici.

## 🌐 Routes FRONT, sauvegarde et rollback

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --tags routes --check
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --tags routes
```

La première commande observe le réseau et calcule le plan sans écrire de route ou de journal. Le mode `--check` testé ici concerne uniquement le tag `routes` ; le déploiement DNS complet en check mode n'est pas qualifié et ses étapes de sauvegarde/provisionnement peuvent échouer faute de sorties réelles. La seconde commande ajoute uniquement les routes manquantes via la passerelle du profil actif, avec `nmcli connection modify ... +ipv4.routes` et `nmcli device modify ... +ipv4.routes`. Aucun up/down/reapply du profil n'est utilisé par le rôle. Les routes par défaut et les profils BACK/ADM sont conservés. Un réseau client directement connecté n'a pas besoin d'une nouvelle route.

La table principale IPv4 et le routage classique sont le périmètre pris en charge ; VRF, policy routing, ponts ambiguës, conflits et chemins SSH susceptibles d'être modifiés provoquent un refus explicite. Le journal root est `/var/lib/Bind9-Ansible/front-routes.lock/plan.json`. Un arrêt brutal bloque les nouveaux ajouts et demande une reprise explicite après inspection :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --limit site1-dns01 --tags routes-recover
```

Adapter `--limit` à un nom **réel** de l'inventaire. En déploiement complet, le journal reste présent jusqu'aux contrôles DNS réussis. Un échec du bloc protégé retire uniquement les ajouts actifs/persistants de cette exécution, puis tente la restauration BIND. Les routes préexistantes restent en place ; une suppression impossible conserve son journal et produit une erreur explicite. Avec le tag `routes` seul, le commit suit les contrôles réseau.

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

Les tests contrôleur nécessitent pytest, PyYAML et Jinja2 ; les workflows Ansible de routes demandent Linux root et un contrôleur Ansible local. Les doubles CLI testent l'orchestration ; un autre laboratoire utilise **vraiment** NetworkManager. Les scripts d'intégration créent leurs espaces réseau isolés ; ne les lancer qu'avec les prérequis documentés dans les rapports. `integration_preflight.py` exécute les vraies assertions sur les inventaires avec des faits RHEL simulés, pas sur les cibles.

Après déploiement : vérifier `systemctl status named`, `journalctl -u named`, les réponses locales/FRONT/BACK, l'égalité des SOA entre pairs, les refus AXFR et les routes source FRONT. Le timer facultatif écrit dans le journal systemd ; aucune alerte externe ou supervision centrale n'est fournie. La présence du drapeau `aa`, du bon statut et du bon record compte davantage qu'un code de sortie `dig` nul.

Depuis un client FRONT autorisé avec la clé protégée :

```bash
/usr/local/sbin/bind-configtool check-front-axfr 192.0.2.11 \
  site1.prod.example.test 198.51.100.10 /chemin/protege/tsig.key
```

Adapter les valeurs d'exemple. Codes : 0 = refus DNS explicite dans les deux essais ; 1 = donnée de zone transférée ; 2 = résultat incomplet/ambigu ou problème TSIG. Une panne réseau, une sortie vide ou « Transfer failed » seul ne prouvent pas un refus. L'outil emploie `dig +yaml` si disponible ; certaines versions anciennes de dig ne rendent pas le RCODE d'un AXFR refusé et restent inconclusives. Un résultat 1 bloque la mise en production ; un résultat 2 exige une preuve DNS explicite complémentaire.

| État | Contrôles |
|---|---|
| **EXÉCUTÉ** | Livraison ACL serveurs : 201 tests relancés, syntaxe Ansible 2.9.27 sur trois inventaires, 44 configurations BIND chargées et 107 contrôles DNS réels. Livraison nettoyée précédente : suite de 207 tests, assertions des 12 hôtes, Pyflakes et essais NetworkManager ; périmètres détaillés dans les rapports |
| **NON EXÉCUTÉ** | Déploiement sur RHEL 8/Python 3.6.8, versions BIND/NM cibles, SELinux et pare-feu réels, redémarrage des serveurs, AXFR/IXFR et forwards réels, deuxième déploiement complet sur la paire, restauration opérationnelle, charge et CI distante |

Les rapports fournissent pour chaque contrôle manquant la raison, les prérequis, la procédure, le résultat attendu et le risque restant. Les 44 configurations couvrent les branches du template ; elles ne prouvent pas toutes les combinaisons arbitraires d'inventaire ni tous les incidents réseau. La sauvegarde à chaud d'un secondaire ne garantit pas un instantané atomique de ses journaux BIND ; qualifier la restauration et votre RPO/RTO avant production.

## 📦 Livraison et traçabilité

La livraison ACL serveurs est produite à partir de `Bind9-Ansible-acl-loopback.zip`, sans modifier cette archive ni les références du projet. `DELIVERY.json` décrit la provenance et les différences ; `SOURCE-MANIFEST.sha256` décrit les fichiers ; le ZIP a une empreinte SHA-256 externe. Les caches, logs de validation, environnements Python et secrets générés sont exclus. Les suppressions motivées et la correction SELinux de la livraison précédente figurent dans [VALIDATION_NETTOYAGE.md](docs/VALIDATION_NETTOYAGE.md) ; les tâches de migration utiles restent conservées.

Les tests sont fournis avec les sources. Le nom public du projet reste **Bind9-Ansible** et le playbook reste **bind9-deploy.yml**. Les métadonnées du rôle déclarent `license: proprietary` ; aucun texte de licence détaillant les droits n'est fourni dans cette archive.
