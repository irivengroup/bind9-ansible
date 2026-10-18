# Blocage du parcours routes sur un BIND déjà déployé

Cette révision part de `Bind9-Ansible-correction-verrou.zip`, SHA-256
`558886be062a5c524d4553e43e3c0468fe8e3411fcb3ad5ed555b9e901db8c2f`.

## Périmètre et incident fourni

BIND est déjà déployé. L’opération demandée est uniquement l’ajout des routes
de retour vers `bind_cloud_clients`, par le tag `routes`. L’utilisateur signale
`File exists` à l’acquisition de `.operation.lock`, tout en constatant l’absence
du chemin lors de ses vérifications. La création concurrente, un verrou
abandonné, une cible différente ou une modification locale du projet ne sont
pas des causes établies. Aucune suppression ni reconstruction DNS n’est une
réponse démontrée à cet incident.

Le sceau persistant `.deployment.lock` autorise déjà le tag `routes`. Le dossier
temporaire `.operation.lock` reste requis pour sérialiser les opérations qui
coopèrent avec le déploiement et le management. L’erreur signalée intervient
avant `ip` et `nmcli` ; elle n’établit pas un défaut de calcul de route.

## Changements intégrés

Seul `roles/bind9/tasks/tasks.d/deploy/guard.yml` change dans la production :

- Observation en lecture seule du verrou immédiatement avant son acquisition.
- En cas d’échec, affichage structuré de l’hôte d’inventaire, de la cible de
  connexion, du nom système disponible dans les facts, des tags, de la commande
  réellement retournée par Ansible, de `rc`, de `stderr` et de l’existence du
  chemin avant/après. Aucun secret ni contenu de configuration n’est lu.
- Message d’arrêt neutre : l’**opération demandée** est arrêtée, y compris
  lorsqu’il s’agit de routage. Un chemin observé absent ne reçoit plus le
  commentaire générique sur un répertoire potentiellement occupé.

L’acquisition exclusive, les règles de propriété et de libération, le journal
FRONT, les commandes réseau, le manager, les paramètres, les tags et les
templates BIND restent identiques. Le contrôle préalable n’accorde aucune
propriété du verrou : seule la création exclusive réussie le permet.

| `exists_before` | `exists_after` | Conclusion limitée aux observations |
|---|---|---|
| `true` | `true` | Un chemin existe aux deux instants. Cela ne prouve pas un verrou abandonné. |
| `false` | `true` | Le chemin apparaît entre les observations. Son créateur n’est pas identifié par ce seul résultat. |
| `false` | `false` | Aucun chemin observé avant/après ; examiner la commande et l’erreur originales. Ne pas diagnostiquer un verrou abandonné. |
| `true` | `false` | Le chemin disparaît entre les observations ; rechercher l’opération qui intervient. |

Ces observations ne constituent pas une transaction atomique du système de
fichiers. Les champs `inventory_host`, `connection_target`, `target_nodename`
et `command` permettent de comparer les cibles et arguments réellement utilisés.
Le nom système est explicitement `unavailable` si les facts ne le fournissent
pas. L’affichage structuré est effectué seulement lors d’un échec.

## Installation du correctif sur le contrôleur

Remplacer uniquement le fichier `guard.yml` à son emplacement indiqué ci-dessus
dans la copie du projet qui lance la commande. Il n’est pas nécessaire de
redéployer BIND ni de modifier des fichiers DNS sur le serveur. Conserver
l’inventaire, Vault et les secrets TSIG existants.

Relancer la commande actuelle avec le **seul tag `routes`** et l’activation
booléenne `bind_front_routes_enabled: true`. Le périmètre d’hôtes et l’inventaire
doivent rester ceux de l’opération voulue. Si l’acquisition échoue encore, le
bloc `Show acquisition evidence from this exact Ansible target and operation`
donne les observations nécessaires sans lecture d’un fichier DNS.

Ne pas supprimer un chemin ou un journal pour contourner l’erreur. La procédure
de récupération existante reste dans [VERROU_OPERATION.md](VERROU_OPERATION.md).
Ne pas lancer `deploy` pour résoudre ce blocage d’une opération de routage.

## Retirer les traces d’une précédente opération de routage

Cette procédure ne désinstalle rien et ne reconstruit aucun fichier DNS. Avant
son exécution, suspendre les automatismes et confirmer qu’aucune exécution
Ansible ne travaille encore sur cet hôte. Elle concerne une précédente
opération de **routage**, pas une restauration DNS incomplète.

Sur le serveur exact indiqué dans l’échec, si le dossier `.operation.lock`
existe et est confirmé abandonné, retirer uniquement ce répertoire vide :

```bash
sudo rmdir -- /var/lib/Bind9-Ansible/.operation.lock
```

S’il est absent, rien à retirer. S’il n’est pas vide ou qu’une autre erreur
survient, arrêter et inspecter ; ne pas employer une suppression récursive.
Ce retrait ne supprime aucune route et ne constitue pas un rollback réseau.

Inspecter le journal de routage, sans le supprimer :

```bash
sudo ls -ld -- /var/lib/Bind9-Ansible/front-routes.lock
```

Depuis le contrôleur, sélectionner l’inventaire et le serveur de l’opération :

```bash
read -r -p 'Chemin de l’inventaire : ' bind_route_inventory
read -r -p 'Nom Ansible du serveur : ' bind_route_host
```

