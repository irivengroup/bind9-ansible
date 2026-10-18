# Nettoyage de la livraison et validation

Cette révision part de `Bind9-Ansible-front-gateway.zip`, SHA-256
`86aa71084508825f055c16f69bc2b4ce9391996a6a4654d0428690fdcb32fe19`.
Elle nettoie le projet livré ; aucune suppression n'est effectuée sur les
serveurs DNS ni dans les sources de référence du projet ChatGPT.

## Code et périmètre

| Élément | Nettoyage et invariant conservé |
|---|---|
| `front_routes_plan.yml` | Suppression des vérifications locales de règles déjà intégralement réalisées par `front_routes_policy.yml`, et du contrôle de forme noyau déjà réalisé par `front_routes_gateway.yml`. Ces imports restent obligatoires avant le calcul du plan et toute écriture. |
| `front_return_routes.yml` | Suppression du champ interne `found[].address`, propagé dans `bind_front_interface.address` sans consommateur. L'adresse de référence reste `bind_front_ip` ; la découverte et les protections continuent de l'utiliser. |
| Documents | Retrait des six anciens fichiers `PREUVES_*.txt` et de deux rapports dépassés. Les liens concernés renvoient au présent historique. |
| Packaging | Manifeste, métadonnées, empreinte du ZIP et README actualisés ; caches, journaux bruts, secrets et dépendances de laboratoire exclus. |

Le contrôle de références couvre les tâches accessibles depuis les entrées du
rôle, les variables affectées et enregistrées, les assets déployés, handlers et
filtres exportés. Il ne prétend pas inférer tous les consommateurs externes ou
la réflexion arbitraire d'Ansible. Aucun fichier de production orphelin n'a été
identifié dans ce périmètre ; les fonctions optionnelles et les tests restent
présents.

Les scripts et modules Python de production, les templates BIND, inventaires,
paramètres publics, tags, management, hibernation, journal de reprise et
opérations d'ajout/suppression de routes sont conservés. Aucun paquet ou
collection supplémentaire n'est nécessaire. La correction de découverte de la
passerelle FRONT reste active dans le plan, sous verrou, avant commit et rollback.

## Contrôles et reproduction

Les résultats de cette révision, les empreintes des sources testées et celles
des sorties de validation sont consignés dans `DELIVERY.json`.

**EXÉCUTÉ** : 964 tests généraux, trois workflows Ansible de routage ciblés,
six contrôles de syntaxe des playbooks, compilation de 70 fichiers Python sous
Python 3.6.8 et syntaxe Bash des six scripts shell. Les empreintes des 68 fichiers
de production correspondent aux sources testées ; les 65 fichiers de tests sont
conservés octet pour octet. L'audit des liens locaux et le contrôle du contenu du ZIP
figurent également dans `DELIVERY.json`.

Depuis la racine du projet, dans un laboratoire Linux jetable avec Ansible
2.9.27, Jinja2 2.10.3, Python 3.6.8 et pytest/PyYAML :

```bash
python -m pytest -q tests --ignore-glob='*_ansible.py' \
  --ignore=tests/test_dnssec_workflows.py \
  --ignore=tests/test_configtool_workflows.py
python -m pytest -q tests/test_front_routes_ansible.py \
  -k 'secondary_front_address_and_multiple_destinations or verification_failure_rolls_back_profile_and_active_additions or check_mode_computes_a_plan_without_mutating_routes_or_state'
```

La sélection générale inclut les contrôles de références et les scénarios
d'expressions de routage : règles personnalisées ou invalides, observations
malformées, métriques, réseaux protégés, passerelles, idempotence et journaux.
La sélection ciblée exécute les vraies tâches Ansible avec des CLI réseau
isolées : découverte d'adresse secondaire et plusieurs destinations, simulation
sans écriture, échec de vérification et rollback sélectif. Elle ne remplace pas
une recette NetworkManager réelle. Les autres workflows n'ont pas été présentés
comme réexécutés par cette passe de nettoyage.

Pour les deux playbooks, contrôler la syntaxe avec chacun des inventaires
`inventory/dev/hosts.ini`, `inventory/preprod/hosts.ini`, `inventory/prod/hosts.ini` :

```bash
for environment in dev preprod prod; do
  for playbook in bind9-deploy.yml bind9-management.yml; do
    ansible-playbook -i "inventory/$environment/hosts.ini" \
      "playbooks/$playbook" --syntax-check || exit 1
  done
done
```

Le packaging vérifie également chaque lien local Markdown, les empreintes du
manifeste, les permissions Unix archivées, l'absence d'artefacts interdits et la
reproductibilité de deux constructions du ZIP.

**NON EXÉCUTÉ** : déploiement sur vos RHEL 8 et leurs services réels,
NetworkManager/iproute2, redémarrage, charge et matrice CI distante. Raison :
aucune cible accessible ; ce nettoyage ne redéploie pas les serveurs.
Prérequis : paire de recette sauvegardée, SSH/sudo et configuration réseau
existante. Procédure : simulation ciblée `--tags routes --check -v`, application,
second passage, contrôle des routes avant/après et après redémarrage ; recette
DNS selon [TESTS_FONCTIONNELS.md](TESTS_FONCTIONNELS.md), reprises selon
[FRONT_ROUTES.md](FRONT_ROUTES.md) et
[HIBERNATE_DEPLOYMENT.md](HIBERNATE_DEPLOYMENT.md). Attendu : mêmes résultats,
seuls les ajouts requis, préservation des routes initiales et des accès,
idempotence et reprise sélective. Les risques système, de concurrence avec un
opérateur et de différences de versions restent à qualifier. La qualification
de production reste **NON TERMINÉE**.

## Historique

Les fichiers retirés sont conservés intégralement dans le ZIP source nommé en
introduction. Leur retrait du paquet courant évite de confondre des résultats
anciens avec les contrôles de la révision courante. Il ne signifie ni que ces
résultats ont été rejoués ni que les scénarios de test ont été supprimés.

Les chemins et empreintes SHA-256 de chaque fichier retiré figurent dans
`DELIVERY.json`, champ `removed_files`. Les rapports de conception conservés
restent historiques ; les procédures d'exploitation courantes sont dans le
README et les documents fonctionnels.

## Retour arrière

Conserver le ZIP source. Hors opération en cours, restaurer sa copie du projet
sur le contrôleur pour annuler ce nettoyage ; aucune donnée DNS ou route n'a
été supprimée par le nettoyage lui-même. En présence d'un journal de routage
interrompu, terminer la reprise avec une version corrigeant la découverte de la
passerelle, conformément à [CORRECTION_PASSERELLE_FRONT.md](CORRECTION_PASSERELLE_FRONT.md),
avant de remplacer les sources. Ne pas effacer un verrou pour contourner un
conflit.
