# Gestion ciblée de BIND et verrou de déploiement

`playbooks/bind9-management.yml` accepte une requête unitaire (`create`, `read`, `list`, `update`, `delete`) ou un lot ordonné de plusieurs types de ressources (`resource: batch`, `action: read` puis `apply`). Le format unitaire existant reste disponible. Voir [le guide des lots et listings](MANAGEMENT_LOTS_ET_LISTES.md) pour les empreintes de plan, la prévalidation de la paire et la découverte paginée. Il utilise les fichiers réellement présents sur le serveur. Il ne rend pas le template du déploiement et conserve le texte des autres clauses, commentaires et enregistrements.

Le déploiement reste déclaratif et reconstruit les fichiers gérés à partir de l’inventaire. Le management ne synchronise pas ces changements vers l’inventaire : avant un futur déploiement forcé, réconcilier les modifications à conserver dans les variables/templates, sauvegarder et revoir le résultat attendu.

## Prérequis et périmètre

- Paire déjà déployée avec ce projet, inventaire du site, primaire avant secondaire, SSH ADM et `become: true`.
- RHEL 8, Python cible 3.6.8, contrôleur Ansible 2.9 ; aucune collection ou bibliothèque Python supplémentaire en production. Le module et son utilitaire sont transférés par Ansible, sans installation persistante d’un nouveau service.
- `named`, `named-checkconf`, `dig`, `rndc`, `restorecon`, groupe `named` et outil privé `/usr/local/sbin/bind-configtool` déjà opérationnels.
- Configuration statique, sans vues, classe IN, répertoire `/var/named`. Fichiers ordinaires root, sans lien ni écriture groupe/autres ; parents protégés. Maximum 16 Mio par fichier et 128 fichiers de configuration inclus ; les zones plus volumineuses exigent un flux adapté. L’unique lien crypto-policy natif RHEL est lu avec contrôle de son emplacement et de sa cible.
- Le graphe des includes, l’ancre explicite `bindkeys-file` et les fichiers de toutes les zones primaires sont inclus dans la révision SHA-256. Les journaux de zones dynamiques, `update-policy`, `allow-update` actif, `inline-signing`, `DLZ` et les stockages externes entraînent un refus. Les clauses non ciblées restent conservées ; un format non pris en charge provoque un refus, sans conversion silencieuse.

Le module ne gère ni ACL, ni logging, ni clés, ni configuration réseau, ni variables clientes. Les options liées aux chemins, aux contrôles d’accès, aux écoutes, aux sources de transfert et au runtime DNSSEC appartiennent au déploiement et sont protégées : `directory`, `pid-file`, `session-keyfile`, `dnssec-validation`, `bindkeys-file`, `managed-keys-directory`, `allow-transfer`, `allow-update`, `allow-query`, `allow-query-on`, `allow-recursion`, `allow-recursion-on`, `allow-query-cache`, `allow-query-cache-on`, `listen-on`, `listen-on-v6`, `transfer-source`, `notify-source`. Les changer nécessite une modification cohérente des sources puis un déploiement forcé, avec recette.

## Lecture, checksum et écriture

Lecture et simulation prennent désormais la même lease exclusive que les écritures : le répertoire d’état privé peut être créé, puis le verrou temporaire est retiré à la sortie normale. Une opération active ou interrompue bloque aussi ces observations. Aucun fichier DNS n’est publié par une lecture ou une simulation.

La lecture renvoie `found` et `checksum`, sans écrire de configuration ni créer de sauvegarde. Avec affichage explicite, elle fournit la valeur BIND de l’option, la déclaration de zone et ses forwarders/ports/politique privée, ou les valeurs et TTL du RRset. Les valeurs de l’objet sélectionné restent masquées par défaut ; utiliser le **booléen YAML** `bind_manage_show_values: true` pour les afficher dans une session maîtrisée. Ne pas activer cet affichage pour une valeur sensible.

Depuis la racine du projet, lire une option sur le primaire :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-management.yml \
  --limit site1-dns01 -e @examples/management/read-option.yml \
  -e '{"bind_manage_show_values":true}'
```

Recopier le checksum de **cet hôte et cette lecture**, puis préparer la révision sans saisir de valeur fictive :

```bash
read -r -p 'Checksum SHA-256 retourné pour site1-dns01 : ' revision
[[ $revision =~ ^[0-9a-f]{64}$ ]] || exit 1
python3 - "$revision" > management-revision.json <<'PY'
import json, sys
json.dump({'bind_manage_checksums': {'site1-dns01': sys.argv[1]}}, sys.stdout)
PY
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-management.yml \
  --limit site1-dns01 -e @examples/management/update-option.yml \
  -e @management-revision.json --check
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-management.yml \
  --limit site1-dns01 -e @examples/management/update-option.yml \
  -e @management-revision.json
