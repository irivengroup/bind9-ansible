# Management de plusieurs ressources et découverte de l’existant

**Mode temporaire actuel :** `bind_operation_lock_enabled: false` désactive la prise du verrou commun `.operation.lock` pour les opérations ordinaires, lectures et simulations de management comprises. Un ancien verrou est ignoré et conservé. **Une seule opération à la fois**, y compris toute intervention manuelle. Avec le booléen YAML `true`, les garanties de verrou coopératif décrites ci-dessous s’appliquent de nouveau. Le tag `hibernate` et le déploiement complet avec `hibernate_deployment: yes` conservent toujours ce verrou. Les journaux de reprise, contrôles de checksum et verrous propres aux routes/sauvegardes restent actifs. [Contrat et preuves de cette révision](VERROUS_OPERATIONS_OPTIONNELS.md).

Le format unitaire existant reste disponible. Cette révision ajoute `action: list` aux ressources `option`, `zone` et `record`, ainsi qu’une enveloppe `resource: batch` avec `action: read` ou `apply`. Un lot applique des actions ordonnées sur plusieurs types de ressources dans une transaction locale commune.

Le [durcissement de l'intégrité live](INTEGRITE_CONFIGURATION_LIVE.md) s'applique aux lots et opérations unitaires. `create` n'est jamais un upsert : options et zones existantes sont refusées, et tout nom de record déjà présent dans sa zone est refusé quel que soit son type DNS. Les autres ressources manuelles sont conservées ; un conflit observé impose une nouvelle lecture.

Les [protections I/O et limites de données](FIABILITE_DONNEES_ET_IO.md) s’appliquent aussi aux lectures et aux lots. Utiliser `ansible.cfg` livré pour refuser les clés YAML dupliquées avant les tâches.

## Préconditions et garanties

Utiliser le contrôleur Linux/Bash, depuis la racine du projet, avec une paire déjà déployée et les prérequis du [guide de management](MANAGEMENT.md). Les exemples emploient `site1-dns01` et `site1-dns02`, dans le groupe `site1`. Adapter les adresses, domaines et noms d’hôtes avant exécution. Aucun paquet ou service supplémentaire n’est requis.

En mode strict (`bind_operation_lock_enabled: true`), toutes les observations prennent le verrou exclusif existant. Lecture, listing et simulation peuvent créer le répertoire d’état privé et prennent une lease temporaire ; ils ne publient pas de fichier DNS, ne créent pas de sauvegarde et ne rechargent pas BIND. Une lease interrompue bloque aussi ces actions et nécessite la reprise documentée.

## Lister les zones, avec leur type et leurs options

```bash
export ANSIBLE_CONFIG="$PWD/ansible.cfg"
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-management.yml \
  --limit site1 -e @examples/management/list-zones.yml \
  -e '{"bind_manage_show_values":true}'
```

Le fichier de requête contient :

```yaml
bind_manage:
  resource: zone
  action: list
  offset: 0
  limit: 100
```

Chaque élément de `items` fournit `name`, `type`, `source` et `options`. `type` reprend la déclaration réelle (`master`, `slave`, `forward`, etc.). `options` est une liste ordonnée de `{name, value}` utilisant la syntaxe BIND réellement déclarée. Le listing inclut les zones natives présentes dans les includes. Il ne crée pas de zones ni d’options implicites.

Sans affichage explicite des valeurs, `catalog` montre les noms, types, sources et noms d’options ; le contenu des valeurs reste masqué.

## Lister les options globales

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-management.yml \
  --limit site1 -e @examples/management/list-options.yml \
  -e '{"bind_manage_show_values":true}'
```

```yaml
bind_manage:
  resource: option
  action: list
  offset: 0
  limit: 100
```

Chaque élément fournit `name`, `value` et `source`. Les includes situés dans le bloc `options` sont développés en conservant leur provenance ; la directive `include` elle-même reste visible. Les défauts internes de la version BIND ne sont pas inventés ou présentés comme des déclarations explicites. Les options protégées du déploiement sont lisibles, mais restent interdites aux écritures de management.

## Lister tous les records d’une zone

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-management.yml \
  --limit site1-dns01 -e @examples/management/list-records.yml \
  -e '{"bind_manage_show_values":true}'
```

```yaml
bind_manage:
  resource: record
  action: list
  zone: site1.prod.example.test
  offset: 0
  limit: 100
```

Chaque ligne fournit `zone`, `name` pleinement qualifié, `type`, `ttl` déclaré/hérité et `value` RDATA. Deux A d’un même nom produisent deux lignes. Le SOA et les types DNSSEC sont lisibles. Il s’agit des données statiques du primaire, pas d’une interrogation du cache ou d’un transfert de la copie secondaire. Les zones dynamiques, formats non pris en charge et signatures restent soumis aux limites du guide principal.

## Pagination et cohérence des listings

Les listes sont triées de façon déterministe. Le résultat contient `total`, `count`, `offset`, `limit`, `next_offset` et le checksum de l’ensemble statique observé. `limit` vaut 100 par défaut, entre 1 et 500 ; `offset` est un entier positif ou nul. `next_offset: null` signifie qu’il n’existe pas de page suivante.

Pour éviter de mélanger plusieurs états pendant une pagination, conserver le checksum de la première page dans `bind_manage_checksums`, comme pour une lecture unitaire. Les pages suivantes le contrôlent avant de répondre. Préparer d’abord la révision de cette première page :


Créer `listing-revisions.json` à partir de la valeur réellement retournée, sans checksum fictif :

```bash
umask 077
read -r -p 'Checksum de la première page sur site1-dns01 : ' listing_checksum
[[ $listing_checksum =~ ^[0-9a-f]{64}$ ]] || exit 1
python3 - "$listing_checksum" > listing-revisions.json <<'PY'
import json, sys
json.dump({'bind_manage_checksums': {'site1-dns01': sys.argv[1]}}, sys.stdout)
PY
```

Puis demander, si nécessaire, la page commençant au record 101 :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-management.yml \
  --limit site1-dns01 \
  -e '{"bind_manage":{"resource":"record","action":"list","zone":"site1.prod.example.test","offset":100,"limit":100}}' \
  -e @listing-revisions.json -e '{"bind_manage_show_values":true}'
```

Si l’état change, la page est refusée : recommencer la pagination après examen du nouvel état. Un checksum de listing peut servir à une écriture unitaire ; un lot exige en plus sa propre prévisualisation et son empreinte de plan.

## Définir un lot mixte

Le fichier [batch.yml](../examples/management/batch.yml) contient un exemple complet : mise à jour d’option, création d’une zone autoritative, création de ses records A/TXT, puis création d’une zone forward. Son champ `action` utilise la variable d’exemple `bind_manage_batch_action`, avec `read` par défaut, pour garder exactement la même liste d’opérations entre lecture et application.

```yaml
bind_manage:
  resource: batch
  action: '{{ bind_manage_batch_action | default("read") }}'
  operations:
    - resource: option
      action: update
      name: minimal-responses
      value: 'yes'
    - resource: zone
      action: create
      name: service.example.test
      kind: authoritative
      serial: 1
      ttl: 300
    - resource: record
      action: create
      zone: service.example.test
      name: app
      type: A
      ttl: 300
      values: ['192.0.2.80', '192.0.2.81']
```

L’ordre est explicite : créer une zone avant ses records. Les champs de chaque opération suivent le contrat unitaire existant. Les opérations internes acceptent uniquement `create`, `update` et `delete`, sans checksum individuel. Le lot contient de 1 à 64 opérations ; sa liste JSON canonique est limitée à 256 Kio. Listings et lots limitent la découverte à 512 assets observés et 64 Mio au total, en conservant la limite de 16 Mio par fichier et 128 includes existante.

## Prévisualiser et approuver le lot exact

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-management.yml \
  --limit site1 -e @examples/management/batch.yml \
  -e '{"bind_manage_show_values":true}'
```

`read` prépare le lot en mémoire et vérifie le candidat final avec BIND. Il retourne les états `before` et `after` de chaque sélection, les chemins prévus, `would_change`, `checksum`, `plan_checksum`, `batch_digest` et `operations_count`. Pour une zone créée dans le lot, ses nouveaux records ont initialement `found: false` et `zone_found: false`.

L’état `before` est celui du début de la transaction ; `after` décrit le candidat final. Les effets nécessaires sur SOA, délégations, glue et marqueurs privés DNSSEC sont inclus. Les valeurs restent masquées par défaut. Examiner explicitement les valeurs avant de recopier les empreintes.

Saisir les deux empreintes de chacun des hôtes :

```bash
umask 077
read -r -p 'Checksum de site1-dns01 : ' primary_checksum
read -r -p 'Plan checksum de site1-dns01 : ' primary_plan
read -r -p 'Checksum de site1-dns02 : ' secondary_checksum
read -r -p 'Plan checksum de site1-dns02 : ' secondary_plan
for revision in "$primary_checksum" "$primary_plan" "$secondary_checksum" "$secondary_plan"; do
  [[ $revision =~ ^[0-9a-f]{64}$ ]] || exit 1
done
python3 - "$primary_checksum" "$primary_plan" "$secondary_checksum" "$secondary_plan" > batch-approvals.json <<'PY'
import json, sys
json.dump({
    'bind_manage_checksums': {'site1-dns01': sys.argv[1], 'site1-dns02': sys.argv[3]},
    'bind_manage_plan_checksums': {'site1-dns01': sys.argv[2], 'site1-dns02': sys.argv[4]}
}, sys.stdout)
PY
```

Le checksum d’état couvre les fichiers réels. Le checksum de plan lie cet état, la liste ordonnée d’opérations, le candidat final et le contexte du site. Changer une valeur, l’ordre des opérations ou les identités du site exige une nouvelle prévisualisation. Ces empreintes contrôlent la cohérence ; elles ne sont pas des signatures d’autorisation contre root.

## Simuler puis appliquer

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-management.yml \
  --limit site1 -e @examples/management/batch.yml \
  -e bind_manage_batch_action=apply -e @batch-approvals.json --check

ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-management.yml \
  --limit site1 -e @examples/management/batch.yml \
  -e bind_manage_batch_action=apply -e @batch-approvals.json
```

Ne lancer l’application qu’après réussite et examen de la simulation. Le playbook effectue d’abord une prévalidation sans publication sur tous les hôtes ciblés ; une erreur de checksum ou de candidat sur le secondaire arrête le parcours avant l’écriture du primaire. Il recontrôle ensuite chaque hôte au moment de son application.

Sur chaque serveur : une lease uniquement en mode strict, un candidat final, une sauvegarde complète et un journal ciblé, publication gardée de tous les fichiers concernés, labels, un rechargement BIND, réconciliation DNSSEC et expiration de chaque suffixe modifié, puis contrôles UDP/TCP/SOA. Les fichiers primaires existants avancent d’un seul serial par lot ; une nouvelle zone conserve son serial initial demandé, même si ses records sont ajoutés dans le même lot. Un lot sans différence ne crée pas de sauvegarde et ne recharge pas le service.

Les records s’écrivent uniquement sur le primaire. Sur le secondaire, ces opérations sont explicitement marquées `skipped: true`, `reason: primary-only` ; les options et déclarations de zones du lot sont appliquées. Un lot contenant des records doit inclure le primaire avant le secondaire, avec le même lot et la même phase. Le secondaire contrôle ensuite les serials reçus du primaire.

Après application, utiliser les listings ou les lectures unitaires pour vérifier l’état. Ne pas rejouer automatiquement un lot contenant des `create` : les ressources existent désormais et leur recréation est volontairement refusée.

## Refus et reprise

- Une ressource ne peut être ciblée qu’une fois dans le lot, après normalisation du nom et du type. Plusieurs RRsets distincts d’une même zone sont admis, ainsi qu’une action sur cette zone et ses records.
- Supprimer une zone et opérer sur ses records dans le même lot est refusé. Les doublons, champs inconnus, lots imbriqués, actions internes de lecture, chemins non observés et limites dépassées sont refusés.
- Un fichier primaire partagé par plusieurs zones ne peut être modifié ou purgé sans migration explicite ; cela protège les autres autorités, y compris en CRUD unitaire. Son listing reste disponible.
- Les protections des zones natives, options de sécurité, données signées, fichiers, permissions, ACL, TSIG et interventions manuelles restent celles du management unitaire. Le lot ne gère ni réseau, ni ACL, ni clés, ni logs.
- Une erreur de planification ou de validation ne publie aucune opération du lot. Une erreur après publication déclenche le rollback des fichiers de tout le lot sur cet hôte. Un fichier modifié par un tiers est conservé ; le journal passe à `recovery-required` et la lease reste présente si la restauration est incomplète.

Les journaux privés conservent les empreintes de lot et de plan ainsi que le nombre d’opérations, sans recopier les valeurs de requête dans le manifeste. Les sauvegardes de fichiers peuvent contenir des secrets ; leur accès reste root privé. La procédure de reprise et de rétention est celle de [MANAGEMENT.md](MANAGEMENT.md#transaction-sécurité-et-reprise).

La transaction reste **par hôte et par fichier**, sans transaction distribuée de la paire. Une panne ou une intervention après la prévalidation peut encore provoquer un échec du secondaire après réussite du primaire ; aucune restauration automatique du primaire déjà committé n’est annoncée. Le verrou n’exclut pas un éditeur root externe ; conserver une fenêtre d’exploitation exclusive. Le management ne réconcilie pas l’inventaire ; un déploiement forcé ultérieur peut écraser ces changements.