Si le journal existe, exécuter la récupération ci-dessous. Si le journal est
absent, omettre cette commande : `routes-recover` refuse normalement un journal
absent. Les contrôles de reprise peuvent refuser une dérive réseau ; dans ce
cas, conserver le journal et traiter l’erreur avant tout nouvel ajout.

```bash
ansible-playbook -i "$bind_route_inventory" playbooks/bind9-deploy.yml \
  --limit "$bind_route_host" --tags routes-recover
```

Lorsque le journal est absent ou que la récupération a réussi, lancer
l’opération d’ajout, avec les deux mêmes variables de commande définies sur le
contrôleur :

```bash
ansible-playbook -i "$bind_route_inventory" playbooks/bind9-deploy.yml \
  --limit "$bind_route_host" --tags routes \
  -e '{"bind_front_routes_enabled": true}'
```

Le sceau `.deployment.lock` reste en place : il autorise ces deux tags. Ne pas
effacer `/var/lib/Bind9-Ansible`, les profils NetworkManager ou les routes
existantes. Si les deux chemins d’opération sont déjà absents, il n’existe
aucune trace de verrou/journal à retirer à ces emplacements ; un nouvel échec
`File exists` exige alors les observations structurées de la même exécution.

## Vérifications et limites

Les tests ajoutés utilisent le véritable point d’entrée du rôle, ses imports,
ses tags, le garde commun et les tâches de routage. Un état déjà déployé est
simulé avec un sceau présent et un fichier DNS opérateur dont les octets,
l’inode et la date de modification doivent être préservés. Le test exécute
successivement simulation, ajout et répétition, avec les deux modes Jinja
d’Ansible 2.9. Les commandes `ip` et `nmcli` sont des doubles isolés ; aucun
démon BIND ni NetworkManager réel n’est qualifié par ces tests.

Les nouveaux cas de diagnostic couvrent un chemin présent, une création
déterministe entre les observations et une erreur système alors que le chemin
reste absent. Les tests existants de refus sur fichier, dossier, lien et FIFO,
ainsi que de récupération et de libération, restent présents. Les nombres
effectivement obtenus et les empreintes des sources testées figurent dans
`DELIVERY.json`. Le rapport précédent
[CORRECTION_VERROU_ET_VALIDATION.md](CORRECTION_VERROU_ET_VALIDATION.md) décrit
la livraison source et ne constitue pas le bilan de la présente passe.

| Statut | Résultat observé |
|---|---|
| **EXÉCUTÉ** | Deux scénarios du parcours `routes` sur un état déjà déployé réussissent avant la modification : incident non reproduit dans ce laboratoire. |
| **EXÉCUTÉ** | Les deux mêmes scénarios réussissent sur le garde modifié : simulation, ajout, second passage, configuration et sceau préservés. |
| **EXÉCUTÉ** | 30 scénarios ciblés du garde, du diagnostic et de la reprise réussissent ; 36 autres cas du fichier ne sont pas sélectionnés par cette passe. |
| **EXÉCUTÉ** | 964 tests généraux réussissent ; 451 avertissements de dépréciation du runtime historique. |
| **EXÉCUTÉ** | Six contrôles de syntaxe des playbooks, compilation de 71 fichiers Python sous 3.6.8 et contrôle individuel des six scripts Bash. |
| **EXÉCUTÉ** | Le nouveau test du diagnostic structuré échoue comme attendu sur la livraison source, où ce bloc n’existe pas. |

Les 996 tests réussis sur les sources finales ne comprennent pas les deux
scénarios de la livraison précédente, ni son échec attendu de reproduction.
Les dépendances de validation sont Ansible 2.9.27, Jinja2 2.10.3 et Python
3.6.8 EL8, dans des conteneurs temporaires sans réseau, sources montées en
lecture seule et données de test isolées. Le packaging exige la correspondance
des 68 empreintes de production testées, la conservation des tests existants,
l’intégrité du manifeste et de chaque fichier, ainsi que deux archives identiques.

Reproduction dans un laboratoire Linux jetable root avec ces dépendances :

```bash
python -m pytest -q tests/test_routes_deployed_entry_ansible.py
python -m pytest -q tests/test_deployment_lock_ansible.py \
  -k 'failed_acquisition or acquisition_evidence or auxiliary_workflows or check_mode_neither or failed_workload or release_never or seal_created_just or failed_recovery_retains or partial_configure_failure or standalone_routes_failure'
python -m pytest -q tests --ignore-glob='*_ansible.py' \
  --ignore=tests/test_dnssec_workflows.py \
  --ignore=tests/test_configtool_workflows.py
```

**NON EXÉCUTÉ** : reproduction sur le serveur cloisonné, ajout de sa route réelle
et matrice CI distante. Raison : aucune cible SSH ni sortie complète de l’appel
en échec accessible. Prérequis : inventaire actuel, cible de recette ou fenêtre
de changement maîtrisée, NetworkManager existant et absence d’opérations
concurrentes. Procédure : appliquer le seul fichier du garde sur le contrôleur,
exécuter l’opération `routes` existante, examiner les champs structurés ; si
l’acquisition réussit, contrôler les ajouts persistants/actifs et le second
passage suivant [FRONT_ROUTES.md](FRONT_ROUTES.md). Attendu : aucune modification
DNS, refus sans mutation réseau si le verrou est occupé, seuls les ajouts de
routes nécessaires si l’opération réussit. Risque restant : cause locale du
blocage non établie et différences du système réel. L’incident utilisateur reste
**NON RÉSOLU** tant que sa route n’a pas pu être appliquée et contrôlée ; la
qualification de production reste **NON TERMINÉE**.
