# Vérification et nettoyage — 14 octobre 2026

Cette passe porte sur **Bind9-Ansible**, à partir de `Bind9-Ansible-routes-named.zip` (SHA-256 `16ced90e797da8bd3d37428243b00f23e256de3ebed2bcf13cbb4067f4401ffb`). Cette archive précédente reste intacte. Le ZIP initial de l'utilisateur sur D: n'est plus accessible dans cette session ; la provenance immédiate est cette livraison cumulative inspectée, pas une nouvelle lecture du ZIP initial.

Le playbook reste `playbooks/bind9-deploy.yml`. Aucun nouveau comportement réseau n'est introduit. La qualification de production reste **NON TERMINÉE**, car la recette sur les serveurs RHEL 8 / Python 3.6.8 n'a pas été exécutée.

## Suppressions et correction

| Élément | Motif et comportement préservé |
|---|---|
| `requirements.yml` | Liste de collections vide, aucune collection externe ni référence à ce fichier ; aucune installation Galaxy nécessaire |
| `docs/FIND_COMPATIBILITY.md` | Rapport historique devenu redondant ; le code omet toujours `read_whole_file` et `test_find_compatibility.py` conserve la non-régression Ansible 2.9 |
| `docs/VALIDATION_LOOPBACK.md` | Ancien relevé de tests remplacé par les rapports DNS, routes et nettoyage actuels ; les fichiers natifs et les tests loopback restent conservés |
| Défaut `bind_migration_state_dir` | Variable jamais consommée, sans effet sur l'exécution ; sa ligne de README est retirée |
| Handler `Restart named` | Aucun `notify` ne le déclenchait ; le handler de rechargement utilisé et le redémarrage explicite du rollback restent présents |
| Attribut `DnsTopology.authoritative` | Jamais lu ; les noms autoritatifs dérivés et la validation restent présents |
| Imports Python inutilisés | Quatre imports retirés dans deux modules de tests ; analyse Pyflakes ajoutée à la CI |
| Variables Bash `status` et `force` | Valeurs jamais consommées ; `--force` reste accepté pour les appels existants et chaque sauvegarde reste unique ; la classification AXFR dépend toujours de la réponse DNS |
| Ancien chemin `/etc/named` dans `restorecon` | La configuration canonique ne crée pas ce répertoire. La commande restaure les labels de `/etc/named.conf` et `/var/named`, disponibles dans le flux réel, sans exiger l'ancien layout |

La non-régression de `restorecon` exécute les anciens puis les nouveaux arguments contre un dossier temporaire contenant seulement le layout canonique. Le double de commande refuse les chemins absents : ancien code **1**, nouveau code **0**. Le test livré reproduit le défaut ; il ne prouve pas la politique SELinux du serveur.

Les tâches qui retirent les anciens fragments/exécutables restent nécessaires pour les mises à niveau et sont conservées. Les sauvegardes, le journal de routes, la reprise explicite, les tests et les inventaires sont également conservés. Les caches Python/pytest, journaux, environnements de test et secrets générés sont exclus du nouveau ZIP. Les anciennes livraisons ne sont pas supprimées.

## Impact et risques

| Domaine | Impact et invariant |
|---|---|
| Configuration | Template DNS et fichiers natifs inchangés ; aucune modification des ACL, des vues, de DNSSEC ou des zones |
| Réseau | Code d'ajout/retrait des routes inchangé ; NetworkManager préconfiguré, ajouts seuls, passerelle existante, aucune activation/reconfiguration du profil par le rôle |
| Compatibilité | Ansible 2.9 conservé ; arguments publics du configtool conservés ; suppression d'un défaut sans effet et de fichiers sans consommateur |
| Sécurité | Retrait d'un chemin obsolète ; labels de la configuration contenant TSIG et des données DNS toujours contrôlés. Aucun secret ajouté |
| Exploitation / rollback | Test supplémentaire : un échec du handler de rechargement rejoint le rescue et retire les routes de l'exécution, tout en conservant celles qui existaient déjà ; restauration BIND remplacée par un double dans cet essai |
| Livraison / reproductibilité | Comparaison au ZIP source, différences autorisées explicites, manifeste SHA-256, CRC, liens locaux Markdown et reproduction binaire du ZIP contrôlés au packaging |

Les menaces concernées par cette passe sont une suppression accidentelle d'un point d'entrée, une régression du rollback et une inclusion de secrets/caches dans la livraison. Les références des sources, les scénarios de panne et le contrôle du manifeste ciblent ces risques. Le laboratoire ne remplace pas les essais de perte SSH, redémarrage ou restauration réelle.

## Contrôles exécutés sur la copie nettoyée

