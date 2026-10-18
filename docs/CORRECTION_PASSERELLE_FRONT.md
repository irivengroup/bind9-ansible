# Découverte et contrôles de la passerelle FRONT

> Rapport de la livraison précédente `2026.10.18-front-gateway.1`. Les contrôles du nettoyage courant sont décrits dans [NETTOYAGE_ET_VALIDATION.md](NETTOYAGE_ET_VALIDATION.md).

Cette révision part de `Bind9-Ansible-hibernate-deployment.zip` et corrige le
refus imposant une propriété persistante `ipv4.gateway` non vide. Sans les
sorties du serveur concerné, la cause exacte de son incident reste à confirmer.
Le défaut reproduit est le refus d'une passerelle active FRONT lorsque cette
propriété persistante est absente.

## Décision et limites de la découverte

La découverte reste exclusivement attachée à l'interface portant
`bind_front_ip` et à son profil NetworkManager actif, identifié par UUID.
Elle rapproche trois observations existantes :

1. `ipv4.gateway` du profil FRONT ;
2. `IP4.GATEWAY` de l'interface FRONT active ;
3. les prochains sauts des routes **par défaut de FRONT uniquement**, dans la
   table principale du noyau, lorsqu'elles existent.

Une propriété NetworkManager vide ou égale à `--` signifie « absente ».
Une valeur non vide invalide n'est jamais ignorée. Toutes les observations
présentes doivent désigner une seule IPv4 identique. Une passerelle BACK/ADM,
la route par défaut globale, ou le prochain saut d'une route vers un réseau
particulier ne servent jamais de remplacement. Si FRONT n'a que des routes
spécifiques, sans `ipv4.gateway`, sans `IP4.GATEWAY` et sans route par défaut
FRONT, le rôle refuse de deviner une passerelle générale.

Les passerelles hors du sous-réseau FRONT, égales à l'adresse FRONT, à son adresse
réseau ou de diffusion restent refusées. Le routage multipath et les objets
de prochain saut indirects ne sont pas pris en charge. Plusieurs passerelles
FRONT distinctes sont refusées, même si leurs métriques diffèrent.

Le rôle n'écrit aucune passerelle, route par défaut ou propriété de connexion
autre que les ajouts ciblés `+ipv4.routes`. Aucune activation de profil,
installation de paquet ou nouvelle dépendance de production n'est ajoutée.

## Parties du code impactées

Les chemins ci-dessous sont relatifs à `roles/bind9/tasks/tasks.d/deploy/`.

| Fichier | Modification |
|---|---|
| `front_routes_gateway.yml` | Nouveau rapprochement partagé : entrées typées, sorties absentes/invalides distinguées, cohérence des sources, IPv4 littérale et bornes d'octets. Les observations JSON et déjà converties par Ansible sont acceptées. |
| `front_return_routes.yml` | Vérification de l'UUID avant lecture du profil ; lecture d'`IP4.GATEWAY` sur le seul périphérique FRONT. |
| `front_routes_plan.yml` | Utilisation de la passerelle réconciliée ; contrôles de sous-réseau et plan additif conservés. |
| `front_routes_before_write.yml` | Nouvelle lecture et même résolution sous verrou ; comparaison avec la passerelle du plan avant tout ajout. |
| `front_routes_verify.yml` | Même résolution après les ajouts, avant commit ; contrôle des routes et du chemin retour conservé. |
| `front_routes_rollback.yml` | Même résolution avant toute suppression ; un changement de passerelle conserve le journal pour reprise opérateur. |

Les tests sont ajoutés dans `tests/test_native_front_routes.py` et
`tests/test_front_routes_ansible.py`. Le double CLI de laboratoire
`tests/fixtures/native_network_cli.py` distingue passerelle persistante et
passerelle active et simule les changements entre lectures. Ces fichiers ne
sont pas installés sur les serveurs. La CI existante collecte ces tests sans
nouveau job ni dépendance.

Les paramètres publics, inventaires, templates BIND, gestion DNSSEC, management,
hibernation et schéma du journal de reprise restent inchangés. La correction
remplace les tâches du rôle sur le contrôleur ; elle n'exige pas un redéploiement
de `named.conf`.

## Simulation et application ciblées

Depuis la racine du projet sur le contrôleur Linux, adapter le nom d'hôte :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --limit site1-dns01 --tags routes --check -v \
  -e '{"bind_front_routes_enabled": true}'