```

`--check` vérifie le checksum et le candidat avec BIND, sans publier, sauvegarder ou recharger. Il peut utiliser des fichiers temporaires privés, supprimés à sa sortie. Une écriture réussie fournit une nouvelle révision : refaire une lecture avant l’action suivante. Une modification manuelle d’un include ou d’une zone invalide aussi le checksum précédent. Le module le contrôle au début, après validation/sauvegarde, puis vérifie chaque cible juste avant sa publication.

Pour les options et les zones, cibler normalement le site entier afin de garder la paire cohérente. Faire la même lecture sur les deux hôtes et fournir les deux empreintes dans `bind_manage_checksums`, une clé par nom d’inventaire. `bind_manage.expected_checksum` convient à un seul hôte ; il n’existe pas de checksum commun à la paire. Les records s’écrivent uniquement sur le primaire ; le secondaire reçoit le transfert normal et son SOA est contrôlé lorsque les deux hôtes sont ciblés. La racine privée locale n’est pas comparée entre pairs.

Le playbook exécute les hôtes séquentiellement et s’arrête au premier échec. **La transaction est par hôte, pas atomique sur la paire** : un échec secondaire ne restaure pas automatiquement le primaire déjà modifié. Conserver les deux résultats et traiter l’écart avant une nouvelle opération.

## Contrat de la requête `bind_manage`

La requête doit être un dictionnaire à clés chaînes, avec un nom explicite. Les noms d’options sont en minuscules et suivent la syntaxe `[a-z][a-z0-9-]*`. Les entrées mal typées sont refusées avec un message contrôlé avant toute commande native.

| Champ | Type / usage |
|---|---|
| `resource` | Requête unitaire : `option`, `zone` ou `record` ; enveloppe de lot : `batch` |
| `action` | `create`, `read`, `update` ou `delete` |
| `name` | Nom de l’option, suffixe de zone ou propriétaire du RRset ; absent pour `list` |
| `expected_checksum` | Chaîne SHA-256 de 64 caractères hexadécimaux minuscules, obligatoire pour toute écriture ; surcharge possible par `bind_manage_checksums[inventory_hostname]` |
| `value` | Option : syntaxe BIND sous forme de chaîne, sans point-virgule final |
| `kind` | Création de zone : `forward` ou `authoritative` |
| `forwarders` | Zone forward : liste non vide de `{address: chaîne IP, port: entier optionnel}` ; port 53 par défaut |
| `dnssec_validate` | Booléen d’une zone forward ; `false` demande une exception privée limitée lorsque DNSSEC global est actif. Création : `true` par défaut ; mise à jour : politique existante conservée |
| `settings` | Mise à jour autoritative : dictionnaire option de zone → valeur BIND chaîne ; stockage, type, signature dynamique, transferts et maîtres protégés |
| `serial`, `ttl` | Création autoritative : entiers positifs, défauts 1 et 300. Pour un record, `ttl` accepte un entier ou un TTL BIND ; sinon TTL existant/hérité |
| `purge_data` | Suppression de zone : booléen, défaut `false`. `true` autorise uniquement le retrait du fichier primaire ciblé sous `/var/named/zones/` |
| `zone`, `type`, `values` | Record : suffixe, type DNS et liste de chaînes RDATA. `values` requis pour créer/modifier, pas pour lire/supprimer |

Les champs inconnus sont refusés. `create` refuse un objet existant ; `update`/`delete` refusent un objet absent. Un update sans différence ne recharge pas BIND. La validation native reste nécessaire pour la syntaxe et la sémantique de chaque option/RDATA.

### Options

```yaml
bind_manage:
  resource: option
  action: update
  name: minimal-responses
  value: 'yes'
