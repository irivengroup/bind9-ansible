# Durcissement et vérification approfondie

Cette révision reprend `Bind9-Ansible-readme-exploitation.zip` sans modifier l’archive source. Elle conserve RHEL 8, Ansible 2.9, Python cible 3.6.8, le playbook `playbooks/bind9-deploy.yml`, les variables publiques et le routage natif YAML/Jinja. Aucun paquet, collection ou script Python de routage n’est ajouté.

## Périmètre et menaces

L’analyse porte sur les points d’entrée du rôle, les chemins privilégiés, la génération DNS, les secrets, les archives, la reprise, les routes, DNSSEC, firewalld, SELinux, les tests et la CI. Les attaquants considérés sont un processus non privilégié pouvant préparer un objet de fichier inattendu, un fichier importé incorrectement et un état réseau ayant dérivé depuis le plan. Un administrateur root ou un compte contrôleur compromis reste hors de la garantie : ces identités ont déjà les droits de modifier configuration, clés et réseau.

Le rôle ne fournit pas de verrou universel contre les autres outils réseau. Une seule autorité doit modifier le profil FRONT pendant une opération. Les contrôles de dérive réduisent la fenêtre de risque ; ils ne rendent pas atomiques plusieurs commandes NetworkManager et noyau.

## Matrice d’impact

| Domaine | Correction et invariant |
|---|---|
| Secret TSIG | Descripteurs de répertoire, refus des composants symboliques, propriétaire et permissions contrôlés, refus FIFO/fichiers spéciaux/liens physiques, lecture bornée et Base64 canonique. Création privée des parents manquants après contrôle des ancêtres, sans chmod préalable. Publication complète avant visibilité, sans écraser une clé existante ; verrou borné à 30 secondes. |
| Lecteur DNSSEC | Ouverture non bloquante avant contrôle du type, refus des liens physiques ; une FIFO de configuration ou de journal ne peut plus bloquer le démarrage/renouvellement avant `fstat`. |
| Archives BIND | Vérification explicite des erreurs des producteurs, des enregistrements TAR et blocs de fin, d’une seule empreinte liée au nom exact, des chemins canoniques et membres uniques de type fichier/répertoire. Archives et empreintes privées, sans lien physique/symbolique. |
| Racine de backup | Contrôle des ancêtres avant création ; refus d’une racine incluse dans les données BIND ou sous `/etc`. Un répertoire existant doit déjà être root `0700` : ni le rôle ni l’outil ne changent aveuglément les droits d’un répertoire système fourni par erreur. |
| Restauration | Refus si `named` n’est pas observé inactif, vérification des sources/destinations, refus des liens et fichiers spéciaux, échecs SELinux explicites. Une archive de sécurité est conservée si la tentative de retour à l’état antérieur n’aboutit pas. |
| Fichiers Ansible | Distinction fichiers/répertoires, refus des liens physiques sur fichiers gérés ; gardes activées également sur les tags partiels concernés. Runtime : root, un seul lien, pas d’écriture groupe/autres, taille maximale 1 MiB avant lecture. |
| Routes FRONT | Revalidation sous verrou avant écriture, règles standard sans sélecteurs supplémentaires, identités actives plus strictes et validation des deux magasins avant suppression. Reprise bornée depuis un journal privé régulier et unique. |
| Configuration DNS | Politique existante conservée : sans vues, loopback natif, IPv4 en écoute, IPv4/IPv6 sortants, racine privée, ACL et TSIG, DNSSEC optionnel. |
| Dépendances | Aucune dépendance de production ajoutée. Bash/GNU tar/gzip/coreutils/flock et outils RHEL restent nécessaires. |
| CI et tests | Nouveaux scénarios de non-régression ; restauration exercée dans des espaces de montage privés. Le générateur TSIG rejoint la mesure de couverture et l’analyse statique. |
| Exploitation et rollback | Changements de préconditions explicités ci-dessous ; logs et ouvertures firewalld restent conservés après rollback DNS. La qualification RHEL réelle reste distincte. |