```

Attendu : périphérique FRONT, passerelle et seuls ajouts proposés ; aucun fichier
de journal ni changement réseau pendant cette simulation.

Après contrôle du plan, appliquer avec la même commande sans `--check`.
Un deuxième passage doit n'ajouter aucune route. Le rôle refuse les conflits
au lieu de supprimer une route préexistante.

### Diagnostic en lecture seule sur le serveur

Le script suivant demande le nom réel de l'interface FRONT ; il n'écrit rien :

```bash
read -r -p 'Interface FRONT : ' front_device
front_uuid=$(LC_ALL=C nmcli -g GENERAL.CON-UUID device show "$front_device") || exit 1
printf 'UUID FRONT : %s\n' "$front_uuid"
LC_ALL=C nmcli -g ipv4.gateway connection show uuid "$front_uuid"
LC_ALL=C nmcli -g IP4.GATEWAY device show "$front_device"
ip -j -4 route show table main
ip -j -4 rule show
```

Si seule une route spécifique porte un prochain saut, déterminer avec l'équipe
réseau s'il convient aux destinations cloud ; ne pas renseigner `ipv4.gateway`
pour contourner l'erreur sans cette analyse. Cette propriété peut faire créer
une route par défaut par NetworkManager.
Sources : [paramètres IPv4 NetworkManager](https://www.networkmanager.dev/docs/api/1.30/settings-ipv4.html),
[nmcli](https://www.networkmanager.dev/docs/api/1.30/nmcli.html).

## Validation et risques restants

**EXÉCUTÉ** : reproduction sur les sources précédentes avec les nouveaux tests :
deux refus sur la tâche signalée, avec propriété persistante vide puis `--` et
passerelle active valide. Les résultats de la correction et leurs empreintes
figurent dans `DELIVERY.json` et [l’historique des validations](NETTOYAGE_ET_VALIDATION.md#historique).

**EXÉCUTÉ** : 964 tests généraux et 146 tests de routage réussis, avec recouvrement
entre ces sélections ; six contrôles de syntaxe Ansible (deux playbooks sur
trois inventaires), compilation de 70 fichiers Python sous Python 3.6.8 et
contrôle syntaxique individuel des six scripts shell livrés.

Les tests exécutent les expressions YAML réelles et le runner Ansible 2.9.27,
Jinja2 2.10.3 et Python 3.6.8 EL8. Les commandes réseau sont des doubles isolés :
elles vérifient le plan, les refus, la simulation, l'idempotence, les contrôles
avant écriture/après ajout et le rollback ; elles ne qualifient pas un véritable
NetworkManager.

La sélection générale et la sélection de routage se recouvrent ; leurs nombres
ne doivent pas être additionnés. Depuis un laboratoire Linux jetable disposant
des dépendances de test et des droits nécessaires, les commandes sont :

```bash
python -m pytest -q tests --ignore-glob='*_ansible.py' \
  --ignore=tests/test_dnssec_workflows.py \
  --ignore=tests/test_configtool_workflows.py
python -m pytest -q tests/test_native_front_routes.py \
  tests/test_front_route_hardening.py tests/test_front_routes_ansible.py \
  tests/test_front_route_hardening_ansible.py
```

Ce n'est pas une réexécution de toute la matrice CI. Les autres workflows
Ansible, les services BIND natifs et la restauration système de la campagne
précédente ne sont pas présentés comme rejoués pendant cette correction.

**NON EXÉCUTÉ** : exécution sur vos serveurs RHEL 8, NetworkManager/iproute2
réels et redémarrage de recette. Raison : aucune cible accessible. Prérequis :
SSH/sudo, profils et réseau FRONT/BACK/ADM existants, relevés avant intervention,
recette sauvegardée. Procédure : diagnostic ci-dessus, simulation ciblée,
application, lecture des routes persistantes et noyau, second passage, contrôle
après redémarrage puis échec contrôlé avec rollback. Attendu : seuls les ajouts
manquants, chemins ADM/BACK et route par défaut conservés, idempotence, permanence
et suppression des seuls ajouts appartenant à l'opération.

Le verrou du rôle ne bloque pas un administrateur ou un autre gestionnaire
réseau. Les relectures détectent les dérives observées ; aucune atomicité avec
ces acteurs externes ni absence absolue d'incident ne peut être garantie. Une
passerelle DHCP peut changer après la fin de l'opération ; le rôle n'est pas
un service de suivi continu. La qualification de production reste
**NON TERMINÉE** jusqu'à la recette sur l'environnement cible.

## Retour à la livraison précédente

Conserver les deux ZIP et remplacer le dossier du projet sur le contrôleur
uniquement lorsqu'aucune opération n'est active. Revenir aux sources précédentes
ne retire pas les routes déjà ajoutées.

S'il reste une opération interrompue, effectuer d'abord sa reprise **avec cette
version corrigée**, après inspection du journal, avec `--tags routes-recover`
selon [FRONT_ROUTES.md](FRONT_ROUTES.md). Le schéma est conservé, mais l'ancienne
version peut refuser la reprise si `ipv4.gateway` est vide : c'est précisément
le contrôle corrigé ici. Ne pas effacer le journal ni renseigner une passerelle
pour contourner ce refus. Revenir à l'ancienne livraison seulement après
résolution de l'opération et disparition de ses verrous.
