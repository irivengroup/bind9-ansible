# Durcissement ciblé des routes et du management

Révision **2026.10.19-hardening.1**. Source exclusive : `Bind9-Ansible-routes-management.zip`, SHA-256 `d58efa8904126d706ef129510114859fd71ef6031362a8b3a9adf5f29f506155`. L’archive source est conservée intacte. Aucun déploiement ni changement réseau n’a été exécuté sur vos serveurs.

## Corrections reproduites et impact

| Défaut observé dans la base | Risque | Correction et non-régression |
|---|---|---|
| Une passerelle FRONT égale à une autre IPv4 locale active était acceptée : adresse secondaire de FRONT ou adresse d’une autre interface. | Ajout d’une route avec le serveur lui-même comme prochain saut. | Refus avant écriture si la passerelle est l’une des IPv4 locales actives observées. Deux scénarios reproduits avant correction. |
| Un autre processus pouvait créer le dossier privé entre le contrôle d’absence et `mkdir`. Le helper s’arrêtait alors avec un retour non nul, sans diagnostic. | Échec inutile lors de l’initialisation concurrente. | Accepter seulement le dossier apparu puis revalider son type, propriétaire et mode root `0700`. Aucun chmod de réparation ; un dossier `0777` reste refusé. |
| Après retrait d’un marqueur finalisé, la garde retournait avant de rechercher d’autres journaux interrompus. | Une nouvelle écriture pouvait être autorisée malgré une autre transaction non résolue. | Réexécuter la garde historique avant d’autoriser l’écriture. Le nettoyage du seul marqueur terminé ne prouve jamais que les autres transactions ont abouti. |
| Une seconde entrée dans `operation()` sur la même instance échouait, mais son `finally` effaçait le descripteur suivi par l’opération extérieure. | Perte du contrôle de propriété du verrou extérieur avant publication. | Refuser la réentrée avant toute acquisition ou modification de l’instance ; le descripteur extérieur reste suivi et sa disparition reste détectée. |

Les cinq échecs attendus avant correction correspondent à quatre défauts logiques (la passerelle locale est testée dans deux positions). Le sixième cas, un dossier concurrent non privé, était déjà refusé : cette protection est conservée. Le scénario de concurrence sur `mkdir` est injecté de manière déterministe à la frontière de la commande ; ce n’est pas un benchmark de contention.

### Parties de code impactées

- `roles/bind9/files/bind-front-routes.sh` : `secure_dir()` pour l’initialisation et `observe()` pour la passerelle locale.
- `roles/bind9/module_utils/bind9_management.py` : `Manager.operation()` et `Manager.recovery_guard()` ; commentaire de durée de vie du verrou précisé : il est libéré lorsque tous les descripteurs propriétaires sont fermés.
- `tests/test_native_front_routes.py` et `tests/test_operation_resilience.py` : six scénarios ciblés ajoutés, exécutés avant/après sans affaiblir les assertions existantes.
- `README.md`, guides routes/management et rapport courant alignés. Le rapport de refonte précédent reste historique.

**Deux fichiers de production modifiés seulement.** Aucun fichier de production ajouté ou supprimé, aucune dépendance, variable publique, commande de playbook ou tag ajouté. La CI prend déjà les deux fichiers de tests concernés et conserve son seuil de couverture de 98 % ; son contenu reste inchangé.

La comparaison avec l’archive de base exige les mêmes octets pour tous les autres fichiers de rôle, tous les templates DNS, inventaires et playbooks, ainsi que le parcours de déploiement et d’hibernation. Les modifications n’altèrent pas les options DNS, les records, les ACL, les services, les règles SELinux ou firewalld. Les listes de clients et la politique DNSSEC restent celles de la base.

## Contrat d’exploitation maintenu

La route par défaut peut rester exclusivement sur **BACK**. La passerelle est recherchée sur le profil actif de l’interface portant l’IP **FRONT**, ou dans l’état actif de cette même interface ; aucune passerelle BACK/ADM/globale n’est utilisée comme repli. La découverte d’une passerelle dans une route déjà déclarée dans le même profil FRONT garde son périmètre antérieur. Les routes restent IPv4, additives et optionnelles, sans réactivation de profil. Une répétition sans différence reste sans changement.