| Contrôle | Statut | Résultat |
|---|---|---|
| Références et YAML | **EXÉCUTÉ** | 9 assets, 21 fichiers de tâches, 1 handler notifié, 9 defaults et 21 filtres consommés ; 48 YAML analysés, imports de tâches résolus |
| Pyflakes 3.2.0 | **EXÉCUTÉ** | Filtres, scripts Python et tests : aucune erreur signalée, aucun import inutilisé signalé |
| Défaut du layout SELinux | **EXÉCUTÉ** | Reproduction ancien/nouveau : codes 1/0 dans le double de commande ; aucune modification de /etc dans le laboratoire |
| Suite pytest complète | **EXÉCUTÉ** | 207 tests réussis, aucun scénario ignoré ; Python 3.8.20 / Ansible 2.9.27 ; 337,43 secondes |
| Syntaxe et compilation | **EXÉCUTÉ** | Syntaxe Ansible sur dev/preprod/prod, syntaxe Bash des deux outils et deux lanceurs, compilation Python ; codes de sortie 0 |
| Couverture ciblée | **EXÉCUTÉ** | 126 tests ; 392 instructions, 2 non couvertes, 99 %, seuil 98 % respecté sur front_routes.py, dns_topology.py et soa_health.py ; aucune mesure globale annoncée |
| Bandit 1.7.10 | **EXÉCUTÉ** | Quatre fichiers Python de production analysés ; aucun résultat signalé, aucune règle désactivée |
| Laboratoire DNS BIND 9.18.24 | **EXÉCUTÉ** | 44 configurations chargées, 107 contrôles réels UDP/TCP, fichiers natifs EL8 inchangés |
| Laboratoire NetworkManager 1.46.0 | **EXÉCUTÉ** | Quatre workflows Ansible : ajout, répétition sans changement, reprise sélective, nouvel ajout ; persistance après reload/down/up du profil de laboratoire ; routes/profils BACK/ADM/défauts et DNS pending préservés |
| Préflight des inventaires | **EXÉCUTÉ** | Trois inventaires, 12 hôtes, vraies assertions avec faits RHEL simulés ; aucune modification cible |
| Packaging | **EXÉCUTÉ** | 101 fichiers ; 14 sources modifiées, 1 rapport ajouté, 3 fichiers obsolètes retirés, 84 sources inchangées et 2 métadonnées régénérées ; manifeste/CRC/liens Markdown vérifiés, deux constructions binaires identiques dans le même runtime |

Paramètres de l'archive : fichiers triés, DEFLATE niveau 9, date ZIP fixe `2026-10-14 00:00:00`, modes Unix 0644 et 0755 pour les exécutables. La reproduction a été observée avec le même Python/zlib ; aucune identité binaire entre versions de compresseur différentes n'est présumée. Le manifeste exclut sa propre empreinte et l'empreinte externe du ZIP porte sur l'archive complète.

Les détails des scénarios DNS et réseau, leurs prérequis et leurs procédures de reproduction figurent dans [VALIDATION_NAMED.md](VALIDATION_NAMED.md) et [VALIDATION_FRONT_ROUTES.md](VALIDATION_FRONT_ROUTES.md).

Depuis la racine du projet sur un contrôleur Linux de recette :

```bash
python -m pytest -q tests
python -m pyflakes roles/bind9/filter_plugins roles/bind9/files tests
python tests/integration_preflight.py
ansible-playbook -i inventory/dev/hosts.ini playbooks/bind9-deploy.yml --syntax-check
bash -n roles/bind9/files/bind-configtool.sh roles/bind9/files/named-healthcheck.sh tests/run_integration_loopback.sh tests/run_integration_front_routes.sh
```

Pyflakes est une dépendance de vérification, pas un prérequis de déploiement. La CI l'épingle à 3.2.0 ; la suite locale emploie Python contrôleur 3.8.20 et Ansible 2.9.27. Les fixtures et runtimes de laboratoire ne sont pas incorporés à l'archive.

## Vérifications restantes

| Contrôle | Statut / raison | Prérequis, procédure et résultat attendu | Risque restant |
|---|---|---|---|
| RHEL 8, Python 3.6.8 et SELinux réels | **NON EXÉCUTÉ** : cibles inaccessibles | Paire de recette avec SSH/sudo et dépôts RHEL ; relever les versions, exécuter `--tags deploy`, `sudo restorecon -RF /etc/named.conf /var/named`, `ls -Z /etc/named.conf` et contrôler les AVC. Attendu : service actif, labels corrects, aucune interdiction inattendue | Différences de modules Ansible, paquets BIND/NM et politique SELinux |
| Réseau, transferts, forwards, redémarrage et idempotence complète | **NON EXÉCUTÉ** : pas de serveurs/clients réels | Procédures de recette détaillées dans les deux rapports liés ci-dessus ; utiliser une paire isolée, clients autorisés/interdits, paire de forwarders et redémarrage planifié. Attendu : réponses exactes, refus AXFR FRONT, convergence SOA, stabilité des données et routes persistantes | Pare-feu, versions, capacité, IXFR et chemin SSH non qualifiés |
| Restauration BIND opérationnelle | **NON EXÉCUTÉ** : tests DNS rollback avec doubles | Recette avec sauvegarde vérifiée et injection de panne après ajout de routes ; suivre [OPERATIONS.md](OPERATIONS.md). Attendu : ancienne configuration servie et seuls nouveaux ajouts retirés | Sauvegarde à chaud du secondaire non atomique, disque ou accès défaillant |
| ShellCheck | **NON EXÉCUTÉ** : outil absent | Contrôleur équipé de ShellCheck ; `shellcheck roles/bind9/files/bind-configtool.sh roles/bind9/files/named-healthcheck.sh tests/run_integration_loopback.sh tests/run_integration_front_routes.sh`. Attendu : diagnostics traités sans désactiver globalement les règles | Bash syntax et tests exécutés ne constituent pas une analyse statique exhaustive |
| CI distante et autres versions de contrôleur | **NON EXÉCUTÉ** : aucun workflow GitHub déclenché | Dépôt GitHub avec runners ; exécuter `.github/workflows/ci.yml`. Attendu : toutes les étapes et toute la matrice réussissent | Autres couples Python/Ansible non qualifiés par le seul essai local |

La charge/capacité, les chaînes DNSSEC signées et la restauration après incident restent également à qualifier selon [VALIDATION_NAMED.md](VALIDATION_NAMED.md). Les raisons, procédures, prérequis, résultats attendus et risques y sont décrits. Le retour à la livraison précédente utilise l'archive source conservée ; sur un serveur déjà modifié, appliquer la procédure de restauration BIND et les règles de reprise des routes, sans supprimer les routes déjà committées par déduction.
