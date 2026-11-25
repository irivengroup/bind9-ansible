# Routes de retour FRONT : exploitation

[Documentation](../../../index.md) › [Configuration](../index.md) › [Réseau et interfaces](index.md) › Routes de retour FRONT : exploitation

[Retour au Parent](index.md)

## Sommaire

- [Préparer puis appliquer](#préparer-puis-appliquer)
- [Découverte et protections](#découverte-et-protections)
- [Exclusion sans verrou périmé](#exclusion-sans-verrou-périmé)
- [Échec et reprise](#échec-et-reprise)
- [Campagnes et qualification](#campagnes-et-qualification)

## Préparer puis appliquer

Pour activer cette fonction de routage, l’inventaire doit garder les deux pairs et leurs adresses ADM/FRONT/BACK, même avec une limite à un hôte. Les trois adresses locales sont des IPv4 littérales ; FRONT doit être présente sur une interface active distincte des interfaces ADM/BACK, y compris lorsque FRONT est une adresse secondaire.

Cette contrainte ne limite pas le rôle DNS à trois interfaces. Avec une seule
interface, une seule écoute DNS ou ADMIN partagé avec FRONT/BACK, le rôle utilise
le routage existant et n’ajoute pas de routes FRONT. Les clients internes ne
déclenchent jamais d’ajout. Voir [les topologies host_vars](INTERFACES_RESEAU.md).
Une destination SSH par nom ou adresse traduite exige `bind_admin_ip`, l’IPv4
ADMIN réellement présente sur la cible.

Dans les variables du site :

```yaml
bind_front_routes_enabled: true
bind_trusted_networks:
  cloud_clients:
    - 203.0.113.0/24
    - 172.20.1.25
  internal_clients: []
bind_front_route_metric: 100
```

Les adresses ci-dessus sont des exemples à adapter. Le booléen est `false` par défaut et la métrique est un entier de 1 à 4294967295. Une liste cloud vide n’appelle pas le moteur ; dans ce cas les clients internes doivent être déclarés. Deux listes clientes vides restent refusées.

Le moteur accepte au plus **512 entrées cloud par opération**, avant agrégation ; cette limite concerne le routage, pas l’ACL DNS lorsque les routes sont désactivées. Chaque observation native est limitée à **1 Mio**, avec trente secondes par commande puis cinq secondes avant arrêt forcé si nécessaire. Un dépassement est refusé explicitement ; aucune sortie tronquée n’est acceptée comme état complet.

Depuis la racine du projet sur le contrôleur Linux :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --limit site1-dns01 --tags routes --check -v \
  -e '{"bind_front_routes_enabled": true}'

ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --limit site1-dns01 --tags routes \
  -e '{"bind_front_routes_enabled": true}'
```

La simulation calcule et affiche les ajouts. Elle ne modifie ni profil ni route ; elle peut créer le dossier privé et le fichier vide d’exclusion. Une répétition après application ne doit annoncer aucun changement. Les commandes de mutation peuvent renvoyer un stdout vide : elles sont contrôlées par leur code retour et par la relecture des deux magasins, jamais par `from_json` sur leur stdout.

Ce tag ne redéploie pas BIND et ne modifie pas les ACL DNS. Les variables clientes servent au calcul des routes ; leur modification ne met pas, à elle seule, la configuration DNS live à jour.

Le code retour `2` est réservé à un résultat volontaire de changement ou de plan. Un échec natif avec ce même code devient un échec Ansible ; après mutation, le rollback conditionnel est tenté. Une sortie partielle ou un échec du tri des clients est refusé avant écriture. Le nettoyage temporaire précède la clôture atomique du journal ou la remise du reçu différé. Son échec garde le rollback armé : seuls les ajouts confirmés de cette opération sont retirés après les contrôles de propriété et de dérive. Une erreur empêchant cette reprise conserve les preuves et reste bloquante ; contrôler l’état avant de relancer.

## Découverte et protections

Le moteur utilise uniquement Bash et les outils natifs : `iproute`, NetworkManager/`nmcli`, `util-linux` (`flock`), GNU coreutils et `sed`. Aucun Python de routage, `jq`, `netaddr`, filtre personnalisé ou collection Galaxy n’est ajouté. Les outils manquants provoquent une erreur nommant le prérequis avant toute écriture de route.

L’adresse FRONT est repérée dans les adresses actives, y compris secondaires. Les routes correspondant à cette IP sont classées par préfixe le plus spécifique puis métrique numérique la plus faible ; une égalité ambiguë est refusée. Les répétitions du même chemin connecté sur la même interface, avec des sources locales différentes, sont reconnues : elles ne créent ni ambiguïté ni nouvelle route. Les chemins vers des interfaces/passerelles différentes ou dotés d’attributs non reconnus restent refusés. L’interface retenue doit posséder l’adresse FRONT. Son profil NetworkManager actif est identifié par UUID, sans supposer que le nom de connexion est celui de l’interface.

La passerelle provient exclusivement des routes IPv4 de la table principale portées par cette interface. Les propriétés `ipv4.gateway`, `IP4.GATEWAY` et les routes persistantes ne fournissent aucun repli. Plusieurs routes utilisant le même prochain saut sont acceptées ; des prochains sauts distincts provoquent un refus avant écriture. Une route directement connectée ne fournit pas de passerelle. Si toutes les destinations clientes sont directement connectées à FRONT, aucune passerelle n’est requise. Sinon, la passerelle doit être utilisable sur l’un des sous-réseaux de FRONT et différente de toutes les IPv4 locales actives. Les routes BACK/ADM, dont la route par défaut système, restent exclues de cette découverte. Les métriques existantes sont conservées. La [correction antérieure](../../historique/CORRECTION_PASSERELLE_FRONT.md) décrit une méthode remplacée et reste une trace historique.

La correction reprend le repérage par IP, l’identification du profil actif, l’ajout conditionnel exact et le contrôle du chemin proposés dans le playbook utilisateur. La recherche native conserve la prise en charge des IP secondaires, sans supposer un champ `ipv4.gateway` dans les faits Ansible. L’UUID reste préféré au nom de connexion, et la comparaison structurée des routes remplace une recherche par expression régulière. L’ajout persistant par `nmcli +ipv4.routes` est activé par `ip route add`, sans réactiver la connexion ni ses paramètres en attente. La relecture des magasins remplace une pause fixe. **Aucun ping de validation n’est ajouté, conformément à la demande.** La recette et les limites de cette correction figurent dans [CORRECTION_PASSERELLE_FRONT.md](../../historique/CORRECTION_PASSERELLE_FRONT.md).

Le moteur ajoute avec `+ipv4.routes` et `ip route add`. Il ne remplace ni ne supprime une ancienne route contradictoire, ne vide aucune table et n’utilise ni `connection up`, ni `device reapply` en exploitation. Les paramètres de profil en attente, notamment DNS, ne sont pas activés. Les adresses, règles, passerelle et magasins sont relus avant écriture et après application ; les routes étrangères doivent rester identiques.

Pour l’IPv4 ADMIN dynamique, la comparaison ignore uniquement les secondes restantes lorsque `valid_lft` et `preferred_lft` sont tous deux finis et strictement positifs. L’interface, le préfixe, les attributs de l’adresse, l’expiration du bail ainsi que les autres adresses restent contrôlés. Le journal conserve la sortie native complète avec sa somme de contrôle et son format reste utilisable pour la reprise des opérations déjà en attente.

Les plages réservées, les réseaux ADM/BACK, les adresses BACK des pairs et le client SSH IPv4 réel sont protégés. Les formes IPv4 mappées en IPv6 du client SSH sont également reconnues. Les clients directement connectés à FRONT ne nécessitent pas de route via une passerelle. Les règles IPv4 doivent être les trois règles standard des tables `local`, `main`, `default` : VRF, routage par politique et multipath nécessitent une gestion réseau spécifique.

## Exclusion sans verrou périmé

Routes et management partagent `/var/lib/Bind9-Ansible/operations.flock`, fichier **vide**, root `0600`, dans un dossier root `0700`. L’exclusion appartient au processus via `flock`, puis disparaît à sa fin, y compris après un arrêt brutal. L’existence du fichier n’est jamais interprétée comme une opération active ; ne pas le supprimer. Un processus actif concurrent reçoit un refus explicite et peut être relancé après la fin du premier.

Le dossier historique `.operation.lock` reste ignoré par défaut hors hibernation. `bind_operation_lock_enabled: true` réactive sa coopération historique dans le garde de déploiement ; ce réglage reste `false` par défaut. Le sceau d’hibernation `.deployment.lock` conserve son fonctionnement. Les déploiements DNS complets et interventions root doivent toujours être organisés dans une fenêtre exclusive : un éditeur root ne coopérant pas avec l’exclusion peut agir entre deux contrôles.

## Échec et reprise

Pour un journal antérieur où seuls des ajouts persistants ont été acquittés, la reprise accepte une passerelle FRONT actuellement absente uniquement après correspondance exacte des deux magasins et du contexte réseau. Elle retire alors ces seuls ajouts persistants prouvés. Cette exception ne permet ni retrait de route noyau ni validation ou commit sans passerelle observée.

Pendant une transaction FRONT, le signal `SIGHUP`, notamment émis lors d’une fermeture de session SSH, emprunte le même rollback gardé que `SIGINT` ou `SIGTERM`. Les codes `129`, `130` et `143` identifient ces interruptions ; un échec du nettoyage peut renvoyer `1`. Aucun de ces codes ne signale une réussite. Si les contrôles de propriété empêchent la reprise, le journal reste disponible pour `routes-recover`. Un arrêt non interceptable ou une coupure machine exige également l’inspection du journal avant reprise.

Lors d’un appel direct du helper, `--ssh-peer` exige une valeur : son absence produit un refus explicite avant création de l’état réseau. Une chaîne vide explicitement fournie reste acceptée pour une invocation locale.


Une opération qui écrit prépare d’abord `/var/lib/Bind9-Ansible/front-routes.pending/`, avec instantanés, opérations ordonnées, progression et SHA-256. Ce dossier contient des données de reprise ; il ne représente pas un processus vivant. Après réussite isolée, son répertoire complet est renommé atomiquement vers `/var/lib/Bind9-Ansible/.front-routes.retired.<jeton>.<suffixe>`. Les neuf fichiers et leurs inodes sont conservés, avec répertoire root `0700` et fichiers root `0600` : une erreur au milieu de suppressions individuelles ne peut plus détruire les métadonnées de reprise. Dans un déploiement DNS complet, le journal actif reste conservé jusqu’aux contrôles DNS puis committé, ou repris par le rollback du déploiement.

Avant le point de commit, une erreur de renommage, de synchronisation ou une interruption interceptée déclenche une remise en place du répertoire complet, contrôlée par son identité et sans écrasement d’un objet apparu au chemin actif. Le rollback conserve ses contrôles exacts des magasins. Si cette compensation rencontre elle-même un conflit ou une panne, les chemins à inspecter sont signalés ; les neuf données de reprise n’ont pas été effacées. Organiser une intervention exclusive et vérifier les identités, les empreintes et l’état réseau avant de réconcilier le chemin actif. Un journal clos n’est jamais choisi automatiquement pour annuler une transaction déjà committée.

Les preuves closes ne sont pas purgées automatiquement. Un objet apparu au chemin de destination après libération de la réservation reste conservé, même si son inode reprend le numéro de la réservation ; une réservation incomplète peut également rester disponible pour inspection. Leur conservation est bornée à **4096 entrées portant ce préfixe avant ouverture d’une nouvelle transaction écrite** ; atteindre cette limite provoque un refus avant préparation et avant toute modification de route. Les plans, les routes déjà à jour et la reprise d’un journal actif restent possibles. L’opérateur archive les seuls journaux clos vérifiés, avec contrôle d’intégrité et rétention appropriée, sous le verrou commun dans une fenêtre exclusive. Ne pas retirer le journal actif, le fichier d’exclusion ni une preuve en cours d’inspection.

Le chargement exige des fichiers bornés, sans octet NUL et terminés par un seul LF. Métadonnées et progression contiennent exactement un enregistrement ; leurs champs sont vérifiés intégralement. Les clients restent limités à 512 entrées et les opérations à 1024. L’union exacte des clients est recalculée, y compris les sous-réseaux agrégés ; chaque opération doit correspondre à une destination de cette union, avec une identité unique par magasin et destination. Une métrique active héritée de zéro reste acceptée. Une donnée malformée provoque un refus sans retrait de route ni effacement du journal.

Une opération différée doit fournir exactement une ligne `transaction:` avec un UUID canonique, en LF ou CRLF. Si ce reçu est absent, malformé ou dupliqué, le rôle échoue explicitement, conserve l’état pending et ne réutilise aucun ancien jeton. La reprise devient alors une intervention explicite après inspection du journal. Voir [DURCISSEMENT_FRONT_ACTUEL.md](../../historique/DURCISSEMENT_FRONT_ACTUEL.md).

Un échec pris en charge déclenche le retrait des seuls ajouts confirmés, après contrôle des deux magasins. Une modification manuelle, une identité inattendue, un fichier de journal ajouté, une corruption ou une issue de commande rendue ambiguë par un crash empêchent toute suppression hasardeuse. Le journal reste présent avec une cause explicite. La reprise protège aussi le client SSH de la connexion courante, qui peut être différent de celui de l’opération initiale.

Après inspection du journal et du réseau, et après vérification qu’aucune autre opération ou intervention n’est en cours :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --limit site1-dns01 --tags routes-recover
```

La commande reprend uniquement le journal `front-routes.pending/`. Elle ne supprime pas un journal absent ou dangereux pour repartir artificiellement. Un conflit nécessite une réconciliation opérateur des seules routes concernées. Ne pas effacer les journaux pour forcer une exécution.

Avec le seul tag `routes-recover`, le rôle ne recompile pas les ressources DNS
et ne réclame ni les paramètres clients/DNSSEC courants ni des faits FRONT/BACK
conservés par un appel précédent. La découverte DNS des deux pairs est ignorée.
Le moteur de reprise conserve ses propres observations noyau, la protection
d'ADMIN, les contrôles du journal et le refus d'une restauration ambiguë ou
incomplète. Cette séparation ne réduit aucun contrôle du déploiement DNS.

Une restauration de BIND n’annule pas des routes déjà committées. Retirer un client de l’inventaire ne supprime pas sa route historique : le fonctionnement reste additif.

L’initialisation concurrente du dossier d’état est acceptée seulement si le dossier apparu est ensuite reconnu root `0700`, sans lien ; aucun droit existant n’est corrigé aveuglément. Les preuves, limites et parties impactées figurent dans [VERIFICATION_RESILIENCE_PERFORMANCE.md](../../qualification/VERIFICATION_RESILIENCE_PERFORMANCE.md). La [refonte antérieure](../../historique/REFONTE_ROUTES_MANAGEMENT.md) conserve sa traçabilité. La recette sur vos serveurs RHEL reste nécessaire.



<a id="recette-du-tag-routes-seul"></a>
## Campagnes et qualification

Les [essais du tag routes seul](../../historique/CAMPAGNES_CONFIGURATION.md#routes-seules) sont historiques. La [qualification actuelle](../../qualification/QUALIFICATION_ACTUELLE.md) sépare les preuves conservées des recettes natives encore nécessaires.

---

Documentation DNSZIBO · contenu harmonisé le 25 novembre 2026.