```

Le checksum est fourni séparément comme dans la procédure précédente. Le listing peut le contrôler pour conserver la même révision entre pages ; un lot exige aussi `expected_plan_checksum` ou `bind_manage_plan_checksums[inventory_hostname]`. Remplacer `action` par `read` sans `value`, par `create` pour une clause absente, ou par `delete` sans `value`. La suppression rétablit le défaut BIND : vérifier l’effet de ce défaut pour votre version avant l’opération.

### Zone forward

Une adresse de forwarder est obligatoirement une chaîne IP explicite, jamais un entier ou un booléen. La diffusion limitée `255.255.255.255` est refusée. Les contrôles multicast, link-local et adresse non spécifiée s’appliquent aussi à l’adresse IPv4 contenue dans une adresse IPv6 mappée.

```yaml
bind_manage:
  resource: zone
  action: create
  name: private.example.test
  kind: forward
  dnssec_validate: false
  forwarders:
    - address: 10.11.0.53
      port: 53
    - address: 10.11.0.54
      port: 53
```

Le module ajoute `forward only` et les délégations NS/glue nécessaires dans le parent primaire local ou la racine privée. Il incrémente le SOA des fichiers primaires affectés. Un update remplace la liste `forwarders` et modifie éventuellement `dnssec_validate`. Un delete retire cette déclaration et ses seules délégations dérivées. Une délégation modifiée manuellement entraîne un refus de conflit ; le module ne l’écrase pas.

Les forwarders IPv4/IPv6 unicast sont admis. Les boucles locales ou vers la paire sur le port 53, adresses non spécifiées, multicast et link-local sont refusées. Le réseau sortant vers les upstreams doit fonctionner. Une zone forward ne peut ni masquer une autorité locale ni être imbriquée dans une autre zone forward. Un suffixe contenant déjà des données manuelles dans son parent exige une résolution explicite des conflits avant création.

En mode DNSSEC actif, le helper existant `bind-private-dnssec` réconcilie l’exception privée après rechargement ; son absence bloque l’action avant publication. Le timer existant continue le renouvellement. Le management ne désactive jamais globalement DNSSEC pour faire fonctionner une zone privée.

### Zone autoritative et records

```yaml
bind_manage:
  resource: zone
  action: create
  name: service.example.test
  kind: authoritative
  ttl: 300
  serial: 1
```

Sur le primaire : création d’une zone statique avec SOA, NS `ns1`/`ns2`, adresses FRONT de la paire et transferts/notifies BACK authentifiés par la clé TSIG existante du site. Sur le secondaire : déclaration slave et maître BACK authentifié. Aucun fichier préexistant n’est écrasé par une création. Les zones natives, leurs recouvrements et la racine privée sont protégés. Les fichiers de zone d’un include externe ne sont pas réécrits par une action zone.

Ajouter ensuite un RRset dans une **nouvelle exécution après lecture** :

```yaml
bind_manage:
  resource: record
  action: create
  zone: service.example.test
  name: app
  type: A
  ttl: 300
  values:
    - '192.0.2.80'
    - '192.0.2.81'
