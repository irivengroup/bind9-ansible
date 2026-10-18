# Durcissement du management — 17 octobre 2026

Rapport historique de la source immédiate. Pour la révision courante, consulter [VALIDATION_FIABILITE_IO.md](VALIDATION_FIABILITE_IO.md).

Révision `2026.10.17-management-hardened.1` de **Bind9-Ansible**. Source immédiate : `Bind9-Ansible-management.zip`, SHA-256 `d336fe07d3c2b53bc3deeefe6e4427931b0497d45960dc580f803fbda13b67fc`. La livraison précédente et les références synchronisées restent intactes.

## Corrections et impacts

| Partie du code | Défaut observé et comportement corrigé | Limite conservée |
|---|---|---|
| `roles/bind9/library/bind9_manage.py` | Une lecture ou simulation testait seulement l’existence du verrou avant de lire. Toutes les actions acquièrent maintenant la même lease exclusive, pendant l’observation, la validation et l’éventuelle écriture. Un verrou existant, y compris fichier ou lien pendant, bloque l’action. | La lease est libérée entre `--check` et l’application : le checksum reste indispensable à chaque écriture. |
| `roles/bind9/module_utils/bind9_management.py` — `Manager.lease` | Le répertoire de verrou pouvait être remplacé avant le `rmdir` final. La libération vérifie maintenant son identité périphérique/inode. Un remplacement est conservé ; une disparition est signalée. | Un administrateur root non coopératif n’est pas neutralisé par ce protocole. |
| Même module — entrées | Refus contrôlé des requêtes mal typées, clés non textuelles et noms d’options invalides, y compris en lecture. L’ordre du contrôle des champs inconnus est préservé. Les adresses de forwarders exigent une chaîne IP explicite ; les entiers et booléens ne sont plus interprétés comme des adresses. | Les valeurs BIND et RDATA nécessitent toujours la validation native du candidat. |
| Même module — `Manager.forwarders` | L’IPv4 contenue dans une IPv6 mappée reçoit aussi les contrôles multicast, link-local et adresse non spécifiée. La diffusion limitée IPv4 est refusée, directement ou sous forme mappée. | La passerelle, le routage, la joignabilité et les politiques cryptographiques restent des prérequis d’exploitation. |
| Même module — `DnsReply`, `Manager.health` | Les recherches de sous-chaînes pouvaient accepter une réponse pour un autre nom avec la même adresse ou le même serial, ou un texte RDATA contenant `status: NOERROR`. Le statut doit figurer dans une ligne de commentaire de réponse ; propriétaire et type doivent correspondre exactement. Les SOA locaux exigent aussi autorité et serial exact. | Les délais et tentatives existants sont conservés ; ces contrôles ne démontrent pas la disponibilité sous charge. |
| `tests/test_management_hardening.py`, CI | Ajout de scénarios d’entrées, d’adresses mappées, de réponses trompeuses, de propriété du verrou et de transport Ansible lecture/simulation. La CI inclut ces tests dans la mesure du moteur, au même seuil de 98 %. | La présence du workflow ne prouve pas son exécution distante. |
| README, guide de management, livraison | Contrats et effets de la lease documentés, preuves courantes distinguées des rapports historiques, manifeste et archive reconstruits. | Les modifications de management ne mettent toujours pas à jour l’inventaire. |

Le template `named.conf.j2`, les tâches de déploiement, les variables DNSSEC, les ACL, le routage FRONT, les inventaires, les sauvegardes et les templates de zones sont inchangés par rapport à la source immédiate. Aucun paquet, collection, service ou dépendance Python de production n’est ajouté. Les types de requête et noms publics ne changent pas.

## Vérifications de cette révision

