# Vérification, robustesse, résilience et performance

**Rapport historique de la révision précédente.** Pour les octets et contrôles courants, voir [VERIFICATION_SYNTAXE_SCRIPTS.md](VERIFICATION_SYNTAXE_SCRIPTS.md).

Révision **2026.10.19-review.1**. Source : `Bind9-Ansible-durci.zip`, SHA-256 `aa56562f0dfe58bbf81a936ae0275c6f5d78bbbaa2906e182156d194007f98e8`. La source est conservée intacte. Aucun serveur de votre environnement n’a été modifié.

## Défauts reproduits et corrections

| Constat sur la source | Risque et périmètre | Correction et preuve |
|---|---|---|
| `sort` renvoie `2` pendant l’observation ou la vérification après ajout. | Ce code était aussi interprété comme un changement réussi ; après ajout, le rollback n’était pas tenté. | Réserver `2` à une sortie volontaire. Tout autre `2` devient un échec et passe par la reprise conditionnelle. Deux injections reproduites avant correction. |
| Un tri de clients émet une ligne puis échoue dans une substitution de processus. | La boucle ne reçoit pas son échec et peut écrire un plan partiel avant le refus final. | Capturer et contrôler le résultat complet du tri, borné par les 512 clients, avant toute écriture. Le test exige zéro mutation sur sortie partielle. |
| Le nettoyage temporaire échoue avec `2` après commit. | Un problème de nettoyage était masqué par un code de changement réussi. | Signaler l’échec, conserver le répertoire de diagnostic. Le commit déjà achevé et ses routes ne sont pas annulés pour ce seul incident de nettoyage. |
| Un listing de 25 records produit deux catalogues complets. | Allocations et formatage inutiles proportionnels au nombre total d’enregistrements. | Projeter la page après tri. Test de budget de 4 Mio pour la seule projection de 10 005 records ; avant correction, plus de 5,1 Mo alloués. |
| Le README désigne encore l’ancien ZIP d’hibernation pour vérifier la livraison courante. | Commande d’exploitation incohérente avec l’archive reçue. | Nom de ZIP, source, révision, rapport et périmètre de couverture réalignés ; garde de packaging sur la commande de vérification. |

Les **cinq scénarios automatisés** échouent sur les octets de production de la dernière archive, puis réussissent dans la suite ciblée sur les octets corrigés. Aucun test existant n’a été supprimé ou assoupli. Les injections de panne sont des doubles contrôlés aux frontières des commandes ; elles ne simulent pas une panne physique du stockage.

### Matrice d’impact et invariants

| Domaine | Impact réel et protections conservées |
|---|---|
| Routes | `roles/bind9/files/bind-front-routes.sh` : `changed_result()`, tri dans `collapse_clients()`, `finish()`/`cleanup()` et installation des traps avant les prérequis. Ajouts/observations/profil/passerelle inchangés sur le parcours sain. |
| Management | `roles/bind9/module_utils/bind9_management.py` : seul `Manager.listing()` change. Ordre, champs, pagination, catalogues publics sans valeurs et checksum restent identiques. |
| Concurrence et intégrité | Exclusion commune de processus, gardes de reprise, contrôles d’identités et de checksums, publications par fichier et refus create-existant conservés. Aucun cache de configuration live ajouté. |
| DNS, déploiement, sécurité | Templates, inventaires, playbooks et tous les autres fichiers du rôle identiques à la source. Écoutes, DNSSEC, TSIG, logs, SELinux, firewalld et hibernation inchangés. |
| Tests et CI | `tests/test_native_front_routes.py` et `tests/test_management_list.py` étendus. Le workflow existant exécute déjà ces fichiers ; son contenu et le seuil de 98 % sont conservés. |
| Configuration et dépendances | Aucune variable, aucun tag, format de journal, prérequis produit ou fichier d’exécution ajouté. Le helper routes reste natif, sans Python de routage. |

La passerelle est toujours celle du profil actif/interface portant l’IP FRONT. La route par défaut peut rester exclusivement sur BACK. Une erreur persistante pendant le rollback conserve le journal et arrête l’opération ; elle n’autorise jamais une suppression aveugle. Une perte d’alimentation entre commande et acquittement peut rester ambiguë et nécessiter une inspection opérateur.

## Mesures de performance — EXÉCUTÉ

Python **3.6.8**, mêmes données synthétiques et mêmes résultats avant/après. Une page de **25 records** est demandée dans une zone de 10 005 puis 50 005 records, majoritairement des records A ; le graphe du laboratoire comporte trois fichiers statiques et deux zones. Cinq échantillons chronométrés pour la projection, trois pour le parcours complet ; un appel tracé séparément mesure le pic des allocations Python avec `tracemalloc`. Les premiers appels chauffent les chemins. Les offsets 0, 25, une fin de zone et une page vide sont comparés ; données, ordre, champs et checksums complets restent identiques.

### Projection de la page après analyse