```

`name` accepte un nom relatif, `@` ou un FQDN terminé par un point, strictement dans la zone. `create` refuse désormais tout nom propriétaire déjà présent dans cette zone, même pour un autre type DNS. `update` remplace **tout le RRset** ; `delete` retire tout ce RRset. Une action ne crée pas automatiquement de PTR : demander une action distincte sur la zone inverse appropriée. Pour TXT, conserver les guillemets DNS dans la chaîne YAML, par exemple `values: ['"texte de service"']`. Les données AAAA restent admises malgré l’écoute DNS uniquement IPv4. Voir le [contrat de préservation live](INTEGRITE_CONFIGURATION_LIVE.md) pour les contrôles de concurrence et la procédure manuelle.

Le SOA avance une seule fois par fichier primaire affecté ; au-delà de 4294967295, le prochain serial est 1. Les données SOA et les types gérés par DNSSEC restent lisibles, mais ne sont pas modifiables comme records. Un fichier primaire contenant déjà des signatures ou clés de zone ne peut pas être modifié par ce flux statique : son processus de signature doit conserver cette responsabilité. Les fichiers statiques `$ORIGIN`, `$TTL`, SOA multilignes et propriétaires hérités sont pris en charge ; si supprimer/modifier une ligne changerait l’héritage d’une autre, le module refuse et demande de rendre ces propriétaires explicites. `$INCLUDE`, `$GENERATE`, propriétaires échappés, zones dynamiques et signatures en ligne exigent un autre flux de gestion. Des `$INCLUDE` de données non reproduits dans le candidat provoquent un refus du validateur, sans publication.

La suppression d’une zone conserve son fichier par défaut pour la reprise. `purge_data: true` s’applique uniquement au primaire : pour la paire, choisir soit un delete commun sans purge, soit deux exécutions distinctes (primaire avec purge, secondaire sans purge), chacune après lecture de son checksum. Une création ultérieure au même chemin refuse le fichier conservé, y compris une ancienne copie secondaire dont le serial pourrait empêcher le nouveau transfert : inspecter et archiver ces fichiers avant de décider de leur retrait. Le management ne nettoie pas arbitrairement les anciennes copies secondaires. La santé d’une zone autoritative créée/modifiée exige un SOA local avec le drapeau autoritatif ; lorsque la paire est ciblée, le contrôle secondaire vérifie en plus le serial reçu du primaire.

## Transaction, sécurité et reprise

Les contrôles DNS de management exigent le statut `NOERROR`, le nom exact et le type de la réponse. Pour les SOA locaux, le drapeau autoritatif et le serial attendu sont aussi contrôlés. Une réponse pour un autre nom, même avec la même adresse ou le même serial, ne prouve pas la santé de la zone demandée.

Un verrou existant, même sous forme de fichier ou de lien pendant, provoque un refus. Si le verrou acquis disparaît ou est remplacé avant sa libération, le module signale le conflit ; il ne retire pas le verrou de remplacement. Aucun vol automatique de verrou n’est prévu. Ces contrôles ne remplacent pas une fenêtre exclusive pour les interventions root externes.

1. Lecture de l’état réel et contrôle de révision.
2. Calcul de la seule action et des conséquences nécessaires : délégations, glue, serial, marqueur privé DNSSEC.
3. Reproduction des fichiers statiques dans un répertoire temporaire privé, puis `named-checkconf -z -t ... /etc/named.conf`.
4. Sauvegarde complète par l’outil existant sous `bind_backup_root` et journal ciblé privé sous `/var/backups/Bind9-Ansible/management/transaction-*` : contenu antérieur des cibles, métadonnées, empreintes, archive complète et statut.
5. Nouveau contrôle de révision, publication atomique **par fichier**, application de `restorecon`, `rndc -s 127.0.0.1 reload`, réconciliation NTA si nécessaire, expiration du cache du suffixe lors d’une action zone et contrôles UDP/TCP localhost/SOA affectés. Chaque réponse a au plus cinq tentatives, espacées d’une seconde si elle n’est pas encore correcte ; le SOA autoritatif d’un secondaire dispose de dix tentatives espacées de deux secondes pour le transfert initial. `dig` utilise un délai de deux secondes et une seule tentative réseau.

Le verrou d’opération commun `/var/lib/Bind9-Ansible/.operation.lock` (répertoire root 0700) exclut les déploiements DNS et management concurrents. Les parcours de routes isolées gardent leur propre verrou FRONT ; les sauvegardes gardent leur verrou d’archive. Il n’arrête pas un éditeur manuel ou un autre outil lancé par root : organiser une fenêtre exclusive. Les contrôles de révision détectent les conflits observables ; ils ne rendent pas une publication multi-fichiers atomique face à un crash ou à une écriture root non coopérative.

Sur échec après publication, les seules cibles de cette transaction sont restaurées, avec leurs modes/propriétaires, labels et rechargement. Une cible modifiée par un tiers n’est pas écrasée pendant le rollback. Le journal porte `rolled-back` après restauration réussie ; `recovery-required` si elle reste incomplète, et le verrou est conservé. Un arrêt brutal peut laisser `prepared` et le verrou : **ne pas les supprimer automatiquement**.

Procédure d’incident : arrêter toute autre automatisation, identifier le journal de l’opération et lire son manifeste sous root ; comparer les empreintes avant/après et les fichiers présents ; conserver une copie de l’état courant ; restaurer explicitement les seules cibles nécessaires depuis les copies protégées, ou employer la restauration complète existante après arrêt de `named` selon le [README](../README.md#reprise). Exécuter `named-checkconf -z`, `restorecon`, recharger/redémarrer et recetter DNS/réplication. Supprimer le répertoire `.operation.lock` **uniquement après** reprise et vérification de l’absence d’opération active. Un manifeste illisible ou une incertitude impose une analyse manuelle.

Les sauvegardes ciblées ne sont pas purgées automatiquement : prévoir rétention, capacité disque, accès root et copie protégée selon votre politique. Elles peuvent contenir des secrets issus de `named.conf`. Les résultats de transport sont `no_log`; seul le message de refus contrôlé et les métadonnées d’exploitation sont affichés par défaut. Les sorties natives complètes ne sont pas publiées dans Ansible ; pour diagnostiquer un candidat refusé, contrôler la requête puis utiliser les outils BIND sous root dans un environnement de recette.

## Verrou après déploiement

Uniquement avec `hibernate_deployment: yes`, après un parcours complet et les contrôles de santé réussis, le rôle écrit atomiquement `/var/lib/Bind9-Ansible/.deployment.lock`, root 0600, dans un dossier 0700. Le JSON contient un manifeste de fichiers/empreintes SHA-256 et l’empreinte du manifeste, sans contenu de configuration. Ce sceau **n’est pas une signature ni un mécanisme contre root** ; sa présence évite une reconstruction accidentelle même après une intervention manuelle.

Tout nouveau déploiement ou tag de reconstruction refuse le sceau existant, même avec `hibernate_deployment: no` (défaut). Avant toute reconstruction, sauvegarder, réconcilier les changements manuels puis retirer explicitement le sceau selon [HIBERNATE_DEPLOYMENT.md](HIBERNATE_DEPLOYMENT.md). Pour reconstruire et verrouiller le résultat :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --limit site1 --tags deploy -e hibernate_deployment=yes
```

