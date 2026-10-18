# Compatibilité et vérification — 15 octobre 2026

Projet **Bind9-Ansible**, playbook `playbooks/bind9-deploy.yml`, cible RHEL 8, Ansible 2.9 et Python serveur 3.6.8. Source de cette livraison : `Bind9-Ansible-correction-controles-dns.zip`, SHA-256 `62fcc29eb0c4bd3a8688ab3c0909f888d8ee3556be5fbc80fd8d2e104a60f918`. Le fichier original sur D: n’a pas été relu. Différences et preuves exactes : `DELIVERY.json` ; contenu : `SOURCE-MANIFEST.sha256`.

Le routage a été remplacé par des tâches Ansible natives sans dépendance ajoutée. Le filtre Python correspondant a été supprimé ; les scripts nécessaires au DNS restent présents. Structure BIND sans vues, ACL techniques, zones natives et tags sont conservés ; DNSSEC est configurable, défaut actuel `auto` ; les inventaires déclarent deux listes plates IPv4 ; `bind_allowed_clients` est leur fusion calculée, jamais une troisième entrée. Les corrections de cycle de vie de la livraison précédente restent couvertes. Voir [VALIDATION_ROUTES_NATIVES.md](VALIDATION_ROUTES_NATIVES.md) et [Historique des validations](NETTOYAGE_ET_VALIDATION.md#historique).

## Contrôles du durcissement

`bind_dnssec_active` est l’unique entrée DNSSEC, défaut actuel `auto`. Les faits
intermédiaires et leur assertion sont supprimés ; les décisions utilisent
l’entrée validée. Les contrôles `boolean` et `integer` nécessitant Jinja 2.11
sont remplacés par `type_debug`, y compris dans le routage. Les valeurs
invalides restent refusées. Impact : [DURCISSEMENT_DNSSEC.md](DURCISSEMENT_DNSSEC.md).

**EXÉCUTÉ** : suites sous Ansible 2.9.27 avec Jinja 2.10.3 et 3.0.3 ; rendu
des trois modes, chaînes CLI et booléens, collisions extra-vars, contrôles de
types et de refus, fichiers/runtime et routes. La non-régression reproduit
l’ancien message de faits dérivés sur l’archive source ; les anciens tests
de types échouent sur Jinja 2.10.3 et leurs remplacements y réussissent.
La cause exacte du serveur utilisateur n’est pas confirmée par cette simulation.
Le transport est natif IPv4/IPv6 ; seule l’écoute DNS IPv6 est fermée. Les
forwarders IPv6 et leur glue AAAA sont pris en charge. Les comptes, périmètres
et sommes de contrôle figurent dans `DELIVERY.json`.
Les dépréciations du vieux runtime sont affichées, pas masquées.

## Preuves locales

| Contrôle | Statut | Résultat et périmètre |
|---|---|---|
| Expressions de routage | **EXÉCUTÉ** | 77 cas sur le moteur réel Ansible 2.9.27 : interfaces/IP secondaires, IPv4, SSH, passerelle, comparaison exacte, idempotence, agrégation CIDR, refus et journaux corrompus |
| Workflows Ansible | **EXÉCUTÉ** | Parcours réels avec doubles CLI : simulation, ajouts, stdout vide, deuxième passage, échecs d’application/vérification, rollback DNS et reprise. La restauration DNS/systemd réelle n’est pas exécutée par ces doubles |
| NetworkManager 1.46.0 | **EXÉCUTÉ** | Quatre workflows réels isolés, `nmcli` persistant et `ip route` actif ; idempotence, reprise sélective, persistance après réactivation du profil de laboratoire, routes initiales et DNS pending conservés |
| Dépendances | **EXÉCUTÉ** | netaddr absent du contrôleur de recette, aucun filtre réseau externe ni Python personnalisé de routage dans le rôle |
| Couverture Python restante | **EXÉCUTÉ** | Quatre modules, 286 instructions, deux non couvertes, 99 % arrondi ; seuil 98 % conservé. La logique YAML est testée par scénarios, elle ne contribue pas à cette mesure Python |
| Préflight | **EXÉCUTÉ** | Trois inventaires, 12 hôtes, vraies assertions et faits cibles simulés ; syntaxe Ansible 2.9.27 |
| BIND EL8 / 9.18.24 | **EXÉCUTÉ** | Chacun 109 configurations chargées et 282 contrôles DNS réels, fichiers natifs inchangés, tests cryptographiques DNSSEC avec ancre temporaire |
| Python 3.6.8 extrait EL8 | **EXÉCUTÉ** | Compilation de tous les Python de production restants et réconciliation DNSSEC isolée ; aucun module Ansible/DNF exécuté sur un serveur RHEL |
| Qualité | **EXÉCUTÉ** | Pyflakes, Bandit, compilation, syntaxe shell ; 62 YAML, 34 fichiers de tâches accessibles, 12 assets, un handler, onze variables par défaut et 11 filtres restants référencés |
| IPv6 sortant / écoute | **EXÉCUTÉ** | Client IPv4 vers upstream IPv6, UDP et repli TCP prouvés par le journal upstream ; absence de listener DNS IPv6 en UDP/TCP, processus sans famille forcée |
| Exemples DNS | **EXÉCUTÉ** | Campagne antérieure : deux zones forwardées, validation désactivée, rendus des deux rôles contrôlés par named-checkconf -z EL8. Le défaut actuel `auto` et ses preuves sont décrits dans [DEFAUT_DNSSEC_AUTO.md](DEFAUT_DNSSEC_AUTO.md). |

La suite complète et ses nombres exacts figurent dans `DELIVERY.json`. Les binaires EL8 sont des RPM AlmaLinux extraits sans installation/scriptlets et exécutés sous WSL Ubuntu avec leurs bibliothèques. La crypto-policy DEFAULT de laboratoire n’est pas la preuve d’une politique effective RHEL/FIPS. Les unités systemd, SELinux et le réseau de production ne sont pas qualifiés par ces essais.

## DNSSEC optionnel : périmètre des preuves

**EXÉCUTÉ** : les trois modes sont chargés par BIND EL8 et BIND 9.18 ; les
signatures invalides sont rejetées en `true`/`auto` et acceptées sans AD en
`false`. Les fichiers natifs restent inchangés. Les transitions, retraits,
permissions et restaurations runtime sont exercés par le vrai runner Ansible
avec les opérations de fichiers réelles et un double de contrat systemd.
Ce double ne prouve ni le démarrage ni la fusion des unités sur un vrai RHEL.

**NON EXÉCUTÉ** : unités natives, boot et bascules sur une paire RHEL réelle.
Prérequis : cible de recette avec sauvegardes BIND et runtime, RNDC local,
ancres officielles actualisées pour les modes activés. Procédure : appliquer
successivement `false`, `auto`, `true`, puis `false` ; inspecter `systemctl cat
named`, vérifier les fichiers gérés et l’état du timer, les réponses DNS et
le mode explicite `false` après restart, puis le défaut `auto` sans surcharge. Injecter ensuite un échec contrôlé après
configuration : les fichiers, permissions, activation et activité du timer
doivent revenir à leur état initial. Risques : comportement systemd cible,
politique SELinux, confiance/rotation réelle des ancres et coupure du contrôleur.
Voir [DNSSEC.md](DNSSEC.md) et [OPERATIONS.md](OPERATIONS.md).

## Reproduction

Sur un contrôleur Linux de recette : Python 3.8.20, Ansible 2.9.27, Jinja2 2.10.3 ou 3.0.3 avec MarkupSafe 2.0.1, PyYAML 6.0.2, pytest 8.3.5 ; coverage 7.6.1, Bandit 1.7.10 et Pyflakes 3.2.0 pour la QA. Aucun paquet ou collection réseau supplémentaire n’est requis.

```bash
sudo python -m pytest -q tests
python tests/integration_preflight.py
python -m pyflakes roles/bind9/filter_plugins roles/bind9/files tests
sudo python -m coverage run --include='*/roles/bind9/filter_plugins/dns_topology.py,*/roles/bind9/filter_plugins/soa_health.py,*/roles/bind9/filter_plugins/client_acl.py,*/roles/bind9/files/bind-private-dnssec.py' \
  -m pytest -q tests/test_optional_dnssec.py tests/test_client_list_required.py tests/test_native_front_routes.py tests/test_dns_topology.py tests/test_soa_health.py tests/test_security_remediation.py tests/test_private_dnssec.py tests/test_global_review.py
python -m coverage report --fail-under=98
sudo bash tests/run_integration_front_routes.sh
```

Le laboratoire réseau nécessite root, unshare, ip, NetworkManager/nmcli, dbus-daemon et capacités réseau/montage. Il isole ces opérations de toute interface opérationnelle. Pour DNS, préparer sous `/opt/recette/bind` les binaires dig/checkconf/checkzone/rndc/keygen/signzone dans `bin`, named dans `sbin`, et les fichiers natifs dans `native`. Aucun secret réel ne doit entrer dans ces fixtures. Une fois ces répertoires préparés :

```bash
sudo bash tests/run_integration_loopback.sh --bin-dir /opt/recette/bind/bin \
  --native-dir /opt/recette/bind/native --named /opt/recette/bind/sbin/named
```

## Contrôles manquants et risques résiduels

Toutes les lignes ci-dessous sont **NON EXÉCUTÉ**, pour la raison indiquée. La qualification reste **NON TERMINÉE** tant que les contrôles applicables ne sont pas réalisés.

| Contrôle / raison | Prérequis et procédure | Attendu / risque restant |
|---|---|---|
| RHEL/Python/DNF réels : aucune cible | Paire RHEL 8, SSH/sudo et dépôts ; relever rpm/Python/Ansible, ping puis deploy primaire avant secondaire | Modules et paquets compatibles, service sain ; divergences des bindings et du système installé possibles |
| Unités/RNDC natifs et SELinux : pas de système cible | `systemctl cat named`, status named-setup-rndc/private-dnssec/timer, journalctl/audit ; reload puis reboot de recette ; vérifier listeners DNS IPv4 uniquement, absence d’option de famille forcée et absence d'AVC | Hooks et timer exécutés avant santé, clé accessible uniquement aux acteurs prévus ; confinement/override personnalisé peut bloquer le service |
| NTA sur sept jours : pas de test longue durée | Suivre nta -dump et journaux au-delà d'une semaine sur recette ; provoquer une panne de renouvellement maîtrisée et vérifier son alerte/reprise | Exceptions renouvelées, expiration détectée, aucune désactivation globale ; indisponibilité privée si renouvellement arrêté |
| Chaînes/ancres réelles, RFC5011 et crypto-policy/FIPS : réseau cloisonné inaccessible | Politique installée, horloge correcte, paquets/ancres à jour ; update-crypto-policies --show, named-checkconf, dig des zones cibles et journaux de clés | Confiance exploitable pour zones strictes, données privées exemptées identifiées ; la racine privée peut empêcher une chaîne publique/le rafraîchissement des clés |
| Pare-feu et clients : réseau cible indisponible | Clients de la liste unique réellement déclarés et sources interdites ; suivre TESTS_FONCTIONNELS, UDP/TCP, SOA des deux pairs et refus AXFR | Réponses exactes, refus explicites ; vieux dig peut rester inconclusif, filtrage/route peut rompre le service |
| Migration/IXFR/upstreams réels : fixtures seules | Sauvegarde, ancien serveur de recette, deploy/reload contrôlé, changement de record + serial et inspection transferts | Données convergentes et migration sans panne ; un restart peut être requis, IXFR effectif non prouvé |
| Réseau/NM/SSH/boot cibles : version réelle inconnue | Profil FRONT déjà configuré, relevés ; simulation puis activation explicite, ip route get source FRONT, deuxième passage et reboot | Seuls ajouts manquants, défauts/ADM/BACK/SSH conservés ; différences de NM/iproute2 ou routage complexe possibles |
| Idempotence complète et restauration : pas de paire cible | Deux deploy identiques ; backup forcé attendu ; restore manuel et échec DNS après ajout de routes en recette ; reprise si journal interrompu | Pas de mutation réseau/donnée inutile, seuls ajouts retirés ; runtime restauré par le rescue depuis sa capture en mémoire, mais non couvert par le backup BIND seul ; perte du contrôleur et ancienne config RNDC restent à qualifier |
| Capacité/sauvegarde/supervision : pas de profil de charge/SLO | Mesurer latence/erreurs/CPU/disque/transferts, fixer seuils et alertes, tester restauration et RPO/RTO | Objectifs mesurés ; aucun débit ni protection saturation démontré, backup chaud secondaire non atomique avec journaux |
| CI distante/autres matrices : pas de déclenchement distant | Exécuter tous les jobs du workflow, y compris coverage root et seuil 98 % | Tous les jobs applicables réussissent ; présence du YAML ne prouve pas les autres versions |

Déployer un environnement à la fois, primaire avant secondaire, valider avant reload, appliquer les exceptions puis santé. Une erreur d'exception n'est pas ignorée. Arrêter en cas de refus AXFR inconclusif, SOA divergent, changement réseau inattendu ou erreur de service. Rollback local à l'hôte, pas une transaction distribuée de la paire. Voir [DNSSEC.md](DNSSEC.md), [OPERATIONS.md](OPERATIONS.md), [FRONT_ROUTES.md](FRONT_ROUTES.md) et [TESTS_FONCTIONNELS.md](TESTS_FONCTIONNELS.md).

## Correction des contrôles de santé et de configuration

Les preuves, les régressions et la procédure de vérification du serveur sont détaillées dans [CORRECTION_CONTROLES_DNS.md](CORRECTION_CONTROLES_DNS.md). Les essais BIND exécutent maintenant aussi les vraies tâches Ansible ; les chemins et ports sont adaptés uniquement au laboratoire. La couverture Python et le laboratoire NetworkManager cités ci-dessus proviennent de la livraison précédente, dont ces sources sont inchangées ; les workflows Ansible de routage et DNSSEC ont été rejoués pour cette correction.

## Journalisation et ACL cliente

L’autorisation du service `dns` dans firewalld est intégrée au déploiement
et au tag `firewall`. Sa recette sur le pare-feu réel reste **NON EXÉCUTÉE** ;
les raisons, prérequis, commandes, résultats attendus et risques figurent
dans [FIREWALLD.md](FIREWALLD.md).

La journalisation fichier, ses rotations, les tâches SELinux et le renommage de l’ACL BIND sont décrits dans [JOURNALISATION.md](JOURNALISATION.md). Les preuves courantes sont dans DELIVERY.json. Les scénarios Ansible de journalisation utilisent des doubles CLI SELinux ; l’accès réel sous named_t/enforcing reste NON EXÉCUTÉ, avec procédure et risques dans ce rapport. Les workflows de routes et de cycle de vie DNSSEC cités dans les rapports précédents ne sont pas rejoués pour ce changement ciblé ; leurs sources de production sont inchangées.
