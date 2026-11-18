# Refonte routes et résilience du management

**Rapport historique de la livraison précédente :** les preuves de la révision courante sont dans [DURCISSEMENT_FINAL.md](DURCISSEMENT_FINAL.md).

Livraison **2026.10.19-routes-management.1**, issue exclusivement de `Bind9-Ansible-verrous-optionnels.zip`, SHA-256 `cb93c5f1eff87eccadc1fcc28eac6205f778e6f77e7a835cb15171a96f8420cb`. La base est la livraison dont le déploiement a été déclaré fonctionnel par l’utilisateur. Aucun serveur de production n’a été modifié par cette passe.

## Comportement attendu

La route par défaut du système reste sur **BACK**. La découverte suit **IP FRONT → interface qui la porte → profil NetworkManager actif → passerelle de ce profil ou de cette même interface**. FRONT peut ne porter aucune route par défaut active. Le moteur ne choisit jamais une passerelle BACK/ADM/globale comme repli. Une unique passerelle déclarée dans les routes du même profil FRONT reste utilisable si les deux propriétés de passerelle sont absentes. Une ambiguïté ou un conflit est refusé explicitement.

Le routage reste additif et optionnel : destinations IPv4 isolées/CIDR de `bind_cloud_clients`, aucune route pour `bind_internal_clients`, aucun remplacement de route existante ni activation globale du profil. Les clients directement connectés n’exigent pas de route via passerelle. L’union des CIDR est normalisée sans élargissement. Les routes contradictoires, politiques non standard, VRF et multipath sont refusés plutôt que réécrits.

## Impact et invariants

| Domaine | Changement et protection |
|---|---|
| Routes | Un point d’entrée YAML et un moteur Bash natif ; un seul adaptateur YAML pour reprise historique. Douze auxiliaires de routes supprimés. Aucun Python de routage, `jq` ou Galaxy ajouté. |
| Déploiement | Seuls les imports/actions de routes sont raccordés au nouveau moteur ; ordre DNS, sauvegarde, health checks et rescue conservés. Routes différées jusqu’à réussite DNS ; échec DNS retire les seuls ajouts possédés. |
| DNS | Templates, ACL, localhost, DNSSEC, TSIG, logs, SELinux et firewalld de la base conservés octet par octet. Inventaires et deux playbooks inchangés. |
| Exclusion | Routes et module management partagent `operations.flock`, fichier vide privé ; exclusion du noyau limitée au processus, jamais à la simple existence d’un chemin. Fin de processus/SIGKILL libère l’exclusion. |
| Management | CRUD/lots/listings, checksums et prévalidation existants conservés. Un pointeur durable refuse les nouvelles écritures après transaction incomplète ; read/list restent possibles si le journal est lisible. |
| Données | Lecture bornée/intégrale, état JSON UTF-8 sans clés dupliquées, refus liens/droits inattendus, contrôles d’identité, écritures atomiques par fichier, sauvegardes ciblées et préservation des modifications étrangères. |
| Compatibilité | RHEL 8 demandé, Ansible 2.9.27, Python cible 3.6.8. Pas de paquet ou bibliothèque Python de production ajouté ; `flock` fourni par `util-linux` est désormais utilisé par les routes. Outils requis explicités dans README. |
| Exploitation | Codes internes routes 0 inchangé, 2 ajouts prévus/effectués, 1 refus. Stdout vide d’une commande native accepté si son code et la relecture sont corrects. Messages contrôlés ; pas de configuration/secrets complets dans les erreurs. |
| CI | Tests de résilience ajoutés à la suite privilégiée, seuil management 98 % maintenu ; syntaxe de chaque script Bash vérifiée individuellement. Matrice distante non exécutée ici. |

Parties de production impactées :

- `roles/bind9/tasks/tasks.d/deploy/front_return_routes.yml` : orchestration unique et arguments explicites.
- `roles/bind9/files/bind-front-routes.sh` : observation, plan, ajout, contrôle, commit, rollback et reprise sous la même exclusion de processus.
- `roles/bind9/tasks/tasks.d/deploy/front_routes_legacy.yml` : ancien format lu/contrôlé ; suppressions et nettoyage exacts sous le verrou natif, sans suppression récursive.
- `roles/bind9/tasks/tasks.d/deploy/main.yml` : raccordement des quatre actions historiques au point d’entrée unique.
- `roles/bind9/library/bind9_manage.py` : exclusion sur chaque invocation, y compris lectures/simulations.
- `roles/bind9/module_utils/bind9_management.py` : exclusion, garde de reprise et lecture privée vérifiée ; aucun nouveau module de management.
- `roles/bind9/defaults/main.yml` : commentaire du réglage historique aligné ; valeurs publiques conservées.

