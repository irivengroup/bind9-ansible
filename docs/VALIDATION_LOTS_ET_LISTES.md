# Vérification et impacts — lots et listings

Révision **2026.10.17-management-batch.1** de Bind9-Ansible. Source immédiate : `Bind9-Ansible-management-durci.zip`, SHA-256 `355c62834c8e680ef86362e10d8f5665e8b48ff050c8055b9d50efa3545e9a55`. Les livraisons antérieures et `sources/` restent inchangés. Les preuves précédentes sont historiques ; les résultats ci-dessous sont propres à cette évolution.

## Impacts et décisions

| Domaine / fichier | Changement et invariant |
|---|---|
| `roles/bind9/module_utils/bind9_management.py` | Ajout de `BatchManager`, `SnapshotFiles` et `BoundedFiles` ; réutilisation des planificateurs CRUD, du commit gardé et du rollback. Une liste ordonnée produit un seul candidat, sans publication intermédiaire. Listing paginé des déclarations et données réellement présentes. |
| `roles/bind9/library/bind9_manage.py` | Sélection explicite du manager selon `resource`, avec le même transport privé et la même lease. Ancien format préservé. |
| `roles/bind9/tasks/tasks.d/manage/main.yml` | Contrats `batch` et `list`, empreintes de plan par hôte, catalogue public sans valeurs, refus d’un lot de records sans primaire précédent et contrôle des serials du secondaire. Le mode simulation hérité n’est pas écrasé. |
| `playbooks/bind9-management.yml` | Prévalidation en simulation sur tous les hôtes ciblés avant publication d’un lot ; application séquentielle avec recontrôle local. Une erreur secondaire de prévalidation ne modifie aucun primaire. |
| Contrats publics | Ajout de `resource: batch`, actions `read`/`apply`, `operations`, `expected_plan_checksum`, `bind_manage_plan_checksums` ; ajout de `action: list`, `zone`, `offset`, `limit` et métadonnées de pagination. Les requêtes unitaires existantes restent disponibles. |
| Données | Une seule avance de serial par fichier primaire existant changé ; serial demandé conservé pour une nouvelle zone avec ses records. Modification/purge d’un fichier primaire partagé par plusieurs zones refusée, y compris en CRUD unitaire. Les records sont écrits uniquement sur le primaire. Pas de PTR implicite ou de synchronisation vers l’inventaire. |
| Sécurité et limites | Contrôle de l’état et du plan, refus des doublons et conflits, limites de 64 opérations / 256 Kio, 512 assets / 64 Mio ; masquage des valeurs par défaut et journal privé sans copie des valeurs de requête. Les protections des options, zones natives, signatures et fichiers sont conservées. |
| Reprise | Une sauvegarde complète et un journal pour tout le lot par hôte, publication atomique par fichier et rollback conditionnel de toutes les cibles. Un conflit manuel reste préservé et peut laisser `recovery-required` avec la lease conservée. |
| Compatibilité et dépendances | Cible RHEL 8 / Ansible 2.9.27 / Python serveur 3.6.8. Aucune nouvelle collection, bibliothèque ou service de production. Déploiement, templates DNS, routes FRONT, inventaires et defaults inchangés. |
| Tests, CI et documentation | Suites de lots, listing, transport de paire et essais natifs étendues ; seuil du moteur conservé à 98 %. Exemples exécutables et guide d’exploitation ajoutés. |

## Résultats observés

| Statut | Contrôle | Résultat et périmètre |
|---|---|---|
| **EXÉCUTÉ** | Suite ciblée principale, Ansible 2.9.27 / Python contrôleur 3.8.20 / Jinja2 2.10.3 | 278 tests réussis : ancien CRUD, durcissement, lots, listing, transport et contrats de déploiement. Les frontières système du transport sont doublées. |
| **EXÉCUTÉ** | Exemple livré et héritage du mode simulation | Deux scénarios supplémentaires réussis, modes Jinja natif désactivé/activé. La même liste d’opérations passe de la prévisualisation à la simulation sans publication ni sauvegarde. Ces cas sont distincts des 278 précédents. |
| **EXÉCUTÉ** | Rejeu de la paire après correction du mode simulation | Quatre scénarios réussis : application mixte sur deux arbres locaux, deux modes Jinja natif, et refus des empreintes secondaire d’état ou de plan avant toute écriture primaire. Recouvrement avec la suite principale : ne pas additionner comme cas uniques. |
| **EXÉCUTÉ** | Moteur final et couverture | 253 tests de composants réussis sur le code final, dont dix scénarios supplémentaires de fichiers partagés. Couverture 98,44 % (1136/1154 lignes), seuil de 98 % conservé. Les transports sont mesurés séparément ; cette mesure ne couvre pas exhaustivement YAML, le réseau et le système RHEL. |
| **EXÉCUTÉ** | Second moteur Jinja | 61 scénarios lots/listing réussis avec Jinja2 3.0.3, Ansible 2.9.27. Recouvrement avec la suite principale. |
| **EXÉCUTÉ** | Candidats avec le validateur EL8 réel | 207 tests réussis avec `named-checkconf` 9.11 EL8 ; autres frontières système doublées dans cette suite. Le risque de fichier partagé a aussi été reproduit sur le moteur précédent avec ce validateur. |
| **EXÉCUTÉ** | BIND EL8 natif, DNSSEC `no` et `auto` | Dans chaque mode : dix transactions unitaires et deux lots mixtes committés ; rollback unitaire et rollback commun d’un lot dont le forwarder est indisponible ; refus natif d’un RDATA invalide tardif ; listings des options, zones et records avec le même checksum. Vrais DNS UDP/TCP, RNDC, fichiers et archives ; espaces de montage/réseau privés. `restorecon` est un double. |
| **EXÉCUTÉ** | Python serveur 3.6.8 EL8 | Compilation de production, transaction mixte option/zone/record avec serial initial conservé et listing paginé. Commandes système doublées dans ce scénario. |
| **EXÉCUTÉ** | Qualité et syntaxe | Pyflakes et Bandit ciblé sans alerte ; compilation de 64 fichiers Python, syntaxe shell ; deux playbooks sur trois inventaires. |
| **EXÉCUTÉ** | Livraison | Périmètre des fichiers comparé à la source, manifeste SHA-256, CRC, correspondance des octets ZIP/source, chemins et modes, liens locaux et double construction identique. Pas de caches, journaux bruts ou secrets actifs ajoutés. |

