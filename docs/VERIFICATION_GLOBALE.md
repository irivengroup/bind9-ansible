# Vérification globale et nettoyage — 15 octobre 2026

> Historique de la livraison source, conservé pour ses constats et corrections. Le routage a depuis été réimplémenté en tâches natives ; ses mesures actuelles sont dans [VALIDATION_ROUTES_NATIVES.md](VALIDATION_ROUTES_NATIVES.md). Les nombres de tests, fichiers et couverture ci-dessous décrivent uniquement cette livraison antérieure.

Cette passe porte sur **Bind9-Ansible**, à partir de l’archive locale `Bind9-Ansible-consolide.zip`. Les sources de référence et anciennes archives sont conservées. Le projet, le playbook `playbooks/bind9-deploy.yml`, les variables publiques d’inventaire, les zones natives, DNSSEC auto, IPv4 et le comportement NXDOMAIN restent en place.

## Nettoyage demandé

| Élément | Constat vérifié et action |
|---|---|
| Agrégat FRONT | Déclaré sans consommateur ; suppression du template et des deux exemples générés |
| FRONT du pair distant | Déclaré sans consommateur sur chaque serveur ; génération uniquement de `bind_primary_front_ip` sur le primaire ou `bind_secondary_front_ip` sur le secondaire |
| BACK | Les deux ACL individuelles et `bind_servers_back_ip` sont effectivement référencées ; conservation pour les pairs, permissions de requête et exclusions de récursion |
| Variables Ansible | Dix valeurs par défaut consommées ; aucune affectation ou variable enregistrée sans référence détectée dans le périmètre rôles/inventaires/playbook. Aucun retrait de variable d’inventaire justifié |
| Tâches, fichiers et code | 23 fichiers de tâches accessibles depuis l’entrée du rôle, 11 assets déployés référencés, un handler notifié et 21 filtres exportés utilisés ; aucun fichier de production orphelin identifié. Pyflakes ne signale aucun import inutilisé ni nom indéfini |
| Fonctions optionnelles | Routes, reprise, migration et supervision restent appelables ; leur désactivation par défaut ne constitue pas du code mort |
| Livraison | Exclusion des caches, journaux, runtimes, clés générées et preuves temporaires ; manifeste, métadonnées et exemples régénérés |

La comparaison avant/après sur les deux rôles identifie deux ACL inutilisées dans chaque ancien rendu et aucune après nettoyage. Les tests `test_server_acls.py` protègent ce résultat ; `test_source_reachability.py` vérifie les affectations, références, includes, assets, handlers et filtres. Ce sont des contrôles statiques adaptés aux références explicites du projet, pas une preuve générale sur toute réflexion Ansible ou tout consommateur externe. Aucune suppression de fonctionnalité ou de contrat d’inventaire n’est déduite de l’absence d’usage dans un exemple.

## Corrections de la passe globale

| Défaut reproduit | Correction et preuve |
|---|---|
| Politique DNSSEC lue avant l’attente du verrou | Relecture après acquisition ; un test remplace la politique pendant l’attente et exige l’application de la nouvelle |
| Renommage atomique du journal sans synchronisation du répertoire | Synchronisation du fichier puis du répertoire avant toute mutation RNDC ; le test contrôle cet ordre, sans prétendre simuler une coupure électrique |
| Rechargement seul après modification de la commande systemd | Enregistrement du changement de l’override ; restart lorsqu’il change, reload sinon. Le test vérifie les trois cas changement/absence de changement/variable absente |
| Restauration pendant l’activité de named et du renouvellement | Suspension du timer/service présents et arrêt de named avant extraction ; redémarrage après restauration puis reprise du timer précédemment actif. Le test vérifie l’ordre déclaré dans le vrai bloc rescue |

Les quatre tests de `test_global_review.py` ont d’abord échoué sur l’état antérieur, puis réussi après correction. Les doubles Ansible exercent les workflows de routes et leur rescue ; ils substituent les opérations de service/restauration. L’activation réelle des unités et le rollback système sur RHEL restent **NON EXÉCUTÉ**.

## Résultats et reproduction

**EXÉCUTÉ** : suite de 251 tests ; syntaxe Ansible 2.9.27 sur les trois inventaires ; préflight des 12 hôtes avec faits simulés ; compilation/exécution isolée sous Python 3.6.8 ; Pyflakes, Bandit et syntaxe shell. Couverture ciblée : cinq modules, 555 instructions, trois non couvertes, 99 % arrondi, seuil 98 % conservé.

**EXÉCUTÉ** : BIND natif EL8 9.11.36 et BIND 9.18.24, chacun 44 configurations chargées et 131 contrôles DNS réels. Les essais incluent zones natives inchangées, UDP/TCP, refus d’accès/transfert, réplication, forwarding et DNSSEC cryptographique avec une ancre temporaire de laboratoire. Les deux exemples passent `named-checkconf -z` EL8. NetworkManager 1.46 : quatre workflows réels, idempotence, reprise sélective, persistance et préservation des routes initiales.

```bash
# Depuis la racine du projet, environnement Linux de recette avec Ansible 2.9.27.
sudo python -m pytest -q tests
python -m pyflakes roles/bind9/filter_plugins roles/bind9/files tests
python tests/integration_preflight.py
```

Les dépendances QA et commandes DNS/NM détaillées figurent dans [VALIDATION_RHEL8.md](VALIDATION_RHEL8.md). `DELIVERY.json` donne le périmètre exact des différences et contrôles ; `SOURCE-MANIFEST.sha256` donne les empreintes des fichiers, le ZIP possède sa somme externe.

## Limites opérationnelles

La qualification de production reste **NON TERMINÉE**. **NON EXÉCUTÉ** : déploiement sur une paire RHEL 8 réelle, modules DNF/Python cibles, SELinux et politique cryptographique installée/FIPS, réseau/SSH réels, activation des unités et redémarrage serveur, renouvellement sur sept jours, RFC5011/chaînes des forwarders réels, double déploiement complet, restauration système, charge et CI distante. Les raisons, prérequis, procédures, résultats attendus et risques sont détaillés dans [VALIDATION_RHEL8.md](VALIDATION_RHEL8.md).

Un changement d’override peut interrompre brièvement le service. La sauvegarde BIND couvre ses fichiers de configuration/données ; elle ne sauvegarde pas le helper, les unités ni le journal var/lib et ne constitue pas un retour complet de version. Un ancien named.conf sans canal RNDC peut empêcher le hook actuel de démarrer. La sauvegarde à chaud n’offre pas un instantané atomique des journaux d’un secondaire. Qualifier ces cas avant production avec [OPERATIONS.md](OPERATIONS.md). Aucun changement n’a été appliqué à vos serveurs.
