# Routes de retour FRONT : exploitation

Le réseau existe déjà. Le rôle ajoute uniquement les routes IPv4 nécessaires aux `bind_cloud_clients`, dans le profil NetworkManager actif de FRONT et dans le noyau. Les `bind_internal_clients` ne déclenchent aucun ajout de route. Les adresses isolées deviennent des `/32` ; les CIDR sont normalisés et leur union est conservée, sans élargissement des destinations.

**La découverte suit l’IP FRONT, puis son interface et son profil actif. FRONT n’a pas besoin de porter la route par défaut du système, ni même une route `0.0.0.0/0` active. La passerelle système ou celle d’une autre interface ne sert jamais de repli.**

Deux fichiers portent le fonctionnement courant : `roles/bind9/tasks/tasks.d/deploy/front_return_routes.yml` et `roles/bind9/files/bind-front-routes.sh`. Un seul adaptateur YAML, `front_routes_legacy.yml`, reste réservé à la reprise des anciens journaux. Les douze anciens fichiers auxiliaires séparés sont retirés.

## Préparer puis appliquer

L’inventaire doit garder les deux pairs et leurs adresses ADM/FRONT/BACK, même avec une limite à un hôte. Les trois adresses locales sont des IPv4 littérales ; FRONT doit être présente sur une interface active distincte des interfaces ADM/BACK, y compris lorsque FRONT est une adresse secondaire.

Dans les variables du site :

```yaml
bind_front_routes_enabled: true
bind_cloud_clients:
  - 203.0.113.0/24
  - 172.20.1.25
bind_front_route_metric: 100
```

Les adresses ci-dessus sont des exemples à adapter. Le booléen est `false` par défaut et la métrique est un entier de 1 à 4294967295. Une liste cloud vide n’appelle pas le moteur ; dans ce cas les clients internes doivent être déclarés. Deux listes clientes vides restent refusées.

Le moteur accepte au plus **512 entrées cloud par opération**, avant agrégation ; cette limite concerne le routage, pas l’ACL DNS lorsque les routes sont désactivées. Chaque observation native est limitée à **1 Mio**, avec trente secondes par commande puis cinq secondes avant arrêt forcé si nécessaire. Un dépassement est refusé explicitement ; aucune sortie tronquée n’est acceptée comme état complet.

Depuis la racine du projet sur le contrôleur Linux :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --limit site1-dns01 --tags routes --check -v \
  -e '{"bind_front_routes_enabled":true}'

ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --limit site1-dns01 --tags routes \
  -e '{"bind_front_routes_enabled":true}'
