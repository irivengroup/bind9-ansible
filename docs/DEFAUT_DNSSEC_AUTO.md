# DNSSEC automatique par défaut — 17 octobre 2026

La valeur par défaut de `bind_dnssec_active` passe de `false` à `auto`. Le template produit désormais `dnssec-validation auto;` sans surcharge de l’inventaire. Les valeurs explicites `false`, `true` et `auto`, ainsi que les chaînes CLI exactes déjà acceptées, conservent leur comportement. Aucun nom de variable ni dépendance n’est ajouté.

## Effet opérationnel et migration

- Sans surcharge, le rôle vérifie l’ancre native `/etc/named.root.key`, prépare `/var/named/dynamic`, installe le helper d’exceptions privées, le drop-in systemd et les unités de renouvellement. Le timer est activé. Les anciens inventaires sans cette variable activent donc DNSSEC au prochain déploiement complet.
- Une valeur explicite `bind_dnssec_active: false` reste prioritaire et conserve le mode désactivé, avec retrait des seuls composants DNSSEC gérés conformément au cycle de vie existant.
- Pour les zones privées non signées nécessitant une exception, conserver `dnssec_validate: false` sur le suffixe concerné. Ne pas étendre cette exception à toutes les zones par défaut. Les exceptions natives NTA couvrent ces suffixes et descendants, durent sept jours et sont renouvelées quotidiennement ; elles ne fournissent pas de garantie DNSSEC sur ce périmètre.
- L’ancre automatique n’est pas une signature de vos zones. Le rôle ne signe pas les zones autoritatives et ne transforme pas une zone privée non signée en zone vérifiable.
- Conserver les paquets/ancres de la distribution à jour, l’horloge synchronisée, RNDC local opérationnel et la connectivité vers les forwarders. La racine privée et NXDOMAIN hors périmètre restent inchangés.

Avant mise à niveau, sauvegarder BIND et les assets runtime, contrôler les surcharges d’inventaire/extra-vars et déployer d’abord sur la paire de recette, primaire puis secondaire. Employer le parcours `deploy` complet : copier seulement `named.conf` n’applique pas les exceptions privées ni les hooks.

