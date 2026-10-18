# Vérifications et impacts — intégrité de la configuration live

Ce document décrit la révision intégrité. Pour les contrôles courants, voir [VALIDATION_FIABILITE_IO.md](VALIDATION_FIABILITE_IO.md) et les [garanties I/O](FIABILITE_DONNEES_ET_IO.md).

Révision **2026.10.17-management-integrity.1**, source immédiate
`Bind9-Ansible-management-lots.zip`, SHA-256
`b88f5bfd1e8a2c0835794a09f250a8c2659d2a78a9f88f96016ef5fa575f3dc6`.
Les archives précédentes et les références `sources/` restent inchangées.
Les rapports des révisions précédentes sont historiques, pas des résultats
réexécutés implicitement pour celle-ci.

## Décisions et matrice d'impact

| Partie | Défaut ou risque traité et comportement final |
|---|---|
| `SafeFiles.read`, `Configuration` | Une lecture pouvait assembler un état incohérent pendant une intervention. Vérification fstat/lstat avant/après lecture, puis recontrôle des octets et métadonnées du graphe observé. Journal `.jnl` pendant également refusé. |
| `SafeFiles.path` | Le répertoire natif EL8 root:named `1770` était refusé par la règle générique. Exception au chemin/mode exact, sans chmod serveur ; les fichiers statiques restent root, sans écriture groupe/autres, liens ou types spéciaux. Protection du sticky bit vérifiée par un processus non root. |
| `Manager.option_location`, `read`, `option` | La recherche limitée au bloc principal pouvait créer une option déjà présente dans un include. Lecture complète des déclarations explicites, ambiguïtés refusées, duplication et réécriture d'un include externe refusées. |
| `ZoneDocument.change` | `create` pouvait ajouter un autre type au même nom. Désormais tout nom propriétaire déjà présent dans la même zone entraîne un refus. Rupture volontaire limitée à cette opération ; update/delete conservent la sélection nom/type existante. |
| `Manager.run`, `commit`, `assert_state` | Alignement des opérations unitaires sur les lots : candidat final complet et vérification de toutes les dépendances avant/après les publications et après santé DNS. Vérification des inodes, permissions et propriété du verrou. |
| `publish`, `labels` | Contrôle répété après les I/O du temporaire avant remplacement ; reçu de publication par inode avant fsync du répertoire ; contrôle après relabel, en autorisant seulement le changement de ctime attendu de SELinux. Données créées avant déclarations, purges après leur retrait. |
| Rollback | Un create échoué pouvait supprimer un fichier concurrent aux mêmes octets ; un fichier remplacé à contenu égal pouvait être adopté à tort. Seules les publications réellement effectuées sont restaurables, avec contrôles d'identité et métadonnées. Le graphe réellement restauré est validé avant reload ; une entrée manuelle invalide ou une reprise incomplète conserve `recovery-required` et le verrou. |
| Tests et CI | 32 nouveaux scénarios de régression/intégrité, dont concurrence entre processus, observation instable, options incluses, créations conflictuelles et sticky bit natif. Suite ajoutée au job de couverture, seuil de 98 % maintenu. |
| Banc natif | Adresse upstream de laboratoire paramétrable, strictement limitée à `127.0.0.1` ou `127.0.0.6`, ports de test distincts. Ownership explicite des répertoires runtime du banc, adapté au processus de test root. Aucune modification des permissions des serveurs de production. |
| Déploiement, DNS et réseau | Playbooks d'entrée, templates, règles DNS, routes FRONT, inventaires, defaults et dépendances de production inchangés. Le management ne reconstruit toujours pas depuis l'inventaire. Le déploiement forcé conserve sa sémantique distincte de reconstruction. |
| Documentation et livraison | Contrat de préservation, migration de create, protocole opérateur, limites, preuves, manifeste et archive actualisés. |

## Résultats propres à cette révision