```

La simulation calcule et affiche les ajouts. Elle ne modifie ni profil ni route ; elle peut créer le dossier privé et le fichier vide d’exclusion. Une répétition après application ne doit annoncer aucun changement. Les commandes de mutation peuvent renvoyer un stdout vide : elles sont contrôlées par leur code retour et par la relecture des deux magasins, jamais par `from_json` sur leur stdout.

Ce tag ne redéploie pas BIND et ne modifie pas les ACL DNS. Les variables clientes servent au calcul des routes ; leur modification ne met pas, à elle seule, la configuration DNS live à jour.

Le code retour `2` est réservé à un résultat volontaire de changement ou de plan. Un échec natif avec ce même code devient un échec Ansible ; après mutation, le rollback conditionnel est tenté. Une sortie partielle ou un échec du tri des clients est refusé avant écriture. Si seul le nettoyage temporaire échoue après commit, le rôle signale l’erreur et conserve le répertoire concerné ; les routes déjà committées restent présentes. Contrôler l’état avant de relancer.

## Découverte et protections

Le moteur utilise uniquement Bash et les outils natifs : `iproute`, NetworkManager/`nmcli`, `util-linux` (`flock`), GNU coreutils et `sed`. Aucun Python de routage, `jq`, `netaddr`, filtre personnalisé ou collection Galaxy n’est ajouté. Les outils manquants provoquent une erreur nommant le prérequis avant toute écriture de route.

La passerelle est lue sur le profil actif FRONT par UUID. Si `ipv4.gateway` est vide, l’état actif `IP4.GATEWAY` de cette même interface peut fournir la passerelle. Si les deux sont absents, une unique passerelle de route par défaut déjà déclarée dans ce même profil peut être utilisée. Des observations contradictoires sont refusées. La passerelle doit être une adresse utilisable du sous-réseau de l’adresse FRONT, différente de toutes les IPv4 locales actives, y compris les adresses secondaires. Aucune passerelle globale, BACK ou ADM n’est choisie par défaut. Les métriques explicites existantes restent conservées ; une métrique automatique est déduite de la route active ou de la métrique effectivement observée sur FRONT.

Le moteur ajoute avec `+ipv4.routes` et `ip route add`. Il ne remplace ni ne supprime une ancienne route contradictoire, ne vide aucune table et n’utilise ni `connection up`, ni `device reapply` en exploitation. Les paramètres de profil en attente, notamment DNS, ne sont pas activés. Les adresses, règles, passerelle et magasins sont relus avant écriture et après application ; les routes étrangères doivent rester identiques.

Les plages réservées, les réseaux ADM/BACK, les adresses BACK des pairs et le client SSH IPv4 réel sont protégés. Les formes IPv4 mappées en IPv6 du client SSH sont également reconnues. Les clients directement connectés à FRONT ne nécessitent pas de route via une passerelle. Les règles IPv4 doivent être les trois règles standard des tables `local`, `main`, `default` : VRF, routage par politique et multipath nécessitent une gestion réseau spécifique.

## Exclusion sans verrou périmé

Routes et management partagent `/var/lib/Bind9-Ansible/operations.flock`, fichier **vide**, root `0600`, dans un dossier root `0700`. L’exclusion appartient au processus via `flock`, puis disparaît à sa fin, y compris après un arrêt brutal. L’existence du fichier n’est jamais interprétée comme une opération active ; ne pas le supprimer. Un processus actif concurrent reçoit un refus explicite et peut être relancé après la fin du premier.

Le dossier historique `.operation.lock` reste ignoré par défaut hors hibernation. `bind_operation_lock_enabled: true` réactive sa coopération historique dans le garde de déploiement ; ce réglage reste `false` par défaut. Le sceau d’hibernation `.deployment.lock` conserve son fonctionnement. Les déploiements DNS complets et interventions root doivent toujours être organisés dans une fenêtre exclusive : un éditeur root ne coopérant pas avec l’exclusion peut agir entre deux contrôles.

## Échec et reprise

Une opération qui écrit prépare d’abord `/var/lib/Bind9-Ansible/front-routes.pending/`, avec instantanés, opérations ordonnées, progression et SHA-256. Ce dossier contient des données de reprise ; il ne représente pas un processus vivant. Après réussite isolée, il est supprimé de façon contrôlée, sans suppression récursive. Dans un déploiement DNS complet, il est conservé jusqu’aux contrôles DNS puis committé, ou repris par le rollback du déploiement.

Un échec pris en charge déclenche le retrait des seuls ajouts confirmés, après contrôle des deux magasins. Une modification manuelle, une identité inattendue, un fichier de journal ajouté, une corruption ou une issue de commande rendue ambiguë par un crash empêchent toute suppression hasardeuse. Le journal reste présent avec une cause explicite. La reprise protège aussi le client SSH de la connexion courante, qui peut être différent de celui de l’opération initiale.

Après inspection du journal et du réseau, et après vérification qu’aucune autre opération ou intervention n’est en cours :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --limit site1-dns01 --tags routes-recover
```

La commande reprend le format actuel, ou l’ancien `front-routes.lock/plan.json` via l’adaptateur conservé. Elle ne supprime pas un journal absent ou dangereux pour repartir artificiellement. Un conflit nécessite une réconciliation opérateur des seules routes concernées. Ne pas effacer les journaux pour forcer une exécution.

Une restauration de BIND n’annule pas des routes déjà committées. Retirer un client de l’inventaire ne supprime pas sa route historique : le fonctionnement reste additif.

L’initialisation concurrente du dossier d’état est acceptée seulement si le dossier apparu est ensuite reconnu root `0700`, sans lien ; aucun droit existant n’est corrigé aveuglément. Les preuves, limites et parties impactées figurent dans [VERIFICATION_RESILIENCE_PERFORMANCE.md](VERIFICATION_RESILIENCE_PERFORMANCE.md). La [refonte antérieure](REFONTE_ROUTES_MANAGEMENT.md) conserve sa traçabilité. La recette sur vos serveurs RHEL reste nécessaire.


## Lisibilité et analyse du script

La révision `2026.10.19-syntax.1` rend explicites les littéraux de phase (`'done'`, `'intent'`), les gardes conditionnelles et l’initialisation de la variable de capture. Les expressions régulières restent des expressions Bash natives : une coloration d’éditeur ne constitue pas une erreur de parsing. Les callbacks de sortie sont appelés par `trap` ; seules deux exceptions locales SC2329 documentent cette limite de l’analyseur. Les protections d’application/reprise ne sont pas supprimées. Voir [le contrôle des scripts](VERIFICATION_SYNTAXE_SCRIPTS.md).