En cas de retour au comportement précédent, définir explicitement `bind_dnssec_active: false` et rejouer le déploiement complet. Pour une restauration après panne, suivre [OPERATIONS.md](OPERATIONS.md) et le [README](../README.md#reprise) ; une perte du contrôleur nécessite les sauvegardes persistantes, le snapshot runtime en mémoire n’étant plus disponible.

## Parties du code impactées

| Fichiers | Changement |
|---|---|
| [defaults/main.yml](../roles/bind9/defaults/main.yml) | Défaut public `auto`. |
| [named.conf.j2](../roles/bind9/templates/named.conf.j2) | Repli `auto` si le template est rendu directement sans variable ; les surcharges explicites restent respectées. |
| [test_optional_dnssec.py](../tests/test_optional_dnssec.py) | Défaut, gates d’installation/activation, repli du template et maintien du mode désactivé explicite. |
| [test_dnssec_settings_ansible.py](../tests/test_dnssec_settings_ansible.py) | Absence de surcharge testée avec le vrai moteur Ansible et les deux modes de typage Jinja. |
| [test_dnssec_workflows.py](../tests/test_dnssec_workflows.py) | Installation et second passage sans variable fournie ; vérification du timer actif et activé, transitions et rollback conservés. |
| [Exemple deux zones](../examples/deux-zones-forwardees/README.md) | Variables et rendus primaire/secondaire en `auto`, exceptions limitées aux deux forwards privés non signés. |
| README et documents DNSSEC/exploitation | Valeur par défaut, procédures et exemples alignés ; les rapports de campagnes antérieures restent historiques. |

Les tâches de production, helpers, routes FRONT, ACL, firewalld, SELinux et inventaires ne sont pas modifiés par cette révision. Les gates existantes sélectionnent désormais le mode actif à partir du nouveau défaut. `DELIVERY.json` fournit la liste exacte des changements et distingue preuves rejouées et héritées.

## Validation et limites

Deux non-régressions ciblées reproduisent l’ancien comportement : valeur par défaut inactive et repli du template à `no`. Elles exigent désormais le mode `auto` et ses composants associés. Les résultats observés de cette révision figurent dans `DELIVERY.json` ; les journaux et clés temporaires de laboratoire sont exclus du ZIP.

| Statut | Résultat observé |
|---|---|
| **EXÉCUTÉ** | 549 tests réussis sous Ansible 2.9.27/Jinja 2.10.3 et les mêmes 549 sous Jinja 3.0.3, contrôleur Python 3.8.20. Les cas explicites `false`/`true`/`auto`, l’absence de surcharge et les valeurs invalides sont couverts. |
| **EXÉCUTÉ** | Avant/après : les deux assertions du nouveau contrat échouent sur le défaut et le repli antérieurs, puis réussissent sur cette révision. |
| **EXÉCUTÉ** | Neuf workflows runtime complémentaires : installation sans surcharge, timer actif/activé et second passage, modes explicites, migration, désactivation et rollback dans les deux directions. Cela représente 558 cas pytest distincts exécutés sur cette révision, sans additionner les répétitions entre moteurs. |
| **EXÉCUTÉ** | BIND EL8 9.11.36 et BIND 9.18 : chacun charge 109 configurations et réussit 282 contrôles DNS. Les contextes des inventaires chargent le nouveau défaut ; les modes explicites, signatures valides/invalides, exceptions privées et zones natives restent exercés. |
| **EXÉCUTÉ** | README : 57 liens, 16 ancres, 23 exemples Bash analysés et trois combinaisons clients sur la paire d’exemple via les assertions réelles du rôle. Références : 12 assets, 38 fichiers de tâches, 11 defaults et 11 filtres ; 66 YAML analysés. |

Les essais DNS utilisent des démons réels dans des réseaux isolés. Les workflows runtime utilisent le vrai moteur Ansible et des fichiers réels, avec un double systemd ; les facts RHEL des inventaires sont simulés. La couverture et l’analyse de sécurité Python de la livraison source sont des preuves héritées, non rejouées ici : les fichiers Python et shell de production restent identiques. Elles ne mesurent pas la couverture du YAML/Jinja modifié.

**NON EXÉCUTÉ — recette réelle RHEL 8.** Aucune cible de votre environnement n’est accessible. Prérequis : paire de recette, sauvegardes exportées, inventaire et forwarders représentatifs, ancre native à jour, horloge et accès ADM de secours. Exécuter `deploy`, les [tests fonctionnels](TESTS_FONCTIONNELS.md), un second passage, puis les tests de reprise et un redémarrage contrôlé. Attendu : `dnssec-validation auto`, localhost et forwards opérationnels, rejet des signatures invalides hors exceptions, timer fonctionnel et routes inchangées. Risque restant : états locaux, politiques SELinux/systemd et chaînes DNSSEC des upstreams non reproduits en laboratoire.

**NON EXÉCUTÉ — charge, rotation RFC5011 et renouvellement au-delà de sept jours.** Nécessite un laboratoire représentatif et une observation prolongée. Contrôler latence, erreurs, mémoire, ancres et renouvellements après démarrage/reload. Attendu : maintien des réponses et des exceptions limitées sans expiration involontaire. La qualification opérationnelle reste **NON TERMINÉE**.

**NON EXÉCUTÉ — CI distante.** Aucun lancement dans un dépôt distant n’est effectué. Prérequis : dépôt et runners Linux disposant des droits d’isolation nécessaires. Exécuter la matrice existante de `.github/workflows/ci.yml` après import des sources. Attendu : jobs applicables réussis, sans contournement des seuils. Les autres versions Ansible de cette matrice ne sont pas qualifiées par les seuls essais locaux sous Ansible 2.9.27.
