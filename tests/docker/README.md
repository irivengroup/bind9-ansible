# Recette opérationnelle Docker EL8

Cette recette emploie deux nœuds Rocky Linux 8 avec de vrais systemd,
NetworkManager, firewalld, BIND/RNDC et connexions SSH. Elle vérifie les
composants canoniques, leurs refus, les paquets TCP/UDP et un redémarrage
Docker. Elle ne qualifie ni RHEL, ni SELinux enforcing, ni un reboot d'hôte.
Les deux playbooks publics conservent leurs contrôles de distribution.

Le laboratoire active aussi le moniteur périodique : il vérifie le timer,
exécute le vrai service avec `/usr/local/sbin/bind-healthcheck` avant et après
restart, puis compare les empreintes et permissions du script et de ses unités.
Les noms des unités systemd restent stables ; aucun second timer n'est créé.

Le lanceur nécessite Python 3.11 et Docker. Il crée trois réseaux internes,
deux nœuds et un contrôleur. Il ne publie aucun port, ne monte pas le socket
Docker et ne modifie aucun réseau, pare-feu ou paramètre du noyau de l'hôte.
Les ressources portent un identifiant et un label propres ; leur suppression
vérifie les deux. Les images et preuves sont conservées. Les clés privées
restent dans les conteneurs et sont détruites à leur suppression.

Le mode Python normal est obligatoire. Les options `-O`, `-OO` et
`PYTHONOPTIMIZE` provoquent un refus immédiat : les assertions de cette recette
doivent rester actives pour le nettoyage, les données privées et les paquets.

Depuis la racine du projet, avec les images immuables de recette déjà présentes :

```shell
python -B tests/docker/recipe.py --output /chemin/exterieur-au-projet/recette-unique
```

Le répertoire de résultats doit être nouveau et extérieur au projet. Les
valeurs par défaut désignent les images locales mesurées de ce laboratoire.
Sur un autre poste, construire le nœud avec `tests/docker/Dockerfile.el8`,
le contrôleur avec `tools/quality/Dockerfile.qa`, puis fournir leurs IDs exacts
avec les options `--node-image` et `--controller-image`. Ces IDs doivent
être réellement obtenus par `docker image inspect`. Le catalogue de paquets
EL8 peut évoluer : une reconstruction d'image doit être recensée et rejouée ;
aucune identité binaire avec l'image de référence n'est supposée.

La recette rend explicites trois dispositions de conteneur :

- `container=docker` permet à systemd 239 de reconnaître son environnement
  et de préserver les écritures après arrêt/redémarrage.
- Les MAC et UUID sont stables. `keep-configuration=no`, limité à FRONT et
  BACK, laisse NetworkManager activer ses profils persistants malgré les
  adresses préconfigurées par Docker. Le démarrage n'ajoute aucune route et
  n'exécute aucun `nmcli connection up` ou `device reapply`.
- Le backend firewalld du laboratoire est explicitement `iptables`.
  L'interface iptables de l'image utilise nftables. `IPv6_rpfilter=yes`, la
  règle native `rpfilter --validmark --invert -j DROP` et les rejets IPv4/IPv6
  restent exigés. Aucun backend ni contrôle du rôle de production n'est modifié.

Le noyau WSL de référence ne fournit pas `CONFIG_NFT_FIB_IPV6`, requis par
le backend nftables direct de firewalld 0.9. La recette ne désactive pas le
contrôle IPv6 pour contourner cette limite et n'exécute aucun `modprobe` ni
changement d'option WSL.

Le fichier `result.json` n'est produit qu'après les vérifications. Il doit être
accompagné de `cleanup.json` et des reçus de commandes, sans `failure.json`.
Une erreur conserve ses journaux et reste un échec. La persistance compare
les routes, profils actifs, octets de configuration, UUID, MAC, permissions,
services et filtres avant/après. Les tests de refus ADM utilisent un véritable
listener local témoin. Le rollback aval est éprouvé avant et après redémarrage.

Le contrôleur conserve séparément les données de commande dans `<label>.log`
et les diagnostics dans `<label>.stderr.log`, avec leurs empreintes dans
`events.json`. Un diagnostic SSH ne peut donc pas être interprété comme une
route JSON. Les commandes distantes désactivent les fichiers de démarrage
interactifs avec `--noprofile --norc` et conservent `-e` et `-u` ; un code de
sortie inattendu reste bloquant et ses deux flux sont conservés.

Les deux routes nouvellement créées passent du protocole d'origine `boot`
(implicite dans la sortie iproute2) au protocole `static` quand NetworkManager
réinstalle son profil. Cette seule transition est attendue explicitement :
aucune propriété n'est ignorée. Les routes préexistantes, chemins, passerelles,
métriques, doublons et fichiers restent comparés intégralement. Les contrôles
de protocole du journal de rollback pendant une transaction restent inchangés.

Avant l'ouverture DNS, des listeners témoins locaux existent sur les trois
interfaces : les refus TCP/UDP sont donc indépendants d'une absence de serveur.
Les témoins temporaires FRONT/BACK sont ensuite arrêtés et désactivés pour
laisser BIND utiliser le port 53. Le composant pare-feu canonique s'exécute
avant le démarrage et les vérifications de BIND, comme dans le déploiement.
Le témoin ADM reste actif et redémarre automatiquement pour vérifier le refus
DNS sur cette interface après redémarrage.

En cas d'interruption du lanceur, reprendre uniquement le nettoyage avec la
même option `--output`, complétée par `--cleanup-only`. Le lanceur contrôle
le format des noms et IDs enregistrés, puis l'ID et le label réels de chaque
ressource avant suppression. Une incohérence est bloquante ; aucune commande
globale de suppression ou de purge Docker n'est utilisée.
