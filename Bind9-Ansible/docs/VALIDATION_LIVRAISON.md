# Rapport de livraison — Bind9-Ansible V4

Date : 12 octobre 2026. Source : archive utilisateur `bind9-ansible-v4-path-hardened.zip`.
Les résultats ci-dessous concernent cette livraison documentaire, pas une recette de production.

## Provenance et modifications

SHA-256 source : `96739671a4a62c5ebc307789fefe91c420200eb2d1b2b01068c3701627a2fe94`.

Les 108 fichiers source sont conservés. Le README français est réécrit à partir du code.
Les documents annexes sont renommés ; les descriptions erronées de migration destructive,
de TSIG Vault, de forwarding FRONT et du nom de l'utilitaire de reprise sont corrigées.
Les seules modifications hors Markdown sont deux libellés : nom du play et description
du service de surveillance. Les octets de ces fichiers correspondent exactement à la
substitution `Bind9Project` → `Bind9-Ansible` ; aucune logique n'est modifiée.
L'inventaire, les tâches, scripts, defaults, filtres, tests et la CI sont conservés.
Aucun code ni document provenant de la V5 locale n'est intégré.

Fichiers modifiés par rapport à l'archive fournie :

- `README.md`
- `docs/BACKUP_AND_RECOVERY.md`
- `docs/FLAT_NAMED_DIRECTORY.md`
- `docs/HARDENING.md`
- `docs/HOST_VARIABLES.md`
- `docs/INVENTORY.md`
- `docs/NETWORK_POLICY.md`
- `docs/PRODUCTION_READINESS.md`
- `docs/RELEASE_V4.md`
- `docs/REVERSE_DNS_AND_TAGS.md`
- `docs/ROBUSTNESS.md`
- `docs/SECURITY_REVIEW.md`
- `docs/TSIG_AUTOMATIC.md`
- `docs/VARIABLE_SINGLE_SOURCE.md`
- `docs/VERIFICATION_HARDENING.md`
- `playbooks/site.yml`
- `roles/bind9/templates/named-healthcheck.service.j2`

Ajouts dans le ZIP : ce rapport et `MANIFEST.sha256`. Le README livré séparément est
identique à celui du ZIP. Les modes Unix des membres source sont conservés.

## Contrôles observés

| Contrôle | Statut | Résultat |
|---|---|---|
| Intégrité source | EXÉCUTÉ | Contrôle CRC ZIP, 108 membres lus |
| Syntaxe Python | EXÉCUTÉ | 36 fichiers compilés en mémoire avec `compile`, Python 3.14, aucune erreur |
| Tests sans dépendances externes | EXÉCUTÉ | 31 fonctions existantes exécutées via `runpy`, 31 réussites ; il ne s'agit pas de la suite pytest complète |
| Tables du README | EXÉCUTÉ | 13 variables de defaults et 12 tags vérifiés présents |
| Non-altération des sources critiques | EXÉCUTÉ | 68 fichiers comparés octet pour octet à la source, identiques |
| Périmètre du diff | EXÉCUTÉ | Uniquement Markdown et deux substitutions de libellés vérifiées |
| ZIP final | EXÉCUTÉ | CRC, liste des membres, données extraites et manifest SHA-256 vérifiés après écriture |
| Reproductibilité du conteneur ZIP | EXÉCUTÉ | Deux générations en mémoire et sur disque comparées ; contenu identique dans cet environnement |
| Liens Markdown du README | EXÉCUTÉ | Cibles relatives existantes ; clôture des blocs de code vérifiée |

Fonctions existantes appelées directement :

