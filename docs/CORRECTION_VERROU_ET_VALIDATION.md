# Correction du diagnostic d’acquisition du verrou

Source : `Bind9-Ansible-nettoye.zip`, SHA-256
`a94a9acc09f964be5a38eabd06be206e519833733d04246123d87848b074e953`.

## Diagnostic et décision

Information fournie : le serveur retourne `rc: 1` et `File exists` à la
création de `/var/lib/Bind9-Ansible/.operation.lock`, y compris après une
suppression manuelle. Ce message signifie qu’un chemin existe lors de
l’acquisition ; il ne prouve pas son type, son propriétaire opérationnel ou
son abandon. La cause de sa recréation sur le serveur est **non établie**.

Faits vérifiés dans les sources : le parent est déjà créé avec le module
`ansible.builtin.file` ; le verrou d’opération attendu est un répertoire ;
le playbook standard importe le garde une fois. Le module `file` ne fournit
pas l’acquisition exclusive nécessaire au verrou partagé. La commande
`mkdir` sans `-p` reste donc intentionnelle.

Le changement améliore le diagnostic, supprime la suggestion générique
inadaptée d’Ansible 2.9 pour cette acquisition et conserve le refus
d’écriture lorsqu’une autre opération peut détenir le verrou. Il ne rend
pas légitime la suppression d’un verrou existant et ne prétend pas résoudre
à distance la cause de sa réapparition.

## Impact et reprise

| Domaine | Changement ou invariant |
|---|---|
| Production | Seul `roles/bind9/tasks/tasks.d/deploy/guard.yml` est modifié. |
| Acquisition | `/usr/bin/mkdir`, arguments séparés, création exclusive ; aucun `creates`, `mkdir -p` ou déverrouillage automatique. |
| Diagnostic | En cas d’échec : code retour, stderr original, message Ansible, type du chemin observé sans suivre les liens ni lire son contenu. |
| Compatibilité | `warn: false` uniquement avant Ansible 2.10 ; argument omis sur les versions suivantes. Contrôle exécuté sur Ansible 2.9.27. |
| Concurrence | Échec d’acquisition = aucune propriété du verrou ; arrêt avant le corps du déploiement. |
| Libération | Identité device/inode, absence de récupération en attente, retrait non récursif du seul dossier possédé : inchangés. |
| Fonctionnement | Templates DNS, paramètres publics, management, routes, hibernation, inventaires, tags et playbooks inchangés. |
| Livraison | README, présent rapport, procédure opérateur, tests, métadonnées et manifeste actualisés ; aucune dépendance ajoutée. |

Pour revenir au code précédent, reprendre l’archive source dont l’empreinte
figure ci-dessus. Cela ne libère aucun verrou sur un serveur et ne restaure
aucune configuration : le changement ne contient aucune migration.

La [procédure opérateur](VERROU_OPERATION.md) distingue les opérations actives,
les reprises interrompues et les chemins inattendus. Elle préserve les
modifications manuelles et les journaux FRONT. Le verrou persistant
`.deployment.lock` reste indépendant.

## Vérification reproductible

Laboratoire Linux EL8 isolé, Python 3.6.8, Ansible 2.9.27, Jinja2 2.10.3,
pytest et PyYAML. Sources montées en lecture seule puis copiées dans un
espace temporaire ; aucun réseau, aucun serveur DNS ou routage hôte modifié.
Les interfaces système externes aux scénarios sont simulées.

Depuis la racine du projet, dans ce laboratoire jetable :

```bash
python -m pytest -q tests/test_deployment_lock_ansible.py tests/test_management_ansible.py
python -m pytest -q tests --ignore-glob='*_ansible.py' \
  --ignore=tests/test_dnssec_workflows.py \
  --ignore=tests/test_configtool_workflows.py
for environment in dev preprod prod; do
  for playbook in bind9-deploy.yml bind9-management.yml; do
    ansible-playbook -i "inventory/$environment/hosts.ini" \
      "playbooks/$playbook" --syntax-check || exit 1
  done
done
```

Les tests ajoutés couvrent les répertoires, fichiers, liens et FIFO préexistants,
leur identité et contenu préservés, l’absence de provisioning et de sceau après
refus, ainsi que les erreurs système simulées conservées dans le diagnostic.
Les deux modes Jinja sont exercés. Le scénario de diagnostic sur répertoire
occupé échoue sur le code précédent faute de message enrichi ; le refus
exclusif lui-même existait déjà et reste exigé.

Les résultats effectivement obtenus, les empreintes des sorties et celles des
sources de production testées sont consignés dans `DELIVERY.json`. Les tests
Python du manager ne remplacent pas une recette native de BIND, SELinux ou
NetworkManager. L’avertissement du laboratoire concernant `/root/.ansible`
non inscriptible provient du conteneur en lecture seule et reste distinct de
la suggestion de module supprimée sur l’acquisition.

| Statut | Résultat observé sur les sources finales |
|---|---|
| **EXÉCUTÉ** | 964 tests généraux réussis ; 451 avertissements de dépréciation du runtime historique. |
| **EXÉCUTÉ** | 65 tests réels Ansible de garde/verrou/hibernation et de transport du management réussis en 826,18 secondes. |
| **EXÉCUTÉ** | Les 12 nouveaux cas de diagnostic réussissent aussi séparément ; ils sont inclus dans les 65 tests précédents et ne doivent pas être comptés deux fois. |
| **EXÉCUTÉ** | Six contrôles de syntaxe : deux playbooks et trois inventaires. |
| **EXÉCUTÉ** | Compilation de 70 fichiers Python sous 3.6.8 ; contrôle individuel de syntaxe des six scripts Bash. |
| **EXÉCUTÉ** | Reproduction de l’absence de diagnostic sur la livraison précédente : échec attendu du test sur répertoire occupé. |

Le packaging vérifie les empreintes des 68 fichiers de production par rapport
aux journaux des suites finales, les liens locaux, la conservation des scénarios
de tests existants et des permissions archivées, le manifeste et les octets de
chaque fichier. Deux constructions identiques de l’archive sont requises avant
livraison ; leurs résultats sont enregistrés par le constructeur et l’empreinte
SHA-256 externe.

**NON EXÉCUTÉ** : diagnostic de la recréation du chemin sur votre serveur,
recette complète RHEL 8, services réels et matrice CI distante. Raison : aucune
cible ni session contrôleur accessible. Prérequis : hôte de recette sauvegardé,
SSH/sudo et maîtrise de toutes les exécutions concurrentes. Procédure :
inspection du verrou selon [VERROU_OPERATION.md](VERROU_OPERATION.md),
identification de son détenteur ou récupération contrôlée, puis reprise de
l’opération prévue et contrôles [DNS](TESTS_FONCTIONNELS.md). Attendu : une
seule opération propriétaire, diagnostic exploitable en cas de refus, aucune
modification en cas de verrou occupé, libération après réussite ou erreur
récupérée, conservation si récupération incomplète. Risque restant : opération
concurrente ou interrompue non identifiée, et différences du système réel.
La qualification de production reste **NON TERMINÉE**.