Les chemins retirés, fichiers modifiés et empreintes exactes figurent dans `DELIVERY.json` et `SOURCE-MANIFEST.sha256`. Aucun fichier de test de la base n’est supprimé. Les tests de l’ancien plan sont raccordés au moteur effectif et aux validateurs de reprise encore utilisés.

## Concurrence, menaces et limites

Le verrou coopératif protège une invocation de routes ou du module management. Il ne couvre pas tout le déploiement DNS historique, les intervalles entre invocations Ansible ou les deux hôtes comme une transaction distribuée. `bind_operation_lock_enabled: false` reste le défaut et ignore l’ancien `.operation.lock` hors hibernation ; ce réglage ne désactive pas `operations.flock`. Le sceau `.deployment.lock` conserve le contrat d’hibernation. Une fenêtre exclusive reste obligatoire pour un déploiement complet ou une intervention manuelle.

Un utilisateur root non coopératif peut changer les fichiers entre deux contrôles. Les instantanés/checksums détectent les conflits observables ; ils ne constituent ni une protection contre root ni une signature. Les risques d’injection sont réduits par les arguments explicites/quotés, l’absence d’évaluation shell des données et les validations de grammaire ; chemins dangereux, sorties trop longues, états partiels et dérives sont refusés. Les observations natives sont bornées à 1 Mio et chaque commande à trente secondes. Au plus 512 destinations cloud sont acceptées par opération ; l’amélioration de performance n’a pas été mesurée sous charge.

Une configuration multi-fichiers n’est pas atomique face à un crash. Un journal durable permet de distinguer ajouts confirmés et issue ambiguë ; l’outil conserve les preuves plutôt que deviner. L’intégrité SHA-256 du journal de routes détecte une corruption accidentelle mais ne résiste pas à un opérateur root malveillant qui le réécrit. Un SIGKILL peut laisser un dossier temporaire privé `.route-work.*` ; son existence ne bloque pas une opération. Les journaux de sauvegarde ciblée n’ont pas de rétention automatique : surveiller disque et archiver les transactions finalisées selon votre politique. Plus de 4096 entrées de management impose l’archivage des preuves finalisées avant nouvelle écriture.

## Tests exécutés sur le code livré

Toutes les preuves d’exécution sont reliées aux SHA-256 des fichiers de production. Les journaux bruts restent dans le dossier local de recette ; l’archive distribuée contient leurs empreintes et résultats, sans logs de laboratoire.

| Statut | Contrôle | Résultat observé |
|---|---|---|
| **EXÉCUTÉ** | Suite générale Python/Ansible | 1055 tests sans échec, 0 ignoré. |
| **EXÉCUTÉ** | Parcours Ansible routes, management, lots et hibernation | 92 tests sans échec, 0 ignoré. |
| **EXÉCUTÉ** | Syntaxe et compilation | 6 contrôles de playbooks/inventaires, 74 sources Python compilées, 7 scripts Bash contrôlés. |
| **EXÉCUTÉ** | Analyse statique | Pyflakes 3.2.0 sur les sources Python/tests et Bandit 1.7.10 sur les modules prévus par la CI : zéro signalement. |
| **EXÉCUTÉ** | Routes natives EL8 | Onze parcours Ansible, dont FRONT sans défaut actif ; ajouts persistants/actifs, reprise, métrique automatique et idempotence. BACK/ADM/défaut préservés ; DNS en attente non appliqué. |
| **EXÉCUTÉ** | Management BIND natif, DNSSEC `no` | Douze transactions, deux lots mixtes, rollback commun, donnée DNS tardive invalide refusée, catalogues ; validations/rechargement/réponses DNS et archives réels, SELinux doublé. |
| **EXÉCUTÉ** | Management BIND natif, DNSSEC `auto` | Douze transactions, deux lots mixtes, rollback commun, donnée DNS tardive invalide refusée, catalogues ; validations/rechargement/réponses DNS et archives réels, SELinux doublé. |
| **EXÉCUTÉ** | Couverture du cœur management | 98.15425 %, 1517 instructions, 28 non couvertes ; seuil 98 % conservé. Python 3.11.15/coverage 7.16.2 uniquement pour cette mesure. |
| **EXÉCUTÉ** | Intégration CI et nettoyage | YAML CI analysé, tests de résilience raccordés, seuil 98 % maintenu ; deux fichiers YAML de routes, chaque script Bash contrôlé. |
| **EXÉCUTÉ** | Régression sur archive fonctionnelle antérieure | Échec attendu reproduit : une nouvelle écriture n’était pas refusée malgré un journal management non résolu. Le scénario réussit dans la suite courante. |

