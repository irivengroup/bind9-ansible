# Qualification : preuves de test et limites de production

[Documentation](../../index.md) › [Qualification et contrôles](index.md) › Qualification actuelle

[Retour au Parent](index.md)

## Sommaire

- [Résultats et provenance](#résultats-et-provenance)
- [Compatibilité et environnements exercés](#compatibilité-et-environnements-exercés)
- [Validation, tests du projet et limites](#validation-tests-du-projet-et-limites)
- [Transport Git des fixtures de CI](#transport-git-des-fixtures-de-ci)
- [Contrôles avant production](#contrôles-avant-production)
- [Historique et traçabilité](#historique-et-traçabilité)

<a id="resultats"></a>
## Résultats et provenance

La campagne locale conservée a exécuté 4 867 tests uniques, avec 1 203 reçus
natifs/instrumentés appariés, sans test ignoré dans cette sélection canonique.
Les 63 composants de production et les profils obligatoires ont satisfait les cinq
seuils de **99,99 % avant arrondi**. Les métriques restent indépendantes.

| Métrique | Couvert / total | Valeur exacte |
|---|---|---|
| Instructions | 6 433 / 6 433 | 100 % |
| Branches | 4 345 / 4 345 | 100 % |
| Fonctions | 249 / 249 | 100 % |
| Lignes | 5 215 / 5 215 | 100 % |
| Conditions booléennes | 5 248 / 5 248 | 100 % |

Ces résultats proviennent des preuves publiées à la racine du projet :
`SYNTHESE_COUVERTURE_INTERFACES_20261125.json`,
`COUVERTURE_INTERFACES_APPARMOR_20261125.json` et
`COUVERTURE_COMPOSANTS_INTERFACES_20261125.csv`. Le CSV conserve 370 lignes,
dont 85 métriques sans dénominateur justifiées comme non applicables ; elles ne
sont pas assimilées à une mesure de 100 %.

L’archive effectivement mesurée porte le SHA-256
`55b760768de685597ab7e7b9d0668ff80c91b6024e1b2d33e7bfec5576fce24f`.
La réadmission documentaire précédente porte le SHA-256
`efa00b0436c53567d5c10f6c6ce7a01fd41815ab8d3b0704b6c4db90b694c1d0`.
Le rapport externe `RAPPORT_HARMONISATION_DOCUMENTATION_20261125.md` rattache la nouvelle
archive à ces preuves par comparaison des octets des 63 composants et de tous
les fichiers QA. Il ne présente pas ces résultats comme une nouvelle exécution
de la campagne. L’extraction Windows contrôle l’identité des sources ; elle
n’est pas une exécution native du rôle Linux.

Les recettes Docker Rocky 8, 9 et 10 conservées ont exercé le pare-feu, les routes,
l’idempotence, le rollback et le redémarrage à froid des conteneurs : 161 commandes
Docker par version, soit 483 au total. Leurs adaptations et doubles SELinux sont
identifiés dans `RECETTE_ROCKY_INTERFACES_HEALTHCHECK_20261125.json`. Ces essais
ne démontrent ni une recette sur RHEL officiel sous SELinux enforcing, ni un
redémarrage de l’hôte. Les contrôles AppArmor ont été exécutés sur le laboratoire
qui les héberge ; AppArmor n’est pas un prérequis imposé aux clients Ansible.

Les rapports et reçus volumineux restent extérieurs au ZIP source. Leur absence
d’une extraction isolée ne transforme pas cette extraction en preuve autonome.
`DELIVERY.json` identifie le rapport courant ; les anciens rapports gardent leur
révision, leur empreinte et leur portée d’origine.

<a id="compatibilite"></a>
## Compatibilité et environnements exercés

| Composant | Contrat et preuve disponible |
|---|---|
| OS cible | **Red Hat Enterprise Linux de version majeure ≥ 7**, sans plafond de version dans les deux points d'entrée. Les prérequis natifs restent obligatoires. Les essais de compatibilité EL7 utilisent CentOS 7.9, Bash 4.2.46, BIND 9.11.4 et NetworkManager 1.18.8. Les distributions dérivées restent des laboratoires et conservent leur identité réelle. La qualification des serveurs de destination dépend des preuves de la livraison courante. |
| Ansible minimal déclaré | **2.9**, dans les métadonnées du rôle. Version des contrôles locaux enregistrés : **2.9.27**. Les autres correctifs 2.9 n’ont pas tous été exécutés. |
| Python requis sur les cibles | **3.6 ou supérieur** pour le module privé de management et les outils DNSSEC. Sur EL7, les facts et YUM peuvent utiliser Python 2.7, puis la transaction privée sélectionne `/usr/bin/python3`. Un interpréteur Python 3 explicitement choisi reste utilisé. Les bindings requis par le gestionnaire natif doivent être disponibles. |
| Protection du contrôle de santé | Sur EL7 et systemd 219, `ProtectSystem=full` est complété par `ReadOnlyDirectories=/` : la racine du service est en lecture seule et `PrivateTmp` conserve son espace temporaire privé. À partir d'EL8, `ProtectSystem=strict` est conservé. `NoNewPrivileges` et `ProtectHome` restent actifs. Le contrôle natif EL7 sous SELinux enforcing a observé les refus d'écriture avant et après un redémarrage de la VM ; cela ne remplace pas la recette DNS complète du site. |
| Contrôleurs de cette passe | QA exécutée sur les mêmes composants de production : Linux, Python **3.11.15**, Ansible Core **2.16.19**, Jinja2 **3.1.6**, pytest **9.0.3**. Les mesures historiques Core **2.16.14** restent identifiées avec leur environnement d’origine. Les preuves historiques de compatibilité utilisent Python **3.6.8**, Ansible **2.9.27**, Jinja2 **2.10.3** ; la recette de la révision courante est identifiée séparément dans le rapport externe. L’observateur QA exige Python 3.11 ; il n’est pas déployé sur les cibles. |
| BIND de laboratoire de la livraison source | **9.11.36-16.el8_10.16**, paquets EL8 installés dans un conteneur Rocky Linux 8.9 avec paquets EL8.10. Ce n’est pas une recette système RHEL. Les essais BIND 9.18.24 restent historiques. |
| NetworkManager de laboratoire de la livraison source | **1.40.16-21.el8_10**, moteur natif et noyau Linux dans un espace réseau jetable, avec route par défaut BACK et passerelle propre à FRONT. La version de vos serveurs reste à recetter. |
| Familles d’adresses | Écoute DNS et clients déclarés : **IPv4**. Forwarders, transport sortant et données DNS : **IPv4/IPv6**. Routes gérées : **IPv4 uniquement**. |

Les versions imposées par le contexte nécessitent une politique de maintenance de votre organisation. Les sept profils locaux Python 3.8–3.12 / Ansible 2.9–2.16 ont réussi sur les mêmes 63 composants de production, dont Core 2.13.13, 2.14.18 et 2.16.19. Les archives et les fixtures de ces profils restent identifiées séparément ; leur identité complète avec cette archive n’est pas présumée. La combinaison déclarée Core 2.16.14 et la CI distante conservent leurs preuves historiques ; aucune nouvelle exécution distante n’est revendiquée. Les reçus, versions exactes et limites figurent dans le rapport externe de qualification.


<a id="validation"></a>
## Validation, tests du projet et limites

La livraison précédente ajoutait un essai combiné du playbook public avec DNSSEC auto et routes FRONT actives ; voir [OPERATIONS.md](../exploitation/OPERATIONS.md#déploiement-combiné-dnssec-auto-et-routes-front). La livraison source regroupait la préparation DNSSEC et sa documentation. Les tests de cycle de vie conservent les modes, l’idempotence, les migrations et le rollback ; les contenus et permissions runtime sont comparés à la livraison source. Résultats observés et périmètre dans `DELIVERY.json` et [DNSSEC.md](../configuration/bind/DNSSEC.md).

### Preuves disponibles

Le [rapport de syntaxe historique](VERIFICATION_SYNTAXE_SCRIPTS.md) conserve les campagnes antérieures des scripts, routes, DNS et management. Elles ne sont pas présentées comme réexécutées pour la consolidation. Le code de ces fonctions et les seuils CI sont conservés.

### Rejouer les contrôles

Sur Linux de recette, avec les dépendances de [.github/workflows/ci.yml](../../../.github/workflows/ci.yml) et un environnement Python isolé adapté :

Pour l’analyse ShellCheck, Docker est un outil de contrôle uniquement, sans dépendance ajoutée aux serveurs :

```bash
docker run --rm --network none --read-only \
  -v "$PWD:/mnt:ro" --workdir /mnt \
  koalaman/shellcheck@sha256:61862eba1fcf09a484ebcc6feea46f1782532571a34ed51fedf90dd25f925a8d \
  --norc --shell=bash roles/bind9/files/*.sh tests/*.sh
```

Puis les contrôles Python/Ansible :

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

La sélection avec `--ignore` ci-dessus est un dépistage partiel : elle ne fournit aucune preuve des cinq couvertures et ne remplace pas la campagne exhaustive suivante.

Les workflows privilégiés exigent un **laboratoire jetable**, root et leurs prérequis. Certains manipulent des chemins système ou substituent des commandes ; ne pas exécuter ces suites sous root sur un DNS de production. La CI décrit les suites routes/DNSSEC et le seuil de couverture.

La collecte des cinq métriques utilise le contrôleur QA Python 3.11 et ses versions inspectées (`ansible-core 2.16.19`, `Jinja 3.1.6`, `pytest 9.0.3`, `coverage.py 7.6.1`). Dans ce laboratoire, `python tools/quality/run_coverage_suite.py --output /tmp/bind9-five-metrics --batch-size 32 tests` répartit toute la sélection collectée en lots natifs/instrumentés, puis applique le même vérificateur à leur réunion. Les filtrages, tests ignorés, échecs et sources modifiées restent bloquants ; les limites des observateurs et les cinq seuils de 99,99 % sont contrôlés avant arrondi. Les mesures par lot et leurs refus ne constituent pas une qualification de production. Les tests de fichiers système exigent en plus un conteneur propre et déconnecté explicitement activé par `BIND9_NATIVE_STATE_LAB=1` ; les contrôles BIND, NetworkManager et espaces de noms conservent leurs prérequis distincts.

Les tests de sauvegarde et restauration de `test_configtool_workflows.py` et `test_native_archive_cli.py` lancent directement les octets du script public avec une seule fixture, `configtool_lab.py`. TAR, sommes de contrôle, verrous, extraction et rollback restent natifs ; seuls systemd, le validateur BIND et le réétiquetage SELinux utilisent des doubles explicites. Le laboratoire refuse les chemins système déjà occupés ou montés. Ces scénarios sont exécutés dans le job complet `five-metric-coverage` ; l'ancienne invocation sous `sudo` sur l'hôte ne satisfait plus ce contrat d'isolation. Les reçus de compatibilité EL8 sont distincts des cinq couvertures du contrôleur QA. L'option `--archive` est réservée à `restore` : `backup --archive …` et `list --archive …`, auparavant ignorées, sont refusées avant création de l'état de sauvegarde avec le code de sortie 2.

Dans ce même conteneur propre, root et sans réseau, `BIND_MANAGEMENT_ISOLATED=1 python tests/integration_backup_container.py` reste le lanceur dédié aux deux suites d'archives. Il délègue leur collecte complète à pytest, conserve ses codes de sortie et restaure le réglage d'isolation de l'appelant. Aucun contenu BIND préexistant n'est effacé pour préparer ces tests ; utiliser un conteneur neuf avec les chemins de fixture initialement absents.

Le job CI `five-metric-coverage` construit [le contrôleur Docker dédié](../../../tools/quality/Dockerfile.qa) depuis une image Python identifiée par son empreinte, [23 dépendances Python verrouillées avec leurs sommes de contrôle](../../../tools/quality/requirements-qa.lock) et l'archive GNU Bash 5.2.37 vérifiée. Le contexte de construction exclut inventaires, clés et preuves. Les deux interpréteurs sont compilés dans une étape séparée ; outils de compilation, sources GNU et clés SSH ne sont pas intégrés au contrôleur final. Les versions des paquets système effectivement résolus y sont conservées ; cela ne prouve pas une reconstruction future identique octet par octet des dépôts système.

Le job exécute toute la suite dans un conteneur neuf, sans réseau externe, avec sources en lecture seule et preuves dans un répertoire dédié. Une copie sur le disque Linux privé évite les relectures répétées du partage hôte : les empreintes de toutes les sources du rôle, playbooks, tests, observateurs et configuration Ansible sont comparées à l'entrée montée en lecture seule. La copie est ensuite montée et vérifiée en lecture seule avant l'exécution. `BIND9_NATIVE_STATE_LAB=1`, `BIND9_CANONICAL_BASH_LAB=1`, `BIND9_NATIVE_SSH_LAB=1` et `BIND_TEST_REQUIRE_MOUNT_NS=1` activent explicitement les laboratoires ; la capacité `SYS_ADMIN` et le profil système permettent leurs espaces de noms privés. Les clés SSH éphémères restent dans le conteneur, sans port publié. Les contrôles de compatibilité Ansible anciens restent séparés. Les reçus et refus sont retenus même si le seuil échoue ; aucun de ces réglages n'autorise l'exécution sur un serveur DNS réel.

Les identifiants de tests longs passent par des fichiers de sélection contrôlés. Les profils Jinja brut et Ansible qui diffèrent gardent chacun leur inventaire et leur seuil. Les observations des modèles exécutés dans les processus Ansible sont conservées dans leurs reçus, sous les mêmes limites de taille et de nombre de processus, puis revérifiées avant leur réunion avec celles du processus parent.

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

<a id="import-git-et-mode-exécutable-de-la-fixture-front"></a>
## Transport Git des fixtures de CI

Le ZIP conserve déjà `tests/fixtures/native_network_cli.py` en mode Unix `0755`. Un import depuis Windows peut néanmoins enregistrer cette entrée en `100644` dans Git. La CI vérifie à la fois `git ls-tree HEAD` et le droit d’exécution réel : changer seulement le fichier ou l’index ne corrige pas le commit qu’elle exécute.

L’annotation du run `45606014034` porte sur le commit `eef389687d28d64e21bb32bc143a2f3a56eab8b5`. Son blob de fixture est `ea2b33c964b06885f9495bc857c3f577948f0b5b`, identique au contenu local. Le correctif externe `CORRECTIF_CI_45606014034.patch` enregistre ce mode, protège le registre IANA contre la normalisation Git et restaure ses octets épinglés. La vérification locale conserve le refus avant, le refus lorsque seul l’index est corrigé et la réussite du contrôle original après un commit synthétique isolé ; elle ne constitue pas une nouvelle CI hébergée.

Le [run hébergé 45606014034](https://github.com/irivengroup/bind9-ansible/actions/runs/45606014034) est **ÉCHOUÉ**. Les journaux des sept jobs de validation montrent chacun le même test IANA refusé : la normalisation CRLF/LF transforme l’empreinte originale `52a1b8d3a42dae6a223772b687dc775a4c289fb9fb3004dfb54da13dfd36a568` en `9936c708c80311b26266aad152e762cc2b444610fd9e58bf04fa3c5130ed1831`. Les 111 fins de ligne CRLF et les autres LF du snapshot original doivent rester exactes. La règle `.gitattributes` `tests/fixtures/iana_dns_types_20261123.csv -text` préserve ces octets ; l’empreinte attendue et le test ne changent pas.

Le patch restaure le registre original sans changer ses données ni son oracle. Le contrôle de round-trip Git vérifie les deux causes avant/après et conserve les refus. Une nouvelle CI hébergée reste **NON EXÉCUTÉE** pour le correctif local ; aucun push n’est effectué.

Dans le dépôt Git réel, examiner les modifications en cours, vérifier que les deux fixtures ne portent aucune modification à conserver, puis appliquer ce patch avec `git apply --index --binary`, puis vérifier `git diff --cached --summary` et `git ls-files --stage -- tests/fixtures/native_network_cli.py`. L’entrée FRONT attendue est `100755`, sans modification de son blob. Le registre doit retrouver l’empreinte originale ci-dessus ; ne jamais modifier l’empreinte attendue pour accepter sa version normalisée. Enregistrer ensuite ce changement dans un commit après revue de tout le contenu indexé et vérifier `git ls-tree HEAD -- tests/fixtures/native_network_cli.py`. Le dossier de publication locale ne contient pas de `.git` : aucun commit ni push n’y est effectué. Le seuil et le contrôle AppArmor ne sont pas modifiés.

## Contrôles avant production

Raison commune des contrôles non exécutés : aucune paire native RHEL représentative accessible dans cet environnement ; les hôtes fournis sont fictifs. Prérequis : paire de recette représentative, inventaire réel, clients autorisés/non autorisés et accès ADM de secours.

| Statut | Procédure | Attendu et risque restant |
|---|---|---|
| **NON EXÉCUTÉ** | Exécuter `deploy -e hibernate_deployment=yes`, recette DNS, refus du deuxième `deploy` avec `true` et `false`, puis retrait opérateur du sceau et reconstruction, CRUD ciblé avec les vrais Python et YUM (EL7) ou DNF (EL8+) et services RHEL. | Données/réponses stables ; sceau persistant bloquant ; sauvegarde lors d’une reconstruction autorisée ou d’une écriture CRUD effective. Écarts possibles de paquets, versions et état système. |
| **NON EXÉCUTÉ** | Tester SELinux enforcing, systemd, firewalld, trafic des deux segments puis reboot de recette, un hôte à la fois. | Écritures `named_t` autorisées, labels/services/timers corrects, DNS/routes persistants, ADM/BACK préservés. Restent politiques locales et comportements de boot. |
| **NON EXÉCUTÉ** | Tester chaînes DNSSEC réelles, suffixes privés, renouvellement NTA sur plus de sept jours, ancres RFC5011 et FIPS si utilisé. | Validation selon politique, exceptions limitées et renouvelées. Restent expirations, chaînes upstream et crypto-policy réelle. |
| **NON EXÉCUTÉ** | Provoquer en recette un échec protégé, une perte de contrôleur et une restauration manuelle avec backups persistants. | Cohérence données/runtime, retrait des routes de l’opération, pair sain disponible, RPO/RTO mesurés. Risque de reprise partielle et perte d’état mémoire. |
| **NON EXÉCUTÉ** | Mesurer charge représentative, latence, débit, disque, rotation et export des logs ; tester pression disque. | Objectifs/alertes d’exploitation respectés. Performance et durée de conservation ne sont actuellement pas démontrées. |
| **NON EXÉCUTÉ** | Exécuter la matrice CI dans votre dépôt et conserver les résultats. | Contrôles de chaque combinaison réussis ; présence du workflow seule insuffisante. |

Détails : [VALIDATION_RHEL8.md](VALIDATION_RHEL8.md), [VALIDATION_ROUTES_NATIVES.md](VALIDATION_ROUTES_NATIVES.md), [JOURNALISATION.md](../configuration/bind/JOURNALISATION.md), [FIREWALLD.md](../configuration/reseau/FIREWALLD.md). **La qualification opérationnelle reste NON TERMINÉE tant que les contrôles applicables à votre environnement ne sont pas exécutés et leurs écarts résolus.**


<a id="historique"></a>
## Historique et traçabilité

Référence historique : `remediation-2026.11.04-coverage-hardening.1.zip`, SHA-256 `b8ef36e938957c03f73540219d29137bf643fddf21d04fe018059bb302cb6bff`. Révision de cette preuve historique : `2026.11.04-coverage-hardening.2`. La présente livraison est identifiée par `DELIVERY.json` et le rapport externe `RAPPORT_HARMONISATION_DOCUMENTATION_20261125.md`. Les résultats de cette livraison historique sont dans [REVUE_DURCISSEMENT_COUVERTURE.md](../../../REVUE_DURCISSEMENT_COUVERTURE.md), [COUVERTURE.json](../../../COUVERTURE.json) et `DELIVERY.json`. L’historique d’hibernation reste dans [AUDIT_HIBERNATE_DEPLOYMENT.md](../historique/AUDIT_HIBERNATE_DEPLOYMENT.md) ; le [durcissement FRONT](../historique/DURCISSEMENT_FRONT_ACTUEL.md) conserve sa portée historique. Les preuves de la présente révision doivent correspondre à son archive exacte ; les anciennes mesures ne la qualifient pas.


---

Documentation DNSZIBO · contenu réorganisé le 25 novembre 2026.