| Records | Pic avant (Mio) | Pic après (Mio) | Réduction | Médiane avant (ms) | Médiane après (ms) |
|---|---|---|---|---|---|
| 10005 | 4.886 | 0.851 | 82.59 % | 17.263 | 4.616 |
| 50005 | 23.805 | 4.246 | 82.16 % | 84.240 | 26.750 |

Ce gain concerne la projection seule : le test fournit un document déjà analysé pour isoler ce coût. Ce n’est pas une réduction de même ampleur de toute la mémoire du module.

### Parcours complet de lecture/listing

| Records | Pic avant (Mio) | Pic après (Mio) | Médiane avant (s) | Médiane après (s) |
|---|---|---|---|---|
| 10005 | 16.284 | 16.284 | 0.1848 | 0.1739 |
| 50005 | 69.666 | 50.107 | 1.1112 | 1.0471 |

Le graphe et le fichier de zone sont intégralement relus, analysés et contrôlés. Le pic peut donc rester dominé par l’analyse : à 10 005 records il est identique avant/après. La complexité du tri reste O(n log n), la mémoire totale O(n), et seuls les résultats détaillés/catalogues sont limités à la page (500 lignes au maximum). Aucun contrôle d’intégrité n’est supprimé pour accélérer le listing.

### Plans de routes sans mutation

| Clients non agrégés | Médiane avant (s) | Médiane après (s) |
|---|---|---|
| 1 | 0.4507 | 0.4609 |
| 128 | 0.8183 | 0.8725 |
| 512 | 1.7928 | 1.9389 |

Trois échantillons par version et volume ; moteur Bash réel, commandes réseau doublées, plans identiques, zéro mutation. Chaque plan part de deux routes noyau (défaut BACK et réseau connecté FRONT), sans route persistante supplémentaire. Ce n’est pas une mesure sur une table de routage volumineuse. Ces séries montrent un léger surcoût observé sur ce laboratoire ; elles ne démontrent aucun gain de latence réseau. D’autres validations étaient en cours, les versions ont été mesurées successivement et l’échantillon est réduit.

Une seconde série utilise **huit échantillons par version et volume**, après chauffe, en alternant l’ordre avant/après puis après/avant :

| Clients | Médiane avant (s) | Médiane après (s) | Écart observé |
|---|---|---|---|
| 1 | 0.4382 | 0.4450 | +1.55 % |
| 128 | 0.7975 | 0.7721 | -3.19 % |
| 512 | 1.7447 | 1.7324 | -0.70 % |

Les plans restent identiques, sans mutation. Les plages d’échantillons avant/après se chevauchent pour les trois volumes ; cette seconde série ne reproduit pas un surcoût stable. Cette observation ne prouve pas une équivalence statistique ni un gain de performance. Ces temps incluent les doubles Python des commandes réseau ; ils ne mesurent pas la latence de NetworkManager réel et ne constituent pas un benchmark DNS. Aucun engagement de débit/latence en production n’en découle. Le coût d’application de 512 destinations via NetworkManager, les transferts DNS et la charge des clients restent à mesurer en recette. Le tri des clients reste unique et les bornes restent 512 entrées, 1 Mio d’observation et 30 secondes par commande.

Pour rejouer le garde mémoire dans un **laboratoire Linux root jetable**, avec Python et dépendances CI : `python -m pytest -q tests/test_management_list.py -k bounded_projection_memory`. Le budget couvre la projection isolée, pas le processus entier. Les scripts de mesures de cette passe et leurs journaux sont conservés hors archive ; ils ne deviennent pas des composants des serveurs DNS.

## Validation courante

Chaque exécution est reliée aux SHA-256 exacts des fichiers de production. `DELIVERY.json` contient les résultats, mesures et empreintes des journaux. Les preuves de la révision précédente sont historiques.

| Statut | Contrôle | Résultat |
|---|---|---|
| **EXÉCUTÉ** | Nouveaux scénarios sur l’archive d’origine | Cinq échecs attendus ; refus incomplet, succès erronés et dépassement de budget reproduits. |
| **EXÉCUTÉ** | Routes et listings ciblés | 134 tests sans échec, dont rollback après erreur `2` et nettoyage après commit. |
| **EXÉCUTÉ** | Composants hors fichiers `*_ansible.py` et workflows DNSSEC/backup | 1042 tests sans échec. |
| **EXÉCUTÉ** | Transport Ansible routes, reprise, rôle déjà déployé, management unitaire/lot et ancienne lease | 44 tests sans échec. |
| **EXÉCUTÉ** | Compatibilité et syntaxe | Python 3.6.8, Ansible 2.9.27, Jinja 2.10.3 ; six syntaxes de playbook/inventaire, 74 fichiers Python et sept Bash. |
| **EXÉCUTÉ** | Analyses statiques prévues par la CI | Pyflakes 3.2.0 et Bandit 1.7.10 : zéro signalement. |
| **EXÉCUTÉ** | Couverture du seul cœur management | 98.16393 % ; 384 tests, seuil 98 % maintenu, mesure Python 3.11.15/coverage 7.16.2. |
| **EXÉCUTÉ** | NetworkManager EL8 et noyau réels, réseau privé | Onze parcours : plan/application/reprise/idempotence, passerelle FRONT sans défaut actif, défaut BACK et ADM préservés. |
| **EXÉCUTÉ** | BIND EL8 réel | Douze transactions par mode DNSSEC `no`/`auto` : DNS UDP/TCP, RNDC, archives, lots mixtes et rollback. SELinux doublé. |
| **EXÉCUTÉ** | Comparaison et packaging | Deux fichiers de production modifiés seulement, autres méthodes Python et fonctions natives contrôlées, aucun retrait. ZIP déterministe, CRC, chemins réguliers sûrs et manifeste SHA-256 contrôlés. |