Routes et management conservent leur exclusion de processus commune `operations.flock`. Le fichier vide peut rester présent ; sa présence ne désigne pas un propriétaire actif. Un dossier historique `.operation.lock` reste ignoré par défaut hors hibernation. Le sceau `.deployment.lock` garde sa fonction. Les journaux de reprise ne sont jamais effacés pour contourner un refus.

Un journal finalisé peut être nettoyé sans modifier le DNS ; toutes les autres transactions restent examinées avant écriture. Un journal incomplet ou dangereux reste bloquant. Read/list restent possibles lorsque les observations sont lisibles et sûres. Le management conserve les autres clauses et changements manuels, contrôle les checksums puis les identités avant publication, et refuse les créations sur un nom déjà présent. Une intervention root non coopérative conserve les limites de course décrites dans le [guide de management](MANAGEMENT.md).

Le verrou couvre les invocations de routes/management, pas tout le déploiement DNS historique ni une transaction distribuée entre les deux hôtes. Les déploiements complets et interventions manuelles nécessitent toujours une fenêtre exclusive. Une publication reste atomique par fichier, pas pour tous les fichiers face à un crash. Il n’est pas possible de promettre l’absence absolue d’incident.

## Contrôles et preuves de cette révision

Chaque exécution est reliée aux SHA-256 exacts des fichiers de production. `DELIVERY.json` conserve les résultats et empreintes des journaux de laboratoire ; ces journaux, caches, outils de validation et environnements ne sont pas inclus dans le ZIP.

| Statut | Contrôle | Résultat |
|---|---|---|
| **EXÉCUTÉ** | Régression sur l’archive source | Cinq échecs attendus, un cas dangereux déjà refusé ; quatre causes distinctes reproduites. |
| **EXÉCUTÉ** | Routes, garde et résilience ciblées | 166 tests sans échec. |
| **EXÉCUTÉ** | Suite de composants hors fichiers `*_ansible.py` et workflows DNSSEC/backup | 1037 tests sans échec. |
| **EXÉCUTÉ** | Transport Ansible routes, reprise, rôle déjà déployé, management unitaire/lot et mode historique optionnel | 44 tests sans échec. |
| **EXÉCUTÉ** | Syntaxe des deux playbooks sur les trois inventaires | Six contrôles ; 74 fichiers Python compilés, sept scripts Bash contrôlés individuellement. |
| **EXÉCUTÉ** | Pyflakes 3.2.0 et Bandit 1.7.10 sur les périmètres prévus par la CI | Zéro signalement. |
| **EXÉCUTÉ** | Couverture du seul cœur management | 98.15911 %, seuil 98 % maintenu ; 383 tests de cette mesure sans échec. |
| **EXÉCUTÉ** | NetworkManager EL8 et noyau réels, espace réseau privé | Onze parcours Ansible ; application, reprise, idempotence, métrique automatique ; défaut BACK préservé et FRONT sans défaut actif. |
| **EXÉCUTÉ** | BIND EL8 réel, DNSSEC désactivé puis auto | Douze transactions par mode : validation, RNDC, réponses UDP/TCP, archives réelles, lots mixtes et rollback ; SELinux doublé. |

Python **3.6.8**, Ansible **2.9.27** et Jinja **2.10.3** exécutent les suites de compatibilité. La mesure de couverture utilise Python **3.11.15** et coverage **7.16.2** ; elle ne mesure pas le Bash, le YAML, la paire ou la disponibilité système. Les avertissements de dépréciation des dépendances historiques restent visibles.

Les outils natifs testés sont **BIND 9.11.36-16.el8_10.16** et **NetworkManager 1.40.16-21.el8_10**, dans des conteneurs Rocky EL8 jetables. Cela ne qualifie pas un déploiement système RHEL complet. Aucun test applicatif n’a été retiré ou rendu moins strict, aucun seuil baissé. Les suites inchangées d’hibernation complète, DNSSEC et sauvegarde/namespaces ne sont pas réexécutées intégralement dans cette passe ; leurs résultats antérieurs restent historiques. Les points d’entrée de routes et les opérations ordinaires avec ancienne lease présente sont réexécutés.