## Défauts reproduits avant correction

Les tests de cette révision ont été exécutés sur les sources antérieures ou avant modification du composant. Ils reproduisent notamment :

- le blocage sur un secret FIFO et l’acceptation de verrous non privés, de liens physiques ou d’un ancêtre symbolique ;
- l’acceptation d’un flux gzip ne contenant pas un TAR complet malgré une empreinte correspondante ;
- l’acceptation de membres ambigus/dupliqués et d’un fichier d’empreintes décrivant plusieurs fichiers ;
- la restauration alors que `named` est encore actif, le changement de mode d’un répertoire général et l’erreur SELinux ignorée ;
- le suivi d’un répertoire de zones symbolique jusqu’à la suppression d’une zone extérieure marquée ;
- la confusion fichier/répertoire dans les gardes et l’acceptation de runtime partagé ou modifiable par le groupe ;
- l’acceptation de sélecteurs supplémentaires dans les règles IPv4 et d’attributs actifs incompatibles avec la route attendue ;
- la suppression persistante avant détection d’une identité active modifiée au rollback ;
- le parcours incorrect d’une liste IPv4 sérialisée comme du texte et la confusion d’une IPv6 SSH native avec une IPv4 mappée ;
- la lecture bloquante d’une FIFO DNSSEC avant contrôle de son type ;
- la création d’un répertoire TSIG à travers un ancêtre symbolique par la tâche préparatoire, avant le refus du helper.

Ces scénarios ne sont pas un décompte de vulnérabilités indépendantes ou de CVE. Les preuves avant/après et résultats exacts sont référencés par `DELIVERY.json` ; les journaux de laboratoire et secrets temporaires sont exclus du ZIP.

## Compatibilité et préparation de la mise à niveau

Les variables publiques gardent leur nom et leur valeur par défaut. Les contrôles deviennent volontairement plus stricts ; une situation auparavant acceptée peut être refusée avant modification.

1. Vérifier que le chemin contrôleur `bind_tsig_secret_file` et ses ancêtres ne sont pas symboliques. Les ancêtres appartiennent à root ou au compte Ansible et ne sont pas inscriptibles par groupe/autres, sauf un répertoire sticky root tel que `/tmp`. Son parent existant doit appartenir au compte Ansible et être `0700` ; les parents manquants sont créés privés par le helper. Le fichier doit être privé, régulier, avec un seul lien et contenir exactement 44 caractères Base64 canoniques représentant 32 octets, avec au plus un LF terminal. Ne pas afficher le secret pour le contrôler ; conserver une sauvegarde protégée avant toute correction.
2. Contrôler `bind_backup_root`. Une racine existante doit appartenir à root en `0700`, sans ancêtre symbolique ou non fiable. Les ancêtres inscriptibles par d’autres sont refusés, sauf répertoire sticky root tel que `/tmp`. Choisir un sous-répertoire dédié ; ne pas désigner `/etc`, `/var/named` ou un répertoire système général. Ne corriger les droits qu’après identification certaine de la destination et sauvegarde.
3. Les archives et `.sha256` importés doivent appartenir à l’utilisateur exécutant l’outil — root pour backup/list/restore — et être privés, sans lien physique/symbolique. Une empreinte ancienne doit décrire uniquement son archive. Les TAR avec chemins `./`, doublons, liens ou fichiers spéciaux sont refusés ; préparer une migration contrôlée plutôt que désactiver les vérifications.
4. Relever et traiter les objets inattendus sous `/etc/named`, `/var/named`, les logs et chemins runtime. Un lien physique peut partager une donnée sans que son nom le révèle : contrôler `stat` et l’origine du fichier avant remplacement.
5. Sauvegarder durablement les fichiers runtime et leurs droits avant changement. Le snapshot du rôle est en mémoire du contrôleur et ne couvre pas une perte de ce dernier.
6. Pour les routes, confirmer l’absence d’autre opérateur ou automatisation modifiant le profil. Un journal interrompu doit être examiné et repris explicitement ; un conflit est conservé pour décision humaine, jamais effacé pour forcer le passage.

