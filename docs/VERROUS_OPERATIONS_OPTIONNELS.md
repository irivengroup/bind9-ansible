# Verrou commun temporairement optionnel

Cette révision repart exclusivement de l’archive fournie `Bind9-Ansible-hibernate-deployment.zip`, SHA-256 `63f826d85deb0ce68ee45dbe87a77fade81df44f636e5fdd0749281d2052c992`. Elle applique la demande d’ignorer temporairement le verrou commun hors hibernation. Elle ne réimporte pas les modifications des livraisons intermédiaires de diagnostic ou de découverte des passerelles.

## Contrat d’exploitation

```yaml
bind_operation_lock_enabled: false
```

Cette nouvelle entrée du rôle est un booléen YAML strict, par défaut `false`. Une chaîne `"false"`, un entier ou une valeur nulle sont refusés. Pour la passer sur la ligne de commande, employer du JSON typé : `-e '{"bind_operation_lock_enabled":false}'`. Avec `true`, le rôle réactive le verrou commun pour les opérations ordinaires. L’argument de bas niveau `operation_lock` du module `bind9_manage` reste à `true` par défaut pour préserver les appels directs ; le rôle lui transmet explicitement son nouveau réglage.

| Opération | Réglage par défaut | Avec `bind_operation_lock_enabled: true` |
|---|---|---|
| Déploiement sans création du sceau | Verrou commun ignoré | Verrou commun requis |
| Routes, reprise de routes, sauvegarde | Verrou commun ignoré | Verrou commun requis |
| Management : lecture, listing, simulation, écriture unitaire ou lot | Verrou commun ignoré | Verrou commun requis |
| Tag isolé `hibernate` | Verrou commun requis | Verrou commun requis |
| Déploiement complet `all` ou `deploy` avec `hibernate_deployment: yes` | Verrou commun requis | Verrou commun requis |
| `--check` | Aucun verrou commun acquis | Aucun verrou commun acquis par le garde de déploiement ; le management conserve son contrat de simulation avec verrou en mode strict |

La présence de `hibernate_deployment: yes` dans l’inventaire ne force pas le verrou commun pour un parcours isolé `routes`, `routes-recover` ou `backup`. La création d’un sceau, elle, reste protégée.

Un ancien `/var/lib/Bind9-Ansible/.operation.lock` est **ignoré sans être supprimé, modifié ni adopté** en mode ordinaire sans verrou. Il n’est pas nécessaire de le nettoyer pour ce contournement. Un sceau `/var/lib/Bind9-Ansible/.deployment.lock` existant continue de bloquer toute reconstruction DNS, même avec les deux réglages à `false`. Il ne bloque pas les routes ou le management.

Les journaux FRONT, `front-routes.lock`, sauvegardes de transaction, contrôles des chemins et de révision, protections des fichiers live et verrou interne des sauvegardes restent en place. Ne pas les effacer pour forcer une opération. Une reprise incomplète nécessite toujours une analyse de son journal ; sans verrou commun, elle ne bloque plus automatiquement tous les autres parcours.

**Une seule opération doit être lancée à la fois**, sur chaque serveur, y compris les autres automatisations et les interventions manuelles. Pendant une hibernation, arrêter également ces autres opérations : celles qui ignorent le verrou commun ne peuvent pas être exclues par ce verrou. Les checksums détectent les conflits observables, mais ne remplacent pas l’exclusion mutuelle. L’atomicité d’un lot de plusieurs fichiers face à une écriture concurrente ou un crash n’est pas garantie.

## Ajouter uniquement une route

Depuis la racine du projet sur le contrôleur Linux, avec les variables existantes `bind_route_inventory` et `bind_route_host` renseignées :

```bash
ansible-playbook -i "$bind_route_inventory" playbooks/bind9-deploy.yml \
  --limit "$bind_route_host" --tags routes --check \
  -e '{"bind_front_routes_enabled":true,"bind_operation_lock_enabled":false}'

ansible-playbook -i "$bind_route_inventory" playbooks/bind9-deploy.yml \
  --limit "$bind_route_host" --tags routes \
  -e '{"bind_front_routes_enabled":true,"bind_operation_lock_enabled":false}'
```

Conserver l’inventaire complet des deux pairs, les variables clientes et les secrets existants. Ce parcours ne reconstruit pas BIND. Les autres gardes du routage restent actifs : cette modification ne corrige pas une passerelle absente du profil, un réseau ambigu ou une reprise FRONT en attente. Le tag `routes-recover` sert uniquement à reprendre un journal FRONT effectivement présent ; ce n’est pas une suppression générale des traces d’opération.

Pour revenir au mode strict, déclarer `bind_operation_lock_enabled: true`. Avant cette réactivation, examiner tout ancien verrou et les journaux associés, vérifier qu’aucune opération n’est active et achever toute reprise. Ne jamais supprimer un verrou protégeant une opération active. Le retour au code de l’archive initiale restaure aussi le verrou obligatoire, sans annuler les routes ou données déjà modifiées.