- `test_acl_hardening.py::test_stale_acl_removed_only_when_both_lists_empty`
- `test_acl_hardening.py::test_acl_values_are_validated_before_rendering`
- `test_flat_named_directory.py::test_no_nested_named_d_in_deployment`
- `test_flat_named_directory.py::test_named_entrypoint_is_includes_only`
- `test_flat_named_directory.py::test_zone_modules_in_view`
- `test_front_security.py::test_front_view_is_nonrecursive_and_has_no_forward_include`
- `test_front_security.py::test_client_address_filter`
- `test_hardening_regression.py::test_front_and_peer_views_separated`
- `test_hardening_regression.py::test_backup_without_stop_or_delete`
- `test_hardening_regression.py::test_inventory_vars_are_separated`
- `test_modular_tasks.py::test_main_imports_all_stages`
- `test_modular_tasks.py::test_all_bind_paths_use_named_d`
- `test_modular_tasks.py::test_single_internal_forwarder_and_fake_dns`
- `test_modular_tasks.py::test_named_config_is_checked_before_service_activation`
- `test_named_orchestration.py::test_named_conf_only_includes_modules`
- `test_named_orchestration.py::test_zone_modules_are_included_in_views_only`
- `test_numbered_includes.py::test_include_order`
- `test_numbered_includes.py::test_zones_in_view`
- `test_numbered_includes.py::test_templates_and_tasks`
- `test_project.py::test_templates_present`
- `test_project.py::test_no_unrestricted_transfers`
- `test_project.py::test_forwarding_is_conditional`
- `test_project.py::test_tsig_secret_not_hardcoded`
- `test_robustness.py::test_filesystem_preflight`
- `test_robustness.py::test_do_not_change_vendor_parent_permissions`
- `test_robustness.py::test_per_file_backups_and_zone_validation`
- `test_robustness.py::test_baseline_rechecked`
- `test_robustness.py::test_authoritative_primary_readiness`
- `test_zone_config_layout.py::test_zone_config_modules`
- `test_zone_config_layout.py::test_non_destructive_creation`
- `test_zone_module_names.py::test_descriptive_zone_module_names`

La sélection retient les modules de tests ne nécessitant ni pytest, ni PyYAML,
ni Jinja2, ni subprocess, puis appelle leurs fonctions `test_*` sans paramètres.
Les fichiers de tests eux-mêmes ne sont ni modifiés ni affaiblis.

## Contrôles manquants

| Contrôle | Statut | Raison, procédure, prérequis, attendu et risque |
|---|---|---|
| Suite pytest complète | NON EXÉCUTÉ | pytest/PyYAML/Jinja2 absents ; installation locale impossible (résolution pypi.org). Reproduire les commandes Linux du README avec les dépendances CI ; attendre tous les tests réussis ; non-régression complète non attestée |
| Syntaxe Ansible, YAML et rendu intégré | NON EXÉCUTÉ | Outils absents ; contrôleur Linux préparé, commande `ansible-playbook -i inventory/hosts.ini playbooks/site.yml --syntax-check` ; attendre code 0 ; intégration non attestée |
| Syntaxe Bash | NON EXÉCUTÉ | Bash ne démarre pas, code Windows -1073741502 y compris avec `--version`. Sous Linux : `bash -n roles/bind9/files/bind-backup.sh roles/bind9/files/named-healthcheck` ; attendre code 0 ; syntaxe non attestée ici |
| ansible-lint | NON EXÉCUTÉ | Outil absent ; installer une version compatible, lancer `ansible-lint playbooks/site.yml roles/bind9` ; traiter les diagnostics ; qualité idiomatique non attestée |
| Molecule | NON EXÉCUTÉ | Aucun scénario ni cible de labo ; préparer un scénario RHEL 8 puis `molecule test` ; attendre convergence et reprise ; aucune preuve end-to-end |
| Couverture | NON EXÉCUTÉ | Instrumentation et seuil absents ; définir le périmètre, utiliser pytest-cov sur Linux ; attendre le seuil convenu ; chemins non testés possibles |
| RHEL, BIND, réseau, SELinux, sauvegarde/restauration | NON EXÉCUTÉ | Pas de cibles ; appliquer la matrice de recette du README sur RHEL 8 isolé ; attendre résultats indiqués ; fonctionnement et reprise non qualifiés |

La préparation d'un environnement virtuel local a échoué à l'étape ensurepip ;
une tentative d'installation des dépendances dans un répertoire local a ensuite
échoué faute de résolution réseau. Ces limites ne constituent pas des résultats
de tests applicatifs. Aucun déploiement ni changement sur serveur DNS n'a été tenté.

## Limites conservées

Le minimum Ansible 2.9 déclaré n'est pas démontré ; le workflow configure 2.16.14.
Les tags partiels et check-mode ne sont pas qualifiés comme parcours autonomes sûrs.
Le serial inverse réconcilié peut diverger du serial attendu par le healthcheck
(déduction des sources, pas reproduction sur BIND). Les sauvegardes horaires ne
sont pas planifiées. Les transactions ne couvrent pas l'ensemble des effets du rôle.
Les instructions détaillées et la recette requise figurent dans le README.

Qualification de production : **NON TERMINÉE**, dans l'attente des contrôles non exécutés.