Le fichier TSIG conserve le même format et le même secret ; aucune rotation automatique n’est introduite. Les archives générées normalement par la livraison précédente utilisent déjà des noms canoniques et des empreintes unitaires. Une archive non standard n’est pas rendue fiable par le seul fait de recalculer son checksum.

## Fichiers et points d’entrée impactés

| Fonction | Sources |
|---|---|
| Secrets et lecteur DNSSEC | [tsig-secret.py](../roles/bind9/files/tsig-secret.py), [tsig.yml](../roles/bind9/tasks/tasks.d/tsig.yml), [bind-private-dnssec.py](../roles/bind9/files/bind-private-dnssec.py) |
| Backup et restauration | [bind-configtool.sh](../roles/bind9/files/bind-configtool.sh), [migration.yml](../roles/bind9/tasks/tasks.d/migration.yml) |
| Gardes privilégiées | [path_safety.yml](../roles/bind9/tasks/tasks.d/path_safety.yml), [runtime_snapshot.yml](../roles/bind9/tasks/tasks.d/runtime_snapshot.yml), [main.yml](../roles/bind9/tasks/main.yml) |
| Entrée FRONT et normalisation | [front_return_routes.yml](../roles/bind9/tasks/tasks.d/front_return_routes.yml), [front_routes_ipv4.yml](../roles/bind9/tasks/tasks.d/front_routes_ipv4.yml), [front_routes_parse_profile.yml](../roles/bind9/tasks/tasks.d/front_routes_parse_profile.yml), [front_routes_plan.yml](../roles/bind9/tasks/tasks.d/front_routes_plan.yml) |
| Préconditions et reprise FRONT | [front_routes_before_write.yml](../roles/bind9/tasks/tasks.d/front_routes_before_write.yml), [front_routes_policy.yml](../roles/bind9/tasks/tasks.d/front_routes_policy.yml), [front_routes_ssh_normalize.yml](../roles/bind9/tasks/tasks.d/front_routes_ssh_normalize.yml), [front_routes_compare.yml](../roles/bind9/tasks/tasks.d/front_routes_compare.yml), [front_routes_verify.yml](../roles/bind9/tasks/tasks.d/front_routes_verify.yml), [front_routes_validate_journal.yml](../roles/bind9/tasks/tasks.d/front_routes_validate_journal.yml), [front_routes_recover.yml](../roles/bind9/tasks/tasks.d/front_routes_recover.yml), [front_routes_rollback.yml](../roles/bind9/tasks/tasks.d/front_routes_rollback.yml) |
| CI | [.github/workflows/ci.yml](../.github/workflows/ci.yml) : suites de restauration et FRONT, couverture du générateur TSIG et analyse statique |
| Non-régression secrets et chemins | [test_tsig_hardening.py](../tests/test_tsig_hardening.py), [test_private_dnssec_hardening.py](../tests/test_private_dnssec_hardening.py), [test_path_hardening.py](../tests/test_path_hardening.py) |
| Non-régression archives et transferts | [test_archive_hardening.py](../tests/test_archive_hardening.py), [test_configtool_workflows.py](../tests/test_configtool_workflows.py), [test_configtool_axfr.py](../tests/test_configtool_axfr.py) |
| Non-régression routes | [test_front_route_hardening.py](../tests/test_front_route_hardening.py), [test_front_route_hardening_ansible.py](../tests/test_front_route_hardening_ansible.py), [native_network_cli.py](../tests/fixtures/native_network_cli.py) |

