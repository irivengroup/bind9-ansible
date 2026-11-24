# Autorisation DNS permanente dans firewalld

[Documentation](../../../index.md) › [Configuration](../index.md) › [Réseau et interfaces](index.md) › Autorisation DNS permanente dans firewalld

[Retour au Parent](index.md)


Le rôle autorise le service firewalld **`dns`**, correspondant au port 53
en UDP et TCP. `named` est le nom du service système BIND ; ce n’est pas
le nom du service firewalld utilisé. Le canal RNDC 953 reste local et
n’est pas ouvert par ces tâches.

## Comportement intégré

Le déploiement complet exécute `tasks.d/deploy/firewall.yml` après l’installation
des paquets BIND et avant les vérifications du primaire et la sauvegarde
DNS. Le tag `firewall` permet aussi d’exécuter uniquement cette gestion :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --tags firewall
```

Prérequis : `ip`, `firewall-cmd` et un démon firewalld **déjà actif** sur
les cibles. Le rôle ne l’installe, ne l’active et ne le redémarre pas.
Son absence ou son arrêt provoque un échec explicite. Aucune collection
Ansible supplémentaire ni code Python de pare-feu n’est ajouté.

Les tâches découvrent toutes les interfaces portant les adresses DNS locales
sélectionnées, y compris les adresses secondaires. La sélection vient de
`bind_listen_interfaces` ou de la résolution FRONT/BACK ; une écoute unique est
acceptée. Chaque adresse choisie doit être présente une seule fois. Elles
interrogent ensuite séparément les zones **runtime** et **permanentes** de ces
interfaces. Voir [les déclarations host_vars](INTERFACES_RESEAU.md).
Une interface sans affectation explicite utilise la zone par défaut déjà
configurée. Les couples zone/périmètre sont dédupliqués.

Pour chaque couple, le rôle interroge `--query-service=dns`, ajoute le
service uniquement s’il manque, puis vérifie les deux états. L’ajout
permanent utilise `--permanent`, l’ajout immédiat ne l’utilise pas.
Les codes et réponses doivent être cohérents (`0/yes`, `1/no`) ; les
sorties vides ou les erreurs ne sont pas assimilées à une règle existante.
Un second passage réussi ne réajoute pas de règle.

Il n’y a ni `--reload`, ni `--runtime-to-permanent`, ni changement de
zone, d’interface, de profil NetworkManager ou de service déjà autorisé.
Les affectations et modifications en attente restent en place.
Les commandes suivent la distinction des périmètres décrite par
[firewalld](https://firewalld.org/documentation/man-pages/firewall-cmd.html).

## Portée et reprise

Une autorisation firewalld porte sur une **zone entière**. Si une zone est
partagée avec une interface ADM, la règle DNS concerne également cette
zone ; BIND continue d’écouter seulement sur loopback et les adresses DNS sélectionnées et
d’appliquer ses ACL. Les zones fondées sur des sources, ipsets, rich rules
ou politiques additionnelles nécessitent une recette spécifique : elles
ne sont pas modifiées automatiquement par cette découverte des interfaces.

Les autorisations DNS demandées sont conservées lors d’un rollback BIND.
Une erreur au milieu des ajouts peut laisser une partie des autorisations
en place et provoque un échec ; rejouer le tag `firewall` complète les
seuls éléments manquants. Aucune règle préexistante n’est supprimée.
En mode `--check`, les observations sont exécutées, les ajouts et leur
vérification finale sont omis. La simulation d’un déploiement DNS complet
reste hors du périmètre qualifié.

## Contrôles de recette

Les paragraphes EXÉCUTÉ/NON EXÉCUTÉ ci-dessous conservent le bilan historique de cette fiche ; ils ne remplacent pas les campagnes de qualification ultérieures.

Sur chaque cible, relever les zones de toutes les interfaces d’écoute DNS, actives et permanentes :

```bash
sudo firewall-cmd --state
sudo firewall-cmd --get-active-zones
sudo firewall-cmd --permanent --list-all-zones
```

Pour chaque zone concernée, exécuter `firewall-cmd --zone=NOM_ZONE
--query-service=dns`, puis la même commande avec `--permanent`, en
remplaçant `NOM_ZONE` par un nom réellement relevé. Attendu : `yes`, code
de sortie 0 dans les deux périmètres. Vérifier ensuite les requêtes UDP
et TCP depuis de vrais clients FRONT/BACK, ainsi que le maintien des
services et des affectations initiales. Contrôler la persistance après
redémarrage sur la paire de recette.

**EXÉCUTÉ** : expressions réelles Ansible 2.9/Jinja, et workflows du vrai
runner avec doubles `firewall-cmd`/`ip` : zones distinctes/runtime-permanent,
interface commune, zone par défaut, adresses secondaires, idempotence,
préservation SSH/affectations et refus de données ou commandes invalides.
Résultats courants dans `DELIVERY.json`.

**NON EXÉCUTÉ** : règles noyau/firewalld réels, trafic distant et reboot
RHEL. Raison : aucune cible accessible ; le laboratoire utilise des
doubles CLI. Prérequis : hôtes de recette avec firewalld actif et clients
autorisés. Procédure : contrôles ci-dessus, deuxième exécution et reboot.
Attendu : `dns` actif et permanent, accès DNS UDP/TCP autorisé, aucun
changement d’affectation ou de service sans rapport. Risques : règles
source/politiques locales prioritaires, service `dns` personnalisé,
versions cibles et dérive de configuration. La qualification opérationnelle
reste **NON TERMINÉE** tant que cette recette n’est pas effectuée.

---

Documentation DNSZIBO · navigation actualisée le 23 novembre 2026.