Les suites Python/Ansible emploient Python **3.6.8**, Ansible **2.9.27**, Jinja **2.10.3** et les CLI doubles pour l’injection déterministe des pannes. Les dépréciations `_yaml`/Jinja de ce runtime historique restent visibles ; elles ne sont pas des échecs de tâche. La couverture du seul cœur Python ne mesure pas le Bash, YAML, le noyau ou toute la disponibilité système.

Les essais natifs distincts utilisent **BIND 9.11.36-16.el8_10.16** et **NetworkManager 1.40.16-21.el8_10** dans des conteneurs Rocky EL8 jetables, avec noyau Linux réel et espace réseau séparé. Le garde de distribution RHEL n’est pas qualifié par un conteneur Rocky : les essais visent le moteur et les transactions, pas un déploiement système complet. Les 24 transactions management utilisent réellement `named-checkconf`, `named-checkzone`, RNDC, `dig` UDP/TCP et les archives ; SELinux reste doublé. Les onze parcours Ansible de routes couvrent plan, application différée, contrôle, reprise, idempotence, réactivation du profil de laboratoire, passerelle active et métrique automatique. BACK/ADM, défaut BACK et paramètres DNS en attente sont vérifiés. L’activation de profil ne figure que dans la fixture de recette, jamais dans le moteur de production.

La suite couvre aussi refus des données tronquées/corrompues, conflits, panne de mutation avec stdout vide, modification d’une route tierce, preuve supplémentaire dans un ancien journal conservée, reprise historique concurrente refusée, exclusion routes/management commune et libération après SIGKILL. Les assertions de rollback contrôlent les routes réelles de la fixture et l’absence du journal après succès ; elles ne reposent pas sur le libellé d’une tâche importée qu’Ansible n’affiche pas.

## Installer, utiliser et revenir en arrière

Extraire cette livraison dans un répertoire distinct sur le contrôleur, conserver la base précédente et ses secrets privés séparément, contrôler les SHA-256 puis reprendre le même inventaire. Aucun redéploiement BIND n’est nécessaire pour lancer le nouveau tag `routes`. Ne pas recopier les fichiers d’état ou secrets de test vers les serveurs. Les deux commandes de simulation/application et `routes-recover` figurent dans [FRONT_ROUTES.md](FRONT_ROUTES.md). Les requêtes CRUD/lots, lectures préalables, checksums par hôte et simulation sont inchangés : [MANAGEMENT.md](MANAGEMENT.md), [exemples de lots](MANAGEMENT_LOTS_ET_LISTES.md).

Un journal de routes courant est `front-routes.pending/`, un ancien est `front-routes.lock/plan.json`. La reprise vérifie l’état avant de retirer les seuls ajouts possédés. Ne pas supprimer un journal pour contourner un refus. Les routes committées ne sont pas retirées en supprimant un client de l’inventaire ou en restaurant BIND.