| Statut | Contrôle | Résultat observé et portée |
|---|---|---|
| **EXÉCUTÉ** | Reproduction avant correction | Les 16 premiers scénarios ont produit 12 échecs et 4 réussites sur le moteur précédent. Ils reproduisent notamment pertes de fichiers concurrents, absence de recherche dans les includes, doublons de noms et contrôle insuffisant de métadonnées/verrou. Le refus du répertoire natif EL8 a ensuite été reproduit avec les vrais paquets. |
| **EXÉCUTÉ** | Suite finale EL8 | **322 tests réussis** : CRUD, refus, durcissement, lots, listings, transport Ansible, contrats de déploiement et 32 nouveaux scénarios. Python **3.6.8**, Ansible **2.9.27**, Jinja2 **2.10.3**, vrai `named-checkconf` **9.11.36-16.el8_10.16**. Les commandes DNS/SELinux sont doublées dans les tests de composants et transport ; leur validation native distincte figure ci-dessous. |
| **EXÉCUTÉ** | Couverture finale du moteur | **98,14 %**, soit **1211/1234 lignes**, avec seuil de 98 % conservé et sans nouvelle exclusion. Mesure du moteur Python, pas couverture exhaustive YAML, OS, réseau ou branches. |
| **EXÉCUTÉ** | Concurrence réelle | Deux processus avec la même approbation : un seul commit. Second processus exclu par la lease ou refusé sur checksum périmé. Tests de perte/remplacement du verrou, et processus ayant abandonné root incapable de supprimer/remplacer une entrée root sous `/var/named` 1770. |
| **EXÉCUTÉ** | Démons BIND EL8, DNSSEC `no` puis `auto` | **12 transactions committées par mode**, dont 2 lots mixtes ; réponses DNS réelles UDP/TCP, RNDC, archives de sauvegarde réelles, listings, refus de RDATA invalide tardif, rollback unitaire et rollback commun d'un lot dont le forwarder devient indisponible. SELinux est un double explicite. |
| **EXÉCUTÉ** | Analyse et compatibilité syntaxique | Pyflakes 3.2.0 et Bandit 1.7.10 ciblés sans alerte ; compilation des fichiers concernés avec Python 3.6.8, syntaxe du lanceur shell, six syntax-checks des deux playbooks sur `prod`, `preprod`, `dev`. |
| **EXÉCUTÉ** | Livraison | Comparaison du périmètre avec la source immédiate, manifeste SHA-256, vérification des fichiers ZIP/octet source, CRC, chemins et modes, exclusion des caches/temporaires/journaux bruts, double construction identique. |