## Utiliser et revenir en arrière

Sur le contrôleur, extraire le ZIP dans un répertoire distinct, vérifier son SHA-256 et `sha256sum -c SOURCE-MANIFEST.sha256`, conserver l’inventaire et les secrets existants hors de l’archive source. Aucun redéploiement DNS n’est nécessaire pour utiliser ce durcissement du tag `routes` ou du module management transféré temporairement.

Les commandes, lecture préalable, checksums par hôte et simulation restent inchangés : [routes](FRONT_ROUTES.md), [management](MANAGEMENT.md), [lots et listings](MANAGEMENT_LOTS_ET_LISTES.md). Commencer par la simulation et un seul hôte de recette. Un refus de passerelle locale exige de corriger le profil réseau par une procédure réseau autorisée ; le rôle ne le réécrit pas.

Les formats des verrous, marqueurs et journaux ne changent pas. Pour un retour au code précédent, suspendre les opérations, terminer ou réconcilier explicitement toute reprise en attente, conserver les preuves puis restaurer le répertoire du contrôleur depuis l’archive source indiquée en tête. Un retour de code ne retire aucune route committée et ne restaure pas les données DNS ; le code précédent réintroduit les quatre défauts décrits. Une restauration DNS conserve les préconditions du [README](../README.md#reprise) : sauvegarde vérifiée, arrêt de `named` et traitement des changements manuels. Ne pas supprimer un journal ni `operations.flock` pour forcer une exécution.

## Contrôles restant à exécuter

**Qualification production : NON TERMINÉE.** Les vérifications suivantes ne sont pas accessibles ici.

| Statut | Raison et prérequis | Procédure | Attendu et risque restant |
|---|---|---|---|
| **NON EXÉCUTÉ** | Aucune paire RHEL réelle accessible ; recette représentative, accès ADM de secours et inventaire réel requis. | Routes `--check -v`, application sur un hôte puis répétition ; contrôler `ip -4 route get` vers les réseaux cloud, défaut BACK et profils BACK/ADM. Lecture/checksum/simulation puis CRUD et lot sur la paire ; recetter UDP/TCP et SOA. | Routes ciblées, accès ADM conservé, DNS et réplication corrects ; écarts locaux de version/topologie ou SSH encore possibles. |
| **NON EXÉCUTÉ** | Services système et politiques réelles non reproduits ; RHEL de recette avec SELinux enforcing, systemd et firewalld requis. | Recette du [README](../README.md#validation), redémarrage un hôte à la fois et vérification des labels, services, flux, sceau et routes persistantes. | Persistance et accès corrects ; les doubles ne prouvent pas la politique réelle ou le démarrage système. |
| **NON EXÉCUTÉ** | Pas de panne matérielle ni de charge représentative injectée ; VM jetable, snapshots/export de sauvegardes et objectifs d’exploitation requis. | Coupure du contrôleur/hôte à plusieurs étapes, reprise ciblée, test de pression disque et de contention, mesure de latence DNS et RPO/RTO. | Refus explicite ou restauration contrôlée, preuves conservées ; durabilité du stockage, issue ambiguë et performances restent à qualifier. |
| **NON EXÉCUTÉ** | Pas de pipeline distante ni de matrice complète exécutée ; dépôt CI et runners Linux privilégiés requis. | Exécuter `.github/workflows/ci.yml`, dont hibernation complète, DNSSEC et backup/namespaces, avec les versions prévues, puis conserver les résultats. | Jobs applicables réussis et seuils maintenus ; la présence du workflow ne démontre pas son exécution distante. |

La [refonte précédente](REFONTE_ROUTES_MANAGEMENT.md) conserve les limites détaillées et son historique ; elle n’est pas comptée comme réexécutée sur ce code. Aucun audit externe, certification ou scan exhaustif de vulnérabilités n’est revendiqué.