`named.conf.j2`, les autres templates, les valeurs par défaut, les inventaires et le playbook ne sont pas modifiés dans cette révision. Les corrections de sécurité sont intégrées aux tâches et outils existants. `README.md`, `FRONT_ROUTES.md` et `OPERATIONS.md` décrivent les nouvelles préconditions ; `DELIVERY.json` et `SOURCE-MANIFEST.sha256` sont régénérés. La comparaison exacte avec l’archive source figure dans `DELIVERY.json`.

## Incident IPv4 signalé

Les passerelles ont été déclarées définies par l’utilisateur. Un défaut de représentation est reproduit : une liste IPv4 transmise sous forme de texte JSON n’était pas traitée comme une liste par le normaliseur. Les producteurs sérialisent maintenant explicitement et le consommateur accepte soit une liste native, soit un tableau JSON, puis contrôle strictement les adresses. Le test de non-régression échoue avant et accepte les deux représentations après correction.

Cela corrige un cas pouvant produire le symptôme signalé ; sans la valeur exacte du message d’erreur initial, ce n’est pas une attribution certaine de l’incident. Les diagnostics affichent aussi le contexte et le type de la valeur refusée. Les passerelles et paramètres réseau existants ne sont pas réécrits, aucune entrée invalide n’est ignorée et aucune passerelle de remplacement n’est choisie. Voir [FRONT_ROUTES.md](FRONT_ROUTES.md).

Un second cas reproduit concerne les gardes SSH : `2001:db8::ffff:1` est une IPv6 native, alors que l’ancien découpage pouvait la traiter comme une IPv4 mappée et échouer à cette même assertion. Les trois cas de non-régression couvrent les formes hexadécimale, mixte et traduite ; les IPv4 mappées complètes restent protégées.

## Restauration et échecs