## Parties du code impactées

| Fichier | Changement |
|---|---|
| `roles/bind9/defaults/main.yml` | Nouvelle entrée publique, défaut `false`. |
| `roles/bind9/tasks/tasks.d/deploy/guard.yml` | Validation du type et acquisition conditionnelle ; acquisition imposée pour l’hibernation. Libération uniquement si acquise. |
| `roles/bind9/tasks/tasks.d/manage/main.yml` | Validation et transmission du réglage au module. |
| `roles/bind9/library/bind9_manage.py` | Argument optionnel ; exécution avec ou sans contexte de verrou commun. |

Le cœur du manager, les tâches de routes, les templates DNS, les inventaires et les playbooks sont identiques à l’archive initiale. Le banc existant de transport management choisit explicitement le mode strict afin de conserver ses assertions de concurrence. Trois nouveaux fichiers de tests couvrent la politique, le contournement et l’entrée réelle du parcours routes. La CI ajoute ces trois fichiers à sa commande privilégiée existante, sans changement de dépendances ni de seuils.

## Vérifications et limites

Les résultats exacts, empreintes des fichiers de production et contrôles d’archive sont enregistrés dans `DELIVERY.json`. Les rapports d’autres révisions sont historiques. Les tests locaux utilisent Python 3.6.8 EL8, Ansible 2.9.27 et Jinja2 2.10.3. Les commandes système DNS et réseau des scénarios Ansible sont des doublures isolées ; aucune route du serveur réel n’a été ajoutée.

La suite générale compte **960 scénarios réussis**, complétés par **2 scénarios routes**. Sur les **22 scénarios ciblés**, 21 ont réussi lors de la première exécution ; un nouveau test ne propageait pas son tag à l’inclusion dynamique et n’exécutait aucune tâche. Seul ce test a été corrigé et réexécuté avec succès, sans modification de la production ni affaiblissement d’une assertion. Le journal initial et celui de la correction sont distingués dans les preuves. Les contrôles de syntaxe couvrent 6 combinaisons playbook/inventaire, 73 fichiers Python et 6 scripts shell.

| Statut | Contrôle et portée |
|---|---|
| **EXÉCUTÉ** | Le scénario « routes avec ancien verrou présent » échoue sur l’archive initiale à `mkdir` avec `File exists` : reproduction déterministe de la restriction supprimée, sans établir la cause de l’incident distant où le chemin était annoncé absent. |
| **EXÉCUTÉ** | Politique de sélection, types invalides, opérations ordinaires avec ancien verrou conservé, hibernation toujours exclusive, sceau toujours bloquant pour la reconstruction, management et refus du checksum périmé ; résultats détaillés dans `DELIVERY.json`. |
| **EXÉCUTÉ** | Entrée réelle du rôle pour routes : simulation, ajout puis répétition sans nouvel ajout, anciens verrou et sceau conservés, configuration DNS inchangée ; doublures `ip`/`nmcli`, deux modes Jinja. |
| **EXÉCUTÉ** | Suite générale, syntaxe des deux playbooks avec trois inventaires, compilation Python 3.6.8, syntaxe des scripts shell ; résultats et empreintes dans `DELIVERY.json`. |
| **NON EXÉCUTÉ** | Recette sur vos serveurs : aucun accès au réseau cloisonné. Prérequis : fenêtre exclusive, sauvegarde et inventaire existants, SSH/sudo, outils RHEL/NetworkManager disponibles. Exécuter les deux commandes routes ci-dessus, vérifier `nmcli -g ipv4.routes connection show uuid UUID_FRONT`, `ip -4 route show`, puis répéter l’application. Attendus : acquisition commune sautée, seule la route nécessaire ajoutée, profil et noyau cohérents, répétition sans ajout. Risque restant : particularités réelles des profils, adresses, passerelles et versions. |
| **NON EXÉCUTÉ** | Recette hibernation et management sur clone RHEL : BIND opérationnel, sauvegarde et absence d’exécution concurrente requis. Exécuter le tag `hibernate` selon son guide ; vérifier le sceau, le refus d’une reconstruction et une lecture/checksum/simulation management selon son guide. Attendus : sceau conservé et écriture ciblée seulement. Risque restant : intégration système réelle, réplication et reprise après incident. |
| **NON EXÉCUTÉ** | Matrice CI complète, charge et crash physique : environnement de qualification indisponible. Lancer la CI livrée et les scénarios de panne documentés sur clone jetable ; attendus : contrôles réussis et reprise sans suppression des données d’un opérateur. Risque restant : combinaisons de versions, stockage et disponibilité non qualifiés. |

**Qualification production : NON TERMINÉE**, dans l’attente de ces recettes. La livraison retire un blocage identifié dans le flux de contrôle ; elle ne démontre ni la cause distante du verrou ni l’absence de tout incident possible.