Le paramètre `force` est retiré et refusé, ainsi que les noms intermédiaires `deployment_lock` et `deployment_locked`. Une reconstruction sans sceau peut écraser les changements manuels et ceux du management ; la sauvegarde et le rollback existants conservent leur objet. Le sceau automatique exige un parcours complet réussi avec le verrouillage activé ; les tags partiels ordinaires et `--check` ne le créent pas. Le tag isolé `hibernate`, décrit ci-dessous, est un parcours distinct. Les sauvegardes et opérations de routes prennent aussi le verrou commun ; une reprise incomplète le conserve. Ne pas utiliser `--skip-tags always` ou `--start-at-task` pour contourner le flux ; les exécutions partielles ne constituent pas une recette complète.

Un sceau produit par une version précédente reste bloquant, sans migration automatique ni suppression. Si aucun sceau n’existe, le premier déploiement reconstruit les fichiers et ne crée un sceau qu’avec `hibernate_deployment: yes`. Sauvegarder et réconcilier les modifications manuelles **avant** cette mise à niveau. Une opération de management ne crée ni ne rafraîchit le sceau de déploiement ; sa révision courante est distincte du manifeste initial.

Pour protéger l’état existant sans cette reconstruction, utiliser `--tags hibernate -e hibernate_deployment=yes` : le tag dédié crée uniquement le sceau du fichier canonique live après contrôle de sécurité et de syntaxe, sans preuve de santé DNS. Les [détails et limites](HIBERNATE_DEPLOYMENT.md) distinguent ce parcours du scellement après déploiement complet.

Si les prérequis de ce guide sont déjà présents, le management peut être utilisé directement sur l’état existant, sans lancer préalablement une reconstruction. Il ne met pas à niveau les paquets ou assets runtime manquants ; un prérequis absent doit être traité explicitement avant une écriture qui en dépend.

## Organisation et validation

`tasks/main.yml` protège et appelle `tasks.d/deploy/main.yml`. `tasks/manage.yml` appelle les prérequis de site et `tasks.d/manage/main.yml`. Les tâches partagées restent à la racine de `tasks.d` : clients, DNSSEC, pairs, chemins, fichiers runtime, loopback et capture/restauration runtime. La configuration DNS, le routage et le garde de déploiement sont dans `deploy/` ; les algorithmes DNS/réseau existants sont conservés.

Les tests livrés couvrent CRUD, préservation, révisions périmées, fichiers dangereux, conflit de rollback, échec après renommage, transport Ansible et cycle du verrou de déploiement. Consulter [AUDIT_HIBERNATE_DEPLOYMENT.md](AUDIT_HIBERNATE_DEPLOYMENT.md) pour les résultats réellement exécutés et les contrôles restant à réaliser. Aucune disponibilité continue sous charge, politique SELinux réelle ni reprise après crash physique n’est certifiée par les tests de laboratoire.

Les résultats courants sont détaillés dans [AUDIT_HIBERNATE_DEPLOYMENT.md](AUDIT_HIBERNATE_DEPLOYMENT.md). Les [garanties I/O](FIABILITE_DONNEES_ET_IO.md) complètent ce guide. Les rapports [I/O](VALIDATION_FIABILITE_IO.md) et [lots/listes](VALIDATION_LOTS_ET_LISTES.md) conservent leur périmètre historique.