Le manager écrit `management.pending.json` avant publication et garde ses sauvegardes/manifestes ciblés. Un état incomplet demande la [procédure de reprise](MANAGEMENT.md#transaction-sécurité-et-reprise) ; une intervention manuelle n’est jamais effacée arbitrairement. La fin d’un processus libère seulement l’exclusion, pas les obligations de reprise. Les anciens journaux management restent contrôlés avant écriture.

Pour revenir au code de la base, suspendre les opérations, terminer ou réconcilier explicitement tout journal actuel avec cette livraison, recetter DNS/réseau et conserver les preuves avant de réutiliser l’archive précédente. La base ne connaît pas les nouveaux journaux : revenir au code ancien avec un état incomplet serait dangereux. Un retour de code ne nécessite pas de redéployer DNS ; il ne retire aucune route committée. Le rollback réseau automatique ne restaure que les ajouts confirmés de sa propre transaction. Une restauration BIND globale garde les préconditions d’arrêt et de sauvegarde du [README](../README.md#reprise).

## Contrôles restant à exécuter

**Qualification opérationnelle de votre paire RHEL : NON TERMINÉE.** Aucune garantie d’absence absolue d’incident n’est annoncée.

| Statut | Raison et prérequis | Procédure | Attendu et risque restant |
|---|---|---|---|
| **NON EXÉCUTÉ** | Paire RHEL réelle inaccessible ; recette représentative, accès ADM de secours et inventaire réel requis. | Relever `rpm -q bind NetworkManager iproute util-linux` ; lancer routes `--check`, appliquer sur un hôte puis répéter, vérifier `ip -4 route get` vers chaque segment cloud et défaut via BACK. Redémarrer uniquement cet hôte de recette puis vérifier persistance. | Ajouts ciblés FRONT, défaut BACK et ADM inchangés, répétition sans changement. Versions/paramètres matériels et boot restent à qualifier. |
| **NON EXÉCUTÉ** | Services système réels non disponibles dans les conteneurs ; paire de recette avec SELinux enforcing, systemd et firewalld requis. | Recette complète du [README](../README.md#validation), contrôles `named-checkconf -z`, `restorecon`, UDP/TCP, refus AXFR, SOA/réplication et règles permanentes firewalld ; hibernation puis refus d’un redéploiement. | Configuration/labels/services/flux corrects. La simulation des services ne prouve pas leur fonctionnement réel. |
| **NON EXÉCUTÉ** | Pas d’injection de panne physique ; VM de recette, snapshots/export de sauvegardes et opérateur habilité requis. | Couper le contrôleur puis l’hôte à plusieurs étapes, inspecter journal/progression, reprendre selon les guides sans écraser une édition manuelle ; mesurer RPO/RTO. | Pas de vol d’exclusion, restauration ciblée démontrée ou refus avec preuves conservées. Durabilité stockage/issue ambiguë restent à qualifier. |
| **NON EXÉCUTÉ** | Pas de banc représentatif de charge ; volumétrie, objectifs de latence et collecte de métriques requis. | Mesurer temps de plan/commit, DNS UDP/TCP, saturation disque et rotation avec charge représentative et espace disque contraint. | Seuils d’exploitation définis et tenus ; aucune optimisation de débit ni rétention temporelle des logs n’est démontrée ici. |
| **NON EXÉCUTÉ** | Pas de pipeline distante exécutée ni de qualification des autres combinaisons Ansible ; dépôt CI et runners Linux privilégiés requis. | Exécuter `.github/workflows/ci.yml`, dont workflows DNSSEC, sauvegarde/namespaces et matrice complète ; conserver résultats et artefacts. | Chaque job applicable réussi, seuils maintenus. Présence d’un workflow et parsing local ne prouvent pas son exécution distante. |
| **NON EXÉCUTÉ** | Lanceur Linux `unshare` livré non exécuté dans cet hôte Windows ; root et namespaces Linux imbriqués requis. | `sudo --preserve-env=PATH bash tests/run_integration_front_routes.sh` dans un laboratoire jetable muni de NM, D-Bus, Ansible et Python compatibles. | Même scénario natif reproductible ; cette passe a exécuté son moteur Python dans un conteneur à espace réseau privé avec un lanceur extérieur, pas ce lanceur `unshare` complet. |

Les vérifications sont proportionnées au changement ; aucun audit externe, certification ou scan exhaustif de vulnérabilités n’a été effectué. Les dépendances de production ne sont pas mises à niveau. Une absence de changement à un composant ne constitue pas sa qualification générale.

## Références techniques

L’exclusion temporaire s’appuie sur la sémantique Linux de [flock(2)](https://man7.org/linux/man-pages/man2/flock.2.html) et [flock(1)](https://www.man7.org/linux/man-pages/man1/flock.1.html). Les propriétés sont celles des profils [NetworkManager](https://www.networkmanager.dev/docs/api/latest/nm-settings-nmcli.html) ; les comportements réellement exécutés sont ceux de la version EL8 citée ci-dessus. Les guides historiques restent datés et ne remplacent pas ce rapport courant.