La procédure normale reste celle du [README](../README.md#reprise). Arrêter le renouvellement privé et `named` avant `restore`, choisir une archive réelle et conserver une sauvegarde récente hors du serveur. L’outil vérifie désormais l’état inactif et refuse un état systemd ambigu.

La validation BIND se fait après extraction, avant remise en service. Une erreur d’extraction, de configuration ou de relabellisation fait échouer la restauration et déclenche la tentative de récupération de l’état immédiatement précédent. Si cette récupération échoue, le fichier privé `.restore-safety.*` est conservé dans la racine des backups et son chemin est signalé. Ne pas supprimer cette preuve ni relancer `named` sans diagnostic. L’outil ne redémarre jamais le service lui-même.

L’archive de sécurité et le backup BIND ne constituent pas un snapshot du système : ils ne restaurent pas les routes, firewalld, les paquets ni tous les fichiers runtime. Les fichiers non marqués absents d’une archive ne sont pas supprimés automatiquement. Après retour arrière, contrôler labels, service, localhost, zones, forwarding, SOA et accès depuis les clients.

## Méthode de validation et limites

| Statut | Résultat observé sur cette révision |
|---|---|
| **EXÉCUTÉ** | 548 tests réussis avec Ansible 2.9.27/Jinja 2.10.3, puis les mêmes 548 avec Jinja 3.0.3 ; contrôleur Python 3.8.20. Les avertissements de dépréciation des dépendances historiques restent visibles. |
| **EXÉCUTÉ** | 18 workflows FRONT, 8 workflows DNSSEC et 5 workflows de sauvegarde/restauration isolés, en complément : 579 cas pytest distincts au total, sans additionner les répétitions entre moteurs. |
| **EXÉCUTÉ** | 90 cas de routes rejoués avec le typage natif Jinja activé. NetworkManager réel 1.46.0 : 4 parcours, idempotence, persistance après relecture du profil, reprise sélective et conservation des routes BACK/ADM/par défaut. |
| **EXÉCUTÉ** | Chaque moteur BIND testé — EL8 9.11.36 et 9.18 — charge 109 configurations et réussit 282 contrôles DNS. Les fichiers natifs restent inchangés ; forwarding IPv6 UDP/TCP opérationnel avec absence d’écoute DNS IPv6. |
| **EXÉCUTÉ** | Python 3.6.8 extrait du laboratoire EL8 : compilation des sources de production, création/réutilisation TSIG et réconciliation DNSSEC. Ce test ne remplace pas l’exécution du rôle complet sur votre OS. |
| **EXÉCUTÉ** | Couverture : 389 lignes sur 391, soit 99,49 %, pour cinq modules Python mesurés ; seuil de 98 % conservé. Suite de mesure : 306 tests. Aucun constat Bandit sur le périmètre analysé. |
| **EXÉCUTÉ** | Références des 12 assets, 38 fichiers de tâches, 11 variables par défaut et 11 filtres ; 66 YAML analysés. README : 56 liens, 16 ancres, 23 exemples Bash analysés et trois combinaisons clients testées sur les assertions réelles de la paire d’exemple. |

Les preuves de journalisation/SELinux — 12 workflows — et firewalld — 8 workflows — sont héritées de la livraison précédente et ne sont pas comptées parmi ces 579 cas exécutés. Les tâches correspondantes ne changent pas. Les journaux de laboratoire restent hors de l’archive ; les résultats structurés et le manifeste y figurent dans `DELIVERY.json` et `SOURCE-MANIFEST.sha256`.

Les tests d’expressions évaluent les conditions YAML réelles avec Ansible ; les workflows emploient son vrai moteur. Les tests CLI backup/restore montent des `/etc` et `/var` temporaires dans un espace de montage privé avant toute opération. Ils utilisent les vrais tar, gzip, empreintes et fichiers ; systemd, BIND et restorecon y sont des doubles de contrat. Ils prouvent la logique de refus/reprise, pas le fonctionnement de ces services sur votre serveur.

Les essais DNS emploient des démons BIND réels dans un réseau isolé. Les workflows de routes utilisent des doubles natifs, complétés par le laboratoire NetworkManager. Aucun de ces essais ne qualifie SELinux enforcing, firewalld noyau, boot et interruptions réelles de votre paire RHEL.

| Statut | Contrôle absent, procédure et risque |
|---|---|
| **NON EXÉCUTÉ** | Déploiement et second passage sur vos RHEL 8 : aucune cible accessible. Prérequis : paire de recette, accès ADM, inventaire réel et sauvegardes exportées. Exécuter `deploy` puis les tests du README, rejouer et contrôler les différences attendues. Risque : versions et états système propres aux cibles. |
| **NON EXÉCUTÉ** | SELinux enforcing, systemd et firewalld réels : suivre les recettes des documents dédiés, confirmer labels/écritures, scopes runtime/permanent et redémarrage un hôte à la fois. Risque : politiques et services locaux non reproduits par les doubles. |
| **NON EXÉCUTÉ** | Routage RHEL en présence des équipements réels, reboot, perte SSH et panne du contrôleur : conserver accès ADM de secours, comparer profil/table/règles/chemins avant/après et essayer la reprise contrôlée. Attendu : aucune perte ADM/BACK et retrait des seuls ajouts de l’opération. Une modification concurrente extérieure reste interdite. |
| **NON EXÉCUTÉ** | Charge, grands backups, coupure d’alimentation au moment des écritures et durée de renouvellement DNSSEC : mesurer sur recette représentative, vérifier stockage, RPO/RTO et renouvellement au-delà de sept jours. Risques : durabilité et capacité non garanties par les tests de logique. |
| **NON EXÉCUTÉ** | Audit exhaustif des vulnérabilités/chaînes d’approvisionnement et CI distante : aucun nouveau contrôle réseau de tous les paquets n’a été exécuté. Faire analyser les versions réellement déployées et lancer la matrice dans votre dépôt. Les dépendances de test ne sont pas toutes verrouillées par un lockfile ; la seule présence de la CI ne prouve pas sa reproductibilité totale. |

**Qualification opérationnelle NON TERMINÉE.** Les gardes ne remplacent pas le moindre privilège, le stockage chiffré des sauvegardes, un canal de distribution authentifié et une recette depuis les réseaux clients.