Les sorties synthétiques correspondantes sont dans [Historique des validations](NETTOYAGE_ET_VALIDATION.md#historique). Les chiffres des rapports antérieurs ne sont pas additionnés aux résultats de cette révision.

| Statut | Contrôle | Résultat et périmètre |
|---|---|---|
| **EXÉCUTÉ** | Reproduction sur copie de la source immédiate | 18 scénarios reproduisent les défauts avant correction ; quatre cas de refus déjà corrects réussissent. Les contrôles ajoutés sur les nouveaux mécanismes ne sont pas présentés comme des défauts historiques. |
| **EXÉCUTÉ** | CRUD, refus, rollback, propriété/exclusion du verrou, transport réel Ansible et contrats de déploiement | 212 tests réussis avec Ansible 2.9.27 et Jinja2 2.10.3. Les outils système sont des doubles dans les tests de transport ; aucun serveur utilisateur n’est contacté. |
| **EXÉCUTÉ** | Couverture du moteur de management | 925 lignes exécutées sur 943, soit 98,09 % ; seuil de 98 % conservé. Cette mesure ne couvre pas exhaustivement YAML, le transport distant ou le système RHEL. |
| **EXÉCUTÉ** | Validation native des candidats | 155 tests réussis avec `named-checkconf` 9.11 EL8 ; autres frontières système doublées dans cette suite. |
| **EXÉCUTÉ** | Transactions natives avec DNSSEC `no` puis `auto` | Dans chaque mode, dix transactions et un rollback après remplacement par un forwarder indisponible malgré une réponse préalablement cachée. Vrai BIND EL8, RNDC, réponses DNS UDP/TCP, archives et fichiers ; espaces réseau et montages privés. `restorecon` reste un double. |
| **EXÉCUTÉ** | Python 3.6.8 EL8 | Compilation des Python de production, transaction protégée et préservation du texte, nouveaux refus de forwarders et contrôle du propriétaire DNS. Les commandes natives de ce scénario sont doublées. |
| **EXÉCUTÉ** | Syntaxe et analyse statique | Les deux playbooks sur trois inventaires, compilation de 61 fichiers Python, syntaxe shell, Pyflakes et Bandit ciblé réussissent. |

Les deux échecs intermédiaires sur les messages de refus ont été corrigés en rétablissant l’ordre des validations, sans retirer ni assouplir les assertions existantes. Les dépréciations du runtime historique restent visibles. Les essais du second moteur Jinja et le contrôle du paquet sont consignés dans les preuves de cette livraison.

## Reproduction

Dans un laboratoire Linux root jetable, avec Ansible et les dépendances de la CI, depuis la racine du projet :

```bash
export ANSIBLE_CONFIG="$PWD/ansible.cfg"
python -m coverage run --include='*/roles/bind9/module_utils/bind9_management.py' \
  -m pytest -q tests/test_management.py tests/test_management_refusals.py \
  tests/test_management_hardening.py tests/test_management_ansible.py \
  tests/test_deployment_contract.py
python -m coverage report -m --fail-under=98
python -m pyflakes roles/bind9/module_utils roles/bind9/library tests
python -m bandit -q roles/bind9/module_utils/bind9_management.py roles/bind9/library/bind9_manage.py
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --syntax-check
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-management.yml --syntax-check
```

Les procédures des démons natifs, de sauvegarde et de reprise restent celles de [MANAGEMENT.md](MANAGEMENT.md) et du [rapport historique](VALIDATION_MANAGEMENT.md#reproduction-en-laboratoire). Elles nécessitent les outils BIND et fichiers natifs correspondants, root, `unshare` et `ip`. Ne pas exécuter les suites privilégiées sur un DNS opérationnel.

## Reprise et limites opérationnelles

Lecture et simulation peuvent créer le répertoire d’état privé et prennent un verrou temporaire ; elles ne publient pas de configuration DNS, ne rechargent pas le service et ne créent pas de sauvegarde. Une lease interrompue empêche aussi ces observations et exige la reprise documentée. Ne pas supprimer automatiquement un verrou remplacé ou laissé par une transaction.

Une écriture conserve les contrôles SHA-256, sauvegardes, journal privé et rollback ciblé existants. En cas de checksum périmé, examiner le nouvel état avant de refaire la lecture. Une restauration logicielle de cette livraison ne restaure pas les configurations déjà changées : utiliser le journal et les sauvegardes de la transaction concernée.

**Qualification opérationnelle : NON TERMINÉE.** Aucune paire RHEL 8 utilisateur n’est accessible. Les essais ci-dessous nécessitent une paire jetable représentative, SSH ADM de secours et des sauvegardes vérifiées.

| Statut | Procédure restant à réaliser | Résultat attendu et risque restant |
|---|---|---|
| **NON EXÉCUTÉ** | Lire puis exécuter les CRUD du guide sur les deux RHEL, vérifier SOA et réponses UDP/TCP, tenter un ancien checksum après une intervention manuelle. | Refus de la révision périmée, préservation des données non ciblées, transfert TSIG BACK et convergence. Restent particularités des fichiers et du transport distant réels. |
| **NON EXÉCUTÉ** | Pendant une opération contrôlée, tenter une seconde lecture/simulation/écriture ; vérifier les labels avec SELinux enforcing, les unités natives et le redémarrage. | Exclusion sans vol de verrou, accès `named_t` et service persistant. Restent politique SELinux et intégration système locales. |
| **NON EXÉCUTÉ** | Interrompre une transaction multi-fichiers en recette, appliquer la reprise du guide, mesurer la disponibilité sous charge ; exécuter la matrice CI distante. | Reprise contrôlée et objectifs d’exploitation respectés. Crash physique, performance, variantes CI et atomicité de la paire ne sont pas démontrés. |

Le management reste statique, sans vues, et n’est pas une transaction distribuée : l’échec du secondaire ne restaure pas automatiquement le primaire. La lease exclut les outils coopératifs du projet ; elle ne verrouille pas un éditeur root externe.
