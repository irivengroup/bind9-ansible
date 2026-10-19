# Revue des scripts, syntaxe et compatibilité EL8

**Rapport historique de la livraison source.** La révision courante consolide les sources DNSSEC ; voir [DNSSEC.md](DNSSEC.md). Le retrait du nettoyage des fragments reste conservé.

Révision **2026.10.19-syntax.1** du 19 octobre 2026. Source conservée : `Bind9-Ansible-verifie.zip`, SHA-256 `a5560b4a24a6e835a73c5eae3dd306b47671f6e2dee8aa561c166982d34d6c81`. Aucun serveur de votre environnement n’a été modifié.

## Résultat et diagnostic de la coloration

Le fichier `bind-front-routes.sh` d’origine ne présente pas d’erreur de parsing avec le Bash 4.4.20 natif EL8. L’analyse des sept scripts révèle toutefois des constructions qui perturbent les analyseurs et la lecture : littéral `done` non cité, variables scalaires suivies de crochets de regex, affectation par capture indirecte et chaînes de gardes ambiguës. Elles sont rendues explicites. Les expressions régulières sont conservées ; aucun remplacement du moteur de routage n’est introduit.

L’analyse ShellCheck 0.11.0 par défaut avait 16 diagnostics : 14 sont résolus dans les sources et deux faux positifs SC2329 sont documentés au niveau des fonctions appelées par le trap EXIT. Ce sont les seules exceptions locales ; aucun fichier, groupe de codes ou niveau de gravité n’est exclu. La limite sur les callbacks est décrite par [ShellCheck SC2329](https://www.shellcheck.net/wiki/SC2329). Les accolades de `${type}` et `${expected}` lèvent l’ambiguïté du scalaire suivi d’un crochet, sans changer la regex produite : [ShellCheck SC1087](https://www.shellcheck.net/wiki/SC1087).

La vérification étendue `check-set-e-suppressed` est aussi relue : les fonctions prédicats sont volontairement utilisées comme conditions ; les fonctions de nettoyage et de relabellisation retournent explicitement leur statut. Le contrôle des métadonnées privées ne dépend plus d’un `read` dont le producteur échoue. Les avertissements informatifs de cette analyse optionnelle ne sont pas assimilés à des erreurs de parsing, ni neutralisés globalement.

## Défauts fonctionnels reproduits

| Défaut | Effet observé avant correction | Correction et non-régression |
|---|---|---|
| Échec de `sort` du catalogue initial caché par `< <(...)`. | `list` réussit avec un catalogue vide. | Capturer le résultat et contrôler le code avant `mapfile`. Le test exige un échec sans modifier la configuration. |
| Échec du catalogue de rétention caché de la même façon. | `backup` émet un reçu de réussite malgré une lecture partielle. | Refus sans purge ni `BACKUP_CREATED`. La nouvelle archive et son empreinte déjà créées restent conservées. |
| Échec partiel de la liste `tar` avant restauration. | Une zone live marquée absente de la liste partielle est supprimée ; la configuration opérateur peut être remplacée. | Exiger la lecture complète avant la création de la sauvegarde de sécurité et avant toute suppression. Le test vérifie le SHA-256 des octets de la zone et la configuration opérateur. |
| Métadonnées `stat` partielles avec code non nul. | Des valeurs plausibles pouvaient être acceptées pour une clé privée, les répertoires de sauvegarde ou la taille du fichier d’empreinte malgré l’échec de la commande. | Contrôler explicitement chaque producteur, puis les valeurs, y compris lorsque la fonction est appelée comme condition. Les tests exigent un refus avant lecture de clé, création d’état ou acceptation d’archive. |
| Diagnostic AXFR avec le `dig` natif EL8. | Sans affichage explicite des commentaires, seul « Transfer failed » peut être visible ; le test d’intégration exigeait `+yaml`, absent de dig 9.11. | Utiliser `+noall +comments +answer` sur dig sans YAML ; conserver `+yaml` quand disponible. Exiger toujours REFUSED/NOTAUTH explicite, refuser un SOA transféré et classer timeout/erreur TSIG/résultat ambigu comme incomplets. |
| Course de démarrage dans le test de réplication. | Le secondaire peut demander le SOA avant que le primaire soit disponible, puis entrer dans le délai natif de nouvelle tentative. Le cas est reproduit avec un primaire retardé de deux secondes : primaire opérationnel mais zone secondaire non chargée. | Attendre une vraie réponse A autoritative sur BACK avant de lancer le secondaire ; convergence automatique et NOTIFY restent exigés, sans forcer de transfert. Ajouter le scénario retardé à la recette livrée et conserver les diagnostics DNS/RNDC en cas d’échec. Aucun réglage BIND de production n’est changé pour ce défaut de test. |

Les trois scénarios de catalogues et celui des métadonnées de répertoires échouent sur les octets de la source précédente ; les cinq scénarios existants y réussissent. Les neuf workflows réussissent après correction. Les défauts de métadonnées privées, de taille d’empreinte et de diagnostic AXFR ont chacun un test qui échoue avant correction et réussit après. Toutes les observations `stat` de l’utilitaire de sauvegarde exigent désormais le succès du producteur, sans dépendre d’une substitution de processus ou du seul contenu affiché. Aucun test existant n’est supprimé, aucune assertion de transfert/refus n’est assouplie.

L’essai de restauration a d’abord révélé l’absence de `cmp` dans l’image minimale du laboratoire ; sa comparaison est effectuée par SHA-256. Un nouveau contrôle d’encodage initialement trop strict a été corrigé pour accepter les CRLF Python/YAML historiques sous Windows ; les sept scripts shell restent obligatoirement LF, sans BOM ni caractères de contrôle cachés. Ces incidents d’outillage ne sont pas présentés comme des défauts BIND.

## Matrice d’impact

| Partie | Modification et invariants |
|---|---|
| `roles/bind9/files/bind-front-routes.sh` | Littéraux de phase cités, gardes explicites, variable locale initialisée et indices arithmétiques clarifiés. Découverte limitée à l’interface portant FRONT, métriques, format de journal, limites, exclusion de processus, reprise conditionnelle et routes étrangères préservés. |
| `roles/bind9/files/named-healthcheck.sh` | Accolades des deux variables de regex. Questions, statut DNS, absence de troncature et exigence d’autorité inchangés. |
| `roles/bind9/files/bind-configtool.sh` | Trois catalogues contrôlés, statut de toutes les observations `stat` propagé (fichiers privés, ancêtres, racine et taille d’empreinte) et diagnostic AXFR compatible dig 9.11. Format des archives, SHA-256, rétention 30 jours, permissions et préconditions de restauration conservés. |
| Sources conservées | Tous les autres fichiers du rôle, Python de production, modèles, playbooks, inventaires, exemples et configuration Ansible identiques aux octets source. Aucune nouvelle variable, option, route par défaut, activation d’interface ni dépendance d’exécution. |
| Tests et CI | Tests de catalogues/métadonnées/dig et contrôles d’encodage ajoutés ; intégration AXFR adaptée aux capacités réelles du binaire. ShellCheck figé par digest dans le workflow existant ; analyse des sept scripts, seuil de couverture 98 % conservé. |
| Éditeurs et Git | `.editorconfig` et `.gitattributes` prescrivent UTF-8/LF pour éviter les conversions futures ; les fichiers historiques non concernés ne sont pas normalisés en masse. |
| Exploitation | README, routes, procédures de sauvegarde et tests fonctionnels alignés ; ancien rapport conservé comme historique. Manifeste et provenance reconstruits. |

## Contrôles exécutés

| Statut | Contrôle | Résultat observé |
|---|---|---|
| **EXÉCUTÉ** | `components` | 1047 tests réussis, aucun ignoré. |
| **EXÉCUTÉ** | `targeted` | 161 tests réussis, aucun ignoré. |
| **EXÉCUTÉ** | `ansible-transport-final` | 44 tests réussis, aucun ignoré. |
| **EXÉCUTÉ** | `contracts` | `{"syntax_checks":6,"python_compiled":74,"shell_scripts":7}` |
| **EXÉCUTÉ** | `sources` | `{"status":"EXÉCUTÉ","counts":{"yaml":71,"jinja_templates":7,"shell":7,"python":74},"yaml_duplicate_keys_rejected":true,"utf8_without_hidden_controls_shell_lf":true}` |
| **EXÉCUTÉ** | `static` | `{"pyflakes":"3.2.0","bandit":"1.7.10","findings":0}` |
| **EXÉCUTÉ** | `native-routes` | 11 workflows Ansible avec NetworkManager/noyau réels, passerelle FRONT sans route par défaut FRONT. |
| **EXÉCUTÉ** | `native-management-no` | 12 transactions, dont 2 lots mixtes ; validation/reload/DNS/archives réels ; rollback exercé. |
| **EXÉCUTÉ** | `native-management-auto` | 12 transactions, dont 2 lots mixtes ; validation/reload/DNS/archives réels ; rollback exercé. |
| **EXÉCUTÉ** | `loopback` | 109 configurations chargées, 345 contrôles DNS, fichiers natifs inchangés. |
| **EXÉCUTÉ** | `logging` | 3 fichiers écrits, rotation réelle à 10 MiB, 5 archives, refus et transfert journalisés. |
| **EXÉCUTÉ** | `preflight` | `{"inventories":3,"hosts":12,"real_preflight_assertions":true,"target_facts_simulated":true}` |
| **EXÉCUTÉ** | `backup-after` | 9 workflows réussis : 5 existants, 3 injections de catalogues incomplets et les métadonnées de répertoires. |
| **EXÉCUTÉ** | `coverage` | 98.163934 % sur 1525 instructions du cœur management. |
| **EXÉCUTÉ** | `backup-before` | 5 workflows existants réussis ; les 4 nouveaux scénarios reproduisent les défauts. |
| **EXÉCUTÉ** | `axfr-stat-before` | 2 défauts reproduits : métadonnées stat partielles et diagnostic dig 9.11. |
| **EXÉCUTÉ** | `archive-stat-before` | `{"expected_regression_failures":1}` |
| **EXÉCUTÉ** | `replication-readiness-before` | `{"controlled_primary_delay_seconds":2,"startup_race_reproduced":true}` |
| **EXÉCUTÉ** | `replication-readiness-after` | `{"controlled_primary_delay_seconds":2,"dns_checks":61}` |
| **EXÉCUTÉ** | `shellcheck` | 7 scripts, 0 diagnostic standard ; 2 faux positifs de trap documentés ; 33 avis optionnels SC2310 relus. |

Les comptes de tests se recoupent : la suite ciblée et la couverture ne s’ajoutent pas à la suite de composants pour calculer un total de cas distincts. La couverture de 98,163934 % ne concerne que `roles/bind9/module_utils/bind9_management.py`, mesurée sous Python 3.11.15/coverage 7.16.2 ; elle ne qualifie pas tous les scripts Bash ni le système. Les tests de compatibilité utilisent le véritable Python 3.6.8, Ansible 2.9.27 et Jinja2 2.10.3. Les avertissements de dépréciation du lexer Jinja et de PyYAML restent visibles ; les assertions de contenu et de contrôle des caractères réussissent.

Les tests natifs utilisent BIND `9.11.36-16.el8_10.16`, NetworkManager `1.40.16-21.el8_10` et les fichiers de distribution dans des conteneurs EL8 jetables, sans réseau externe ni service de l’hôte. Les routes sont exercées avec une passerelle FRONT propre au profil et une route par défaut exclusivement BACK. Les transactions management utilisent validation BIND, rechargement, réponses DNS et archives réels ; SELinux/systemd restent des doubles dans ces workflows. Les tests de journalisation observent les fichiers et la rotation réels d’un démon de laboratoire.

Le laboratoire local a été interrompu pendant une première campagne. Les contrôles incomplets sont relancés avec concurrence limitée à deux traitements et ressources bornées ; seules les exécutions achevées ci-dessus servent de preuves. Les logs restent hors ZIP. `DELIVERY.json` conserve les résultats, empreintes de preuves et empreintes de toutes les sources de production testées.

## Reproduction et exploitation

Depuis la racine du projet sur un contrôleur Linux de recette avec les prérequis du README :

```bash
for script in roles/bind9/files/*.sh tests/*.sh; do bash -n "$script"; done
docker run --rm --network none --read-only \
  -v "$PWD:/mnt:ro" --workdir /mnt \
  koalaman/shellcheck@sha256:61862eba1fcf09a484ebcc6feea46f1782532571a34ed51fedf90dd25f925a8d \
  --norc --shell=bash roles/bind9/files/*.sh tests/*.sh
python -m pytest -q tests/test_configtool_axfr.py tests/test_archive_hardening.py tests/test_backup_retention.py tests/test_language_separation.py
```

Dans un laboratoire jetable Linux root avec namespaces de montage autorisés :

```bash
sudo --preserve-env=PATH BIND_TEST_REQUIRE_MOUNT_NS=1 "$(command -v python)" -m pytest -q tests/test_configtool_workflows.py
```

La CI conserve les autres workflows et seuils. Le [README](../README.md#validation) décrit la préparation des binaires/fichiers natifs et les commandes d’intégration DNS, routes et management. Ne pas lancer les suites qui manipulent les chemins système sous root sur un serveur de production.

Pour les routes, utiliser la nouvelle copie du projet sur le contrôleur : `bind-front-routes.sh` est transmis par `ansible.builtin.script` à chaque opération, sans installation permanente de ce fichier dans `/usr/local/sbin`. Contrôler d’abord le plan du tag `routes` avec `--check`.

Pour les deux utilitaires installés, employer une procédure de changement contrôlée : conserver une copie privée avec droits/empreintes de `/usr/local/sbin/bind-configtool` (root:root `0700`) et, s’il est installé, `/usr/local/sbin/named-healthcheck` (root:root `0750`). Vérifier qu’aucune opération n’est active, préparer les nouveaux fichiers dans le même système de fichiers, exécuter `bash -n` puis les remplacer par renommage avec les droits/labels requis. Contrôler les commandes de lecture et DNS. Le ZIP ne modifie pas automatiquement les serveurs. Un déploiement complet reste une reconstruction et ne doit pas servir à une simple mise à jour d’outil sur une configuration manuelle live.

Le rollback de cette livraison consiste à remettre la copie précédente du projet sur le contrôleur et les utilitaires sauvegardés avant remplacement, puis à réexécuter les contrôles DNS/routes en lecture. Aucune migration de schéma ni de journal n’est requise. Ce rollback ne restaure pas une configuration/zone déjà modifiée par une opération distincte ; pour ces données, utiliser la procédure de reprise et l’archive correspondante. Ne pas effacer un journal de reprise pour forcer le passage.

## Contrôles restant à exécuter

**Qualification de production : NON TERMINÉE.** Tous les défauts fonctionnels identifiés dans cette passe ont une correction et une non-régression ; cela ne démontre pas l’absence absolue de bugs dans tous les environnements.

| Statut | Raison et prérequis | Procédure | Attendu et risque restant |
|---|---|---|---|
| **NON EXÉCUTÉ** | Aucune paire réelle de vos serveurs RHEL 8 accessible ; laboratoire représentatif, inventaire, clients et secours ADM nécessaires. | Exécuter le déploiement, les tests DNS du README, un plan `--tags routes --check`, l’application puis une deuxième exécution en recette. Tester CRUD/checksums et le sceau d’hibernation. | DNS stable, route via la passerelle FRONT, deuxième exécution sans changement, paramètres manuels conservés et conflits refusés. Restent versions locales et état réel des profils/fichiers. |
| **NON EXÉCUTÉ** | Pas de systemd/SELinux enforcing/firewalld représentatifs dans les conteneurs. | Tester les services natifs, labels et flux sur RHEL, puis reboot un hôte à la fois. | DNS/routes persistants, ADM/BACK conservés, accès aux logs autorisé. Restent politique SELinux, démarrage et pare-feu réels. |
| **NON EXÉCUTÉ** | Pas de matériel ni de stockage de recette contrôlable ; sauvegardes exportées et paire de secours nécessaires. | En recette, interrompre une opération et simuler espace plein/panne d’écriture ; contrôler la reprise, puis restaurer une archive vérifiée. | Refus explicite des états ambigus et préservation des données ; mesurer RPO/RTO. Restent panne électrique et garanties du stockage. |
| **NON EXÉCUTÉ** | Aucun trafic/objectif de charge représentatif fourni. | Mesurer latence/débit DNS, temps de plan/apply/management, mémoire et stockage sous charge attendue. | Respect des objectifs d’exploitation. Aucun gain de performance de cette révision n’est revendiqué ; les anciennes mesures restent historiques. |
| **NON EXÉCUTÉ** | Aucune exécution du dépôt GitHub ni de toute la matrice CI distante. | Exécuter `.github/workflows/ci.yml` sur les combinaisons déclarées, avec Docker disponible pour l’analyse figée. | Contrôles de chaque combinaison réussis. Les autres versions d’Ansible et les plateformes hors EL8 ne sont pas qualifiées par cette passe. |

Les preuves présentes ne doivent pas être remplacées par des affirmations « zéro bug » ou « production certifiée ». La livraison reste traçable et les prérequis manquants explicités.