Les outils natifs sont **BIND 9.11.36-16.el8_10.16** et **NetworkManager 1.40.16-21.el8_10**, dans des conteneurs Rocky EL8. Ce n’est pas une qualification système RHEL. Les avertissements des dépendances historiques sont conservés. Les suites inchangées d’hibernation complète, DNSSEC et backup/namespaces ne sont pas intégralement réexécutées ; le transport et les gardes ordinaires/hibernation sélectionnés le sont. La première tentative du banc de mesure a échoué sur le répertoire temporaire Ansible du laboratoire en lecture seule ; ce réglage du banc a été corrigé, sans changement produit, avant les mesures présentées.

## Exploitation et retour arrière

Vérifier `sha256sum -c Bind9-Ansible-verifie.zip.sha256`, extraire dans un nouveau répertoire du contrôleur, puis `sha256sum -c SOURCE-MANIFEST.sha256`. Conserver inventaire et secrets opérationnels hors source. Aucune reconstruction DNS n’est nécessaire pour utiliser le tag `routes` ou le module de management corrigés. Commencer sur un seul hôte de recette, par la simulation, puis vérifier état réseau/DNS et idempotence. Commandes : [routes](FRONT_ROUTES.md), [management](MANAGEMENT.md), [lots/listings](MANAGEMENT_LOTS_ET_LISTES.md).

Un refus avant mutation ne demande pas de restauration réseau. Un échec après mutation tente la reprise conditionnelle ; si elle refuse, conserver les journaux et suivre le runbook. Un échec de nettoyage seul après commit conserve les routes et signale le répertoire temporaire à inspecter. Ne jamais effacer `operations.flock` ou un journal pour forcer une exécution.

Pour revenir au code source précédent, arrêter les opérations concurrentes, terminer ou réconcilier toute reprise en attente, conserver les preuves puis restaurer le répertoire du contrôleur depuis l’archive source indiquée en tête. Les formats ne changent pas ; ce retour réintroduit les défauts décrits. Il ne retire aucune route committée ni ne restaure les données DNS. Une restauration DNS exige les sauvegardes et préconditions du [README](../README.md#reprise).

La configuration live reste la source de vérité. L’exclusion protège routes/management coopératifs, pas une édition root non coopérative, tout le déploiement DNS ou une transaction distribuée sur la paire. Publication atomique par fichier, pas atomicité globale face à une panne. Aucune garantie d’absence absolue d’incident n’est revendiquée.

## Contrôles restant à exécuter

**Qualification production : NON TERMINÉE.**

| Statut | Raison et prérequis | Procédure | Attendu et risque restant |
|---|---|---|---|
| **NON EXÉCUTÉ** | Pas d’accès à votre paire RHEL ; recette représentative et accès ADM de secours requis. | Simulation puis routes sur un seul hôte, répétition et `ip -4 route get` ; CRUD/lot après lecture et simulation, DNS UDP/TCP et SOA sur la paire. | Ajouts ciblés, défaut BACK/ADM conservés, DNS/réplication corrects ; écarts de versions, topologie et SSH possibles. |
| **NON EXÉCUTÉ** | Services/politiques réels non reproduits ; VM RHEL avec SELinux enforcing, systemd et firewalld requise. | Recette du [README](../README.md#validation), labels, flux, services, redémarrage d’un hôte à la fois et persistance des routes. | Droits/persistance corrects ; les doubles ne prouvent ni politique réelle ni démarrage système. |
| **NON EXÉCUTÉ** | Pas de panne matérielle ni de charge représentative ; VM jetable, snapshots, sauvegardes et objectifs d’exploitation requis. | Coupures à plusieurs phases, contention, pression disque, réconciliation et restauration ; appliquer les volumes réels via NetworkManager et mesurer latences DNS, durées de maintenance et RPO/RTO. | Refus ou reprise contrôlée et preuves conservées ; durabilité, contention et performance/SLO restent à qualifier. |
| **NON EXÉCUTÉ** | Pas de CI distante ni de matrice complète ; dépôt et runners Linux privilégiés requis. | Exécuter `.github/workflows/ci.yml`, dont hibernation complète, DNSSEC et backup/namespaces, avec les versions annoncées. | Jobs applicables réussis, seuils inchangés ; un workflow présent ne prouve pas son exécution. |

Le [rapport précédent](DURCISSEMENT_FINAL.md) conserve ses preuves et limites détaillées. Aucun audit externe, certification ni scan exhaustif de vulnérabilités n’est revendiqué.