Le laboratoire est un conteneur **standard** Rocky Linux 8.9 utilisant les paquets
EL8.10, sans capacité ajoutée, sans privilège étendu ni désactivation de seccomp.
Pendant les recettes natives : seul réseau effectif `none`, aucune route externe,
aucun port publié, source montée en lecture seule. Les démons du banc s'exécutent
sous root dans le conteneur ; cela ne qualifie pas l'identité et les politiques
du service systemd réel. Les empreintes des sorties sont conservées dans
[Historique des validations](NETTOYAGE_ET_VALIDATION.md#historique), les journaux bruts hors ZIP.

Une première mesure de couverture était à 97,83 % : les scénarios d'erreurs de
lecture/reprise ont été complétés, sans réduire le seuil. La première préparation
EL8 manquait du transport YAML ; les dépendances de recette ont été installées
puis la suite complète rejouée. Les échecs initiaux du banc natif ont révélé le
répertoire sticky refusé et deux différences d'environnement : interface loopback
non ajoutée dans le conteneur standard et répertoires RPM runtime appartenant à
named. Le correctif du moteur et les fixtures du banc ont été testés, puis les
deux modes DNSSEC rejoués avec succès. Aucun de ces échecs intermédiaires n'est
présenté comme un contrôle final encore ouvert.

## Reproduction

Depuis la racine du projet, dans un laboratoire Linux root jetable avec
Ansible 2.9.27, pytest et coverage compatibles avec le Python du laboratoire :

```bash
export ANSIBLE_CONFIG="$PWD/ansible.cfg"
export BIND_MANAGEMENT_CHECKCONF=/usr/sbin/named-checkconf
python3 -m coverage run --include='*/roles/bind9/module_utils/bind9_management.py' \
  -m pytest -q tests/test_management.py tests/test_management_refusals.py \
  tests/test_management_hardening.py tests/test_management_ansible.py \
  tests/test_management_batch.py tests/test_management_batch_ansible.py \
  tests/test_management_list.py tests/test_management_live_integrity.py \
  tests/test_deployment_contract.py
python3 -m coverage report -m --precision=2 --fail-under=98
```

Le banc natif utilise toujours le lanceur documenté dans
[VALIDATION_MANAGEMENT.md](VALIDATION_MANAGEMENT.md#reproduction-en-laboratoire).
Autre possibilité : conteneur standard jetable, réseau `none`, sans ports publiés,
avec système de fichiers propre et paquets BIND EL8 déjà installés. Avant recette,
copier les fichiers officiels `named.localhost`, `named.loopback`, `named.empty`,
`named.rfc1912.zones`, `named.root.key` dans `/tmp/native`. Créer `/tmp/bindbin`
avec des liens vers les binaires installés `named-checkconf`, `rndc` et `dig`.
**Uniquement dans ce conteneur jetable**, pour chaque mode `no` puis `auto` :

```bash
export BIND_MANAGEMENT_ISOLATED=1
export BIND_MANAGEMENT_TEST_UPSTREAM=127.0.0.1
export BIND_MANAGEMENT_DNSSEC=auto
python3 tests/integration_management.py \
  --bin-dir /tmp/bindbin --native-dir /tmp/native --named /usr/sbin/named
```

Le banc réécrit `/etc/named.conf` et ses propres données ; ne jamais l'exécuter
sur un serveur DNS opérationnel. Avec le lanceur en namespaces, le comportement
par défaut reste l'upstream `127.0.0.6`. Les dépendances et fixtures de recette
ne sont pas des dépendances supplémentaires du rôle de production.

## Limites et qualification restante

**Qualification de la paire utilisateur : NON TERMINÉE.** Le changement est testé
dans les périmètres ci-dessus ; aucune intervention sur vos serveurs n'a eu lieu.

| Statut | Contrôle, raison et prérequis | Procédure, résultat attendu et risque restant |
|---|---|---|
| **NON EXÉCUTÉ** | Paire RHEL 8 utilisateur, absente de l'environnement. Pré requis : recette représentative, sauvegardes, inventaire et accès SSH ADM. | Exécuter les listings et le lot du guide, `--check`, puis apply ; vérifier SOA, records, UDP/TCP et transfert TSIG BACK. Restent les chemins et la convergence propres à la paire. |
| **NON EXÉCUTÉ** | Politique SELinux, service systemd named et firewalld réels. Aucun hôte représentatif avec ces services/politiques n'est accessible. | `named-checkconf -z`, `ls -lZ` des fichiers concernés, `systemctl status named`, `journalctl -u named`, essais clients et redémarrage en recette. Attendus : labels, identité, permissions, service et flux persistants corrects. Le conteneur ne démontre pas cette intégration système. |
| **NON EXÉCUTÉ** | Crash physique, disque défaillant, charge et disponibilité ; pas de banc de panne/charge autorisé. | En recette : interrompre une publication multi-fichiers, préserver journal/lease et appliquer le runbook ; mesurer délais et ressources sous charge cible. Attendus : reprise contrôlée et objectifs d'exploitation respectés. Atomicité multi-fichiers et disponibilité continue non démontrées. |
| **NON EXÉCUTÉ** | CI distante et matrice complète ; aucun lancement distant effectué. | Exécuter le workflow livré sur le dépôt autorisé. Attendus : tous les jobs requis réussis, couverture au moins 98 %. Les versions non rejouées localement restent à qualifier. |

Le protocole opérateur et ses limites sont décrits dans
[INTEGRITE_CONFIGURATION_LIVE.md](INTEGRITE_CONFIGURATION_LIVE.md) : une écriture
root non coopérative peut encore courir entre contrôle et renommage. Les
interventions manuelles doivent prendre le verrou commun. En cas de conflit,
préserver l'état live et examiner le journal ; ne jamais restaurer aveuglément
une archive complète ni supprimer automatiquement une lease. Un échec secondaire
tardif ne restaure pas automatiquement un primaire déjà committé.

Pour revenir au moteur précédent, conserver une copie de l'état live et des
journaux ; le remplacement du code ne restaure pas les données. Restaurer
uniquement les cibles sélectionnées après résolution des conflits, validation
native et contrôles DNS. L'ancien moteur ne possède pas les protections ajoutées
dans cette révision : son retour n'est pas un correctif aux conflits présents.