Les sorties synthétiques et empreintes des journaux locaux sont dans [PREUVES_LOTS_ET_LISTES.txt](PREUVES_LOTS_ET_LISTES.txt). Les journaux bruts restent hors archive et ne sont pas présentés comme des preuves d’accès aux serveurs utilisateur. La matrice CI distante n’a pas été exécutée.

Un test d’exemple a reproduit un écrasement du mode simulation hérité dans la première implémentation du lot. Le correctif conserve cet héritage et place la prévalidation dans un bloc explicitement en simulation ; l’exemple et les scénarios de paire concernés ont été rejoués. Un essai natif initial comparait aussi des fichiers runtime NTA, renouvelés par BIND : la comparaison porte désormais sur le graphe statique sous gestion et reste complétée par les réponses DNS après rollback. L’import inutilisé identifié par Pyflakes a été retiré. Aucun de ces échecs intermédiaires n’est présenté comme encore ouvert, ni masqué par une suppression d’assertion fonctionnelle existante.

## Reproduire les contrôles

Sur Linux root jetable avec les dépendances de la CI, depuis la racine :

```bash
export ANSIBLE_CONFIG="$PWD/ansible.cfg"
python -m coverage run --include='*/roles/bind9/module_utils/bind9_management.py' \
  -m pytest -q tests/test_management.py tests/test_management_refusals.py \
  tests/test_management_hardening.py tests/test_management_ansible.py \
  tests/test_management_batch.py tests/test_management_batch_ansible.py \
  tests/test_management_list.py tests/test_deployment_contract.py
python -m coverage report -m --fail-under=98
python -m pyflakes roles/bind9/module_utils roles/bind9/library tests
python -m bandit -q roles/bind9/module_utils/bind9_management.py roles/bind9/library/bind9_manage.py
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --syntax-check
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-management.yml --syntax-check
```

La suite actuelle comprend les deux nouveaux tests d’exemple et les dix scénarios de fichiers partagés en plus des 278 de la première campagne. Les procédures des démons natifs et leurs prérequis figurent dans [VALIDATION_MANAGEMENT.md](VALIDATION_MANAGEMENT.md#reproduction-en-laboratoire) ; le lanceur étendu exécute aussi les lots et listings. Ne pas exécuter les suites privilégiées sur un DNS opérationnel.

## Qualification restante et reprise

**Qualification opérationnelle : NON TERMINÉE.** Aucune paire RHEL 8 utilisateur n’est accessible. Préconditions communes : paire jetable représentative, inventaire et paquets cibles, SELinux enforcing, SSH ADM de secours et sauvegardes vérifiées.

| Statut | Procédure non exécutée et raison | Résultat attendu / risque restant |
|---|---|---|
| **NON EXÉCUTÉ** | Exécuter les listings et le lot du guide sur la paire RHEL ; comparer les SOA et réponses depuis les clients. Raison : aucun accès aux serveurs. | Données exactes, TSIG BACK et convergence réelle. Restent particularités des fichiers, Python distant et politiques de la paire. |
| **NON EXÉCUTÉ** | En recette, modifier manuellement l’état après aperçu, rendre un checksum secondaire périmé, puis provoquer un échec secondaire après commit primaire. | Refus préalable sans écriture dans le premier cas ; écart de paire détecté et reprise opérateur dans le second. Une transaction distribuée n’est pas fournie. |
| **NON EXÉCUTÉ** | Vérifier labels, unités, droits du service et redémarrage avec les politiques SELinux/firewalld réelles. | Service persistant et accès `named_t` corrects. Les doubles du laboratoire ne qualifient pas la politique système locale. |
| **NON EXÉCUTÉ** | Interrompre physiquement une publication multi-fichiers, appliquer le runbook de reprise ; mesurer charge, consommation mémoire/disque et délais ; exécuter la CI distante. | Restauration contrôlée et objectifs d’exploitation respectés. Crash, performance, disponibilité et variantes CI ne sont pas démontrés. |

Une validation de syntaxe et les smoke tests ne prouvent pas toutes les politiques fonctionnelles ou tous les chemins réseau. Les listings décrivent les déclarations explicites sur disque, pas les défauts compilés ni le cache du démon. Les options trouvées dans un include externe sont listées avec leur provenance ; cela ne rend pas cet include automatiquement éditable par le workflow.

Après échec, suivre le journal et la reprise de [MANAGEMENT.md](MANAGEMENT.md#transaction-sécurité-et-reprise). Ne pas supprimer automatiquement une lease interrompue ou remplacée. Le rollback du lot est local à l’hôte ; il ne restaure pas un primaire déjà committé si seul le secondaire échoue ensuite. Le retour à l’ancienne version du code ne restaure pas les données déjà modifiées : utiliser les sauvegardes de la transaction. Avant un déploiement forcé, réconcilier les changements de management dans les sources.
