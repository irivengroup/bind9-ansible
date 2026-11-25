# DNSZIBO — Bind9-Ansible

DNSZIBO déploie et administre une paire de serveurs DNS BIND 9 par site. Le rôle
prend en charge les zones autoritatives privées, le forwarding conditionnel,
les enregistrements DNS et la réplication authentifiée entre primaire et secondaire.

Deux points d’entrée couvrent le cycle de vie de la configuration :

| Playbook | Usage |
|---|---|
| [`bind9-deploy.yml`](playbooks/bind9-deploy.yml) | Installer et configurer une paire à partir de l’état décrit dans l’inventaire. |
| [`bind9-management.yml`](playbooks/bind9-management.yml) | Lire, créer, modifier ou supprimer des ressources dans une configuration existante. |

La [documentation](docs/index.md) contient les procédures détaillées, les contrats
de configuration et les guides d’exploitation. Les adresses et domaines des
exemples sont fictifs : adaptez-les à votre infrastructure.

## Sommaire

- [Compatibilité](#compatibilite)
- [Architecture](#architecture)
- [Prérequis](#prerequis)
- [Inventaire et interfaces](#inventaire)
- [Configuration des ressources](#variables)
- [Secrets](#secrets)
- [Déploiement](#deploiement)
- [Gestion des ressources](#management)
- [Tags](#tags)
- [Opérations réseau](#operations)
- [Sécurité](#securite)
- [Journaux et supervision](#observabilite)
- [Contrôles de fonctionnement](#tests)
- [Sauvegarde et reprise](#reprise)
- [Diagnostic](#diagnostic)
- [Documentation de qualification](#validation)
- [Organisation et livraison](#livraison)

<a id="compatibilite"></a>
## Compatibilité

Le rôle accepte **Red Hat Enterprise Linux de version majeure 7 ou supérieure**.
Les outils de gestion et DNSSEC nécessitent **Python 3.6 ou supérieur** sur les
cibles. Le minimum Ansible déclaré est **2.9** ; choisissez un contrôleur compatible
avec le Python et le gestionnaire de paquets de vos serveurs.

Les écoutes DNS et les réseaux clients déclarés utilisent IPv4. Les forwarders,
le transport sortant et les données DNS peuvent utiliser IPv4 ou IPv6. Les routes
gérées par le rôle sont exclusivement IPv4.

Les versions effectivement exercées et le périmètre de chaque campagne sont
décrits dans la [documentation de qualification](docs/wiki.d/qualification/QUALIFICATION_ACTUELLE.md).

<a id="architecture"></a>
## Architecture

Chaque site comprend un primaire et un secondaire appartenant au même
environnement. Le rôle génère une configuration `/etc/named.conf` unique, sans
vues BIND, et conserve les zones localhost fournies par la distribution.

| Fonction | Comportement |
|---|---|
| Autorité | Zones directes sur le primaire, réplication vers le secondaire et génération des inverses depuis les enregistrements A/AAAA du déploiement. |
| Forwarding | Politique `forward only` vers les résolveurs déclarés pour chaque suffixe. |
| Résolution hors périmètre | Réponse `NXDOMAIN` par une racine privée pour les noms non déclarés. |
| DNSSEC | Validation en mode `auto` par défaut, avec gestion des exceptions privées déclarées. |
| Administration | SSH sur l’adresse ADMIN et RNDC local sur `127.0.0.1:953`. |

Les interfaces FRONT et BACK peuvent être distinctes ou partager une même écoute.
Les autorisations clientes reposent sur les réseaux déclarés ; les politiques
réseau déterminent les chemins d’accès aux interfaces.

Consultez la [référence d’architecture](docs/wiki.d/exploitation/REFERENCE_ROLE.md#architecture)
et le [contrat des ACL](docs/wiki.d/configuration/reseau/ACL_SERVEURS.md).

<a id="prerequis"></a>
## Prérequis

Sur le contrôleur :

- Linux, Ansible et Python compatibles avec les cibles ;
- accès SSH et élévation de privilèges sur les deux serveurs ;
- inventaire complet de la paire, y compris lors d’une exécution limitée à un hôte ;
- stockage privé pour le secret TSIG persistant.

Sur les serveurs :

- systemd, dépôts RHEL accessibles et Python adapté aux modules Ansible ;
- firewalld installé et actif ;
- adresses IPv4 d’écoute et d’administration déjà configurées ;
- outils natifs de sauvegarde et de verrouillage, horloges synchronisées et espace disque disponible ;
- fichiers BIND natifs et, lorsque DNSSEC est actif, ancres de confiance à jour.

Le rôle installe `bind`, `bind-utils`, `python3` et l’outillage SELinux natif. Les
routes FRONT optionnelles nécessitent aussi NetworkManager actif. La liste
complète des paquets, fichiers et flux requis figure dans les
[prérequis détaillés](docs/wiki.d/exploitation/REFERENCE_ROLE.md#prerequis).

<a id="inventaire"></a>
## Inventaire et interfaces

Les environnements fournis sont `inventory/dev`, `inventory/preprod` et
`inventory/prod`. **Indiquez toujours `-i`** : la configuration Ansible livrée
sélectionne la production par défaut. Utilisez un seul environnement par exécution.

Chaque site possède exactement un membre de `dns_primary` et un membre de
`dns_secondary`. Dans `dns_deployment_order`, placez le primaire avant le secondaire.
Le déploiement traite les hôtes un par un et arrête la suite après un échec.

### Structure commune des interfaces

Déclarez les écoutes dans `host_vars` avec une liste d’objets
`bind_listen_interfaces`. Les noms d’interfaces dépendent de l’inventaire.

**Une seule interface :**

```yaml
ansible_host: 10.10.1.11
bind_listen_interfaces:
  - name: ens192
```

Cette forme convient lorsque l’interface possède une seule IPv4 utilisable.
Avec plusieurs IPv4, ajoutez `ipaddress` pour sélectionner l’adresse d’écoute.
Une écoute unique assure les rôles FRONT et BACK ; aucune route cliente n’est ajoutée.

**Plusieurs écoutes et administration distincte :**

```yaml
ansible_host: 10.10.1.11
bind_listen_interfaces:
  - name: ens256
    ipaddress: 192.0.2.11
    scope: front
  - name: ens224
    ipaddress: 10.20.1.11
    scope: back
```

**Connexion SSH par nom :**

```yaml
ansible_host: dns01.example.test
bind_admin_ip: 10.10.1.11
bind_listen_interfaces:
  - name: ens256
    ipaddress: 192.0.2.11
    scope: front
  - name: ens224
    ipaddress: 10.20.1.11
    scope: back
```

`bind_admin_ip` désigne l’IPv4 locale d’administration lorsque la destination SSH
est un nom ou une adresse traduite. Avec plusieurs écoutes, identifiez une entrée
`front` et une entrée `back` ; les écoutes supplémentaires peuvent omettre `scope`.

Les deux pairs doivent être **ISO** : mêmes nombres d’interfaces IPv4 utilisables
et d’écoutes, même affectation DNS/ADMIN. Les noms et adresses peuvent différer.
Consultez le [guide des interfaces](docs/wiki.d/configuration/reseau/INTERFACES_RESEAU.md)
et les [six exemples de paires](examples/interfaces/README.md).

<a id="variables"></a>
## Configuration des ressources

Placez les paramètres communs dans `group_vars` et les sélections propres aux
serveurs dans `host_vars`. Les [valeurs par défaut](roles/bind9/defaults/main.yml)
et la [référence des variables](docs/wiki.d/exploitation/REFERENCE_ROLE.md#variables)
décrivent le contrat complet.

### Réseaux clients autorisés

```yaml
bind_trusted_networks:
  cloud_clients:
    - 198.51.100.0/24
  internal_clients:
    - 10.30.1.0/24
```

Les deux listes sont vides par défaut ; **au moins une doit être renseignée pour
déployer**. Elles acceptent des IPv4 ou des CIDR canoniques. Le rôle calcule et
déduplique leur union. Les clients internes utilisent le routage existant du
serveur : aucune route n’est créée pour eux.

### État initial et gestion ciblée

`bind_resources_deploy` décrit l’état à construire au déploiement.
`bind_resources_manage` reçoit les opérations de gestion sur l’état existant.
Les deux entrées partagent le même schéma de ressources : **ACL, options,
journalisation, zones et enregistrements**.

```yaml
bind_resources_deploy:
  resource: batch
  action: create
  operations:
    - resource: zone
      action: create
      name: service.example.test
      kind: authoritative
      ttl: 300
      serial: 1
    - resource: record
      action: create
      zone: service.example.test
      name: app
      type: A
      ttl: 300
      values: ['192.0.2.80', '192.0.2.81']
    - resource: zone
      action: create
      name: private.example.test
      kind: forward
      policy: only
      dnssec_validate: false
      forwarders:
        - address: 10.11.0.53
          port: 53
```

Adaptez les upstreams à des serveurs joignables en UDP et TCP. Pour les champs,
types d’enregistrements, serials, délégations et inverses, utilisez le
[contrat des ressources](docs/wiki.d/configuration/bind/RESSOURCES.md) et le
[lot de déploiement complet](examples/deployment/batch.yml).

<a id="secrets"></a>
## Secrets

Définissez pour chaque environnement et site `bind_tsig_key_name`,
`bind_tsig_algorithm: hmac-sha256` et `bind_tsig_secret_file`, chemin absolu
privé sur le contrôleur. Les deux pairs partagent cette clé de transfert.
Le rôle crée le secret s’il manque, puis le vérifie et le réutilise.

Le fichier contient uniquement le secret Base64 ; protégez-le et sauvegardez-le
séparément dans un stockage chiffré. La clé RNDC native reste distincte.
Ansible Vault peut protéger les identifiants de connexion et d’élévation ;
le lecteur TSIG attend un fichier Base64 brut, pas un fichier chiffré par Vault.

La [procédure TSIG et Vault](docs/wiki.d/exploitation/REFERENCE_ROLE.md#secrets)
décrit la préparation, les permissions et la rotation coordonnée.

<a id="deploiement"></a>
## Déployer une paire

Exécutez les commandes suivantes sur le contrôleur Linux, depuis la racine du
projet, après adaptation de l’inventaire, des clients, des ressources et du TSIG.
Préparez une fenêtre exclusive et les sauvegardes nécessaires au retour arrière.

```bash
ansible-inventory -i inventory/prod/hosts.ini --graph
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --syntax-check
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --list-hosts --limit site1
ansible -i inventory/prod/hosts.ini site1 -m ping
ansible -i inventory/prod/hosts.ini site1 -b -m command -a 'id -u'

ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --limit site1 --tags deploy
```

Ajoutez `--ask-become-pass` si nécessaire ou utilisez vos variables Vault
protégées. Le [parcours de déploiement](docs/wiki.d/exploitation/REFERENCE_ROLE.md#deploiement)
précise les étapes, contrôles et conditions d’arrêt.

Pour poser un sceau après un déploiement réussi, ajoutez
`-e hibernate_deployment=yes`. **Un sceau existant bloque toute reconstruction**,
même avec `hibernate_deployment=no`. Son retrait relève d’une intervention
explicite décrite dans le [guide d’hibernation](docs/wiki.d/exploitation/HIBERNATE_DEPLOYMENT.md).

<a id="management"></a>
## Gérer les ressources existantes

Utilisez le playbook de management pour les lectures, listings paginés et
opérations CRUD ciblées. Il conserve les clauses non visées et n’applique pas
le template de déploiement global.

Exemple de listing avec la requête fournie :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-management.yml \
  --limit site1 -e @examples/management/list-zones.yml
```

Une écriture exige le checksum courant de chaque hôte ; l’application d’un lot
exige aussi l’empreinte du plan approuvé. Les validations de la paire précèdent
la publication. Consultez les procédures de [gestion unitaire](docs/wiki.d/exploitation/MANAGEMENT.md)
et de [lots et listings](docs/wiki.d/exploitation/MANAGEMENT_LOTS_ET_LISTES.md)
avant une modification.

<a id="tags"></a>
## Tags

| Tag | Usage |
|---|---|
| `deploy` | Parcours complet de déploiement. |
| `hibernate` | Poser le sceau sur une configuration existante, sans redéploiement. |
| `firewall` | Compléter les autorisations DNS runtime et permanentes. |
| `routes` | Gérer les routes FRONT optionnelles. |
| `routes-recover` | Reprendre explicitement une transaction réseau interrompue. |
| `monitor` | Installer le moniteur lorsque `bind_monitoring_enabled: true`. |

Le [catalogue complet des tags](docs/wiki.d/exploitation/REFERENCE_ROLE.md#tags)
précise les prérequis des parcours partiels. Pour le parcours normal, utilisez
`--tags deploy`. Ajoutez `monitor` pour installer également le moniteur et
conservez les tâches `always`.

<a id="operations"></a>
## Opérations réseau

Les routes FRONT sont désactivées par défaut. Leur activation concerne uniquement
les destinations `bind_trusted_networks.cloud_clients` et nécessite des chemins
FRONT, BACK et ADMIN distincts. Les chemins partagés et l’interface unique
n’ajoutent aucune route cliente.

Le rôle identifie le profil NetworkManager actif et recherche la passerelle dans
la table de routage principale de l’interface FRONT. Il préserve la route par
défaut et les chemins ADMIN/BACK. Les clients internes conservent toujours leur
routage existant.

Le [guide FRONT](docs/wiki.d/configuration/reseau/FRONT_ROUTES.md) décrit la
prévisualisation, l’activation, le journal et la reprise. Ne supprimez pas un
journal pour contourner une transaction interrompue.

<a id="securite"></a>
## Sécurité

Le rôle applique les ACL clientes, authentifie les transferts de zones par TSIG,
interdit les mises à jour dynamiques et conserve RNDC sur loopback. Il ajoute
le service `dns` dans les zones firewalld concernées, en runtime et en permanent,
et applique les contextes SELinux lorsque celui-ci est actif.

Préparez les politiques réseau externes, restreignez l’accès aux configurations
et sauvegardes contenant des secrets, et conservez les protections système.
La [référence de sécurité](docs/wiki.d/exploitation/REFERENCE_ROLE.md#securite)
et les fiches [ACL](docs/wiki.d/configuration/reseau/ACL_SERVEURS.md),
[firewalld](docs/wiki.d/configuration/reseau/FIREWALLD.md) et
[DNSSEC](docs/wiki.d/configuration/bind/DNSSEC.md) détaillent ces mécanismes.

<a id="observabilite"></a>
## Journaux et supervision

Les journaux BIND sont regroupés dans `/var/log/named` : `default.log`,
`security.log` et `xfer.log`. BIND assure leur rotation par taille.

Le moniteur périodique est optionnel. Pour l’installer avec le déploiement,
définissez `bind_monitoring_enabled: true` et utilisez `--tags deploy,monitor`.
Le script `bind-healthcheck.sh` est installé sous
`/usr/local/sbin/bind-healthcheck` ; les unités existantes restent
`named-healthcheck.service` et `named-healthcheck.timer`.

Consultez la [journalisation](docs/wiki.d/configuration/bind/JOURNALISATION.md)
et la [supervision](docs/wiki.d/exploitation/REFERENCE_ROLE.md#observabilite)
pour la rétention, la migration du moniteur et les contrôles à compléter.

<a id="tests"></a>
## Contrôles de fonctionnement

Après un changement, vérifiez les réponses DNS depuis chaque serveur et depuis
les segments clients : localhost, autorité, forwarding, réplication et refus
des accès non autorisés. Contrôlez aussi firewalld et les routes activées.

Les [commandes et résultats attendus](docs/wiki.d/exploitation/REFERENCE_ROLE.md#tests)
et les [tests fonctionnels](docs/wiki.d/qualification/TESTS_FONCTIONNELS.md)
permettent de préparer cette recette.

<a id="reprise"></a>
## Sauvegarde et reprise

Le déploiement sauvegarde la configuration BIND et les zones avant leur
modification. L’outil installé `/usr/local/sbin/bind-configtool` permet de créer,
lister et restaurer ces archives privées ; la rétention est de 30 jours,
évaluée à chaque sauvegarde.

Préparez le retour arrière avant l’intervention : archive et empreinte contrôlées,
secret TSIG, inventaire, version et composants runtime nécessaires.
La [procédure de restauration](docs/wiki.d/exploitation/REFERENCE_ROLE.md#reprise)
décrit le périmètre exact, l’arrêt des services et les contrôles de remise en service.

<a id="diagnostic"></a>
## Diagnostic

Relevez l’hôte, l’heure, la version, la tâche en échec et la dernière sauvegarde.
Conservez les messages utiles sans exposer de secret. Le
[guide de diagnostic](docs/wiki.d/exploitation/REFERENCE_ROLE.md#diagnostic)
relie les symptômes aux contrôles DNS, réseau, journaux et SELinux.

<a id="validation"></a>
## Documentation de qualification

Les [preuves de test, mesures de couverture et limites de production](docs/wiki.d/qualification/QUALIFICATION_ACTUELLE.md)
sont regroupées dans la documentation de qualification, avec leur provenance
et les procédures restant à réaliser dans l’environnement de destination.

<a id="livraison"></a>
## Organisation et livraison

```text
.
├── inventory/         # Environnements, sites et variables des hôtes
├── playbooks/         # Déploiement et gestion des ressources
├── roles/bind9/       # Rôle, modules, scripts et templates
├── examples/          # Interfaces, ressources et requêtes de gestion
├── docs/              # Configuration, exploitation et qualification
├── tests/             # Suites de tests et intégration
├── tools/quality/     # Instrumentation et contrôles qualité
├── ansible.cfg
├── DELIVERY.json
├── SOURCE-MANIFEST.sha256
└── README.md
```

Vérifiez l’archive puis les fichiers extraits avec les empreintes livrées :

```bash
sha256sum -c bind9-ansible-v4.zip.sha256
# Après extraction, depuis la racine du projet :
sha256sum -c SOURCE-MANIFEST.sha256
```

Le [portail documentaire](docs/index.md) donne accès aux procédures et à
l’historique. `DELIVERY.json` identifie la livraison ; le manifeste décrit
l’intégrité des sources. Les secrets générés et les artefacts de laboratoire
restent hors de l’archive source.

**Licence :** les métadonnées du rôle indiquent `proprietary`. Faites préciser
les droits d’usage et de redistribution par le propriétaire avant redistribution.
