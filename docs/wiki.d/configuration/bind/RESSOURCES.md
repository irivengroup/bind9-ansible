# Ressources de déploiement et de gestion

[Documentation](../../../index.md) › [Configuration](../index.md) › [Configuration BIND](index.md) › Ressources

[Retour au Parent](index.md)

## Sommaire

- [Déclaration unique](#declaration-unique)
- [Types de ressources](#types-de-ressources)
- [Exemple complet](#exemple-complet)
- [Gestion ultérieure](#gestion-ulterieure)
- [Migration et reprise](#migration-et-reprise)

<a id="declaration-unique"></a>
## Déclaration unique

`bind_resources_deploy` remplace les deux anciennes listes `bind_authoritative_zones` et `bind_forward_zones`. Définir la même requête pour les deux serveurs d'un site, normalement dans `group_vars/site1.yml`. La déclaration décrit un état neuf, construit à partir de la configuration de base protégée du rôle.

Le contrat reprend les ressources et les champs de [`bind_resources_manage`](../../exploitation/MANAGEMENT.md) : `option`, `acl`, `logging`, `zone`, `record`, ou une enveloppe `batch` contenant `operations`. Les variantes de déclaration de zone, avec `name` ou avec un objet `zone`, sont communes aux deux usages. Une opération utilise un seul format.

Un lot de déploiement emploie `action: create` ou `action: apply`. Les opérations enfants portent leur propre action `create`, `update` ou `delete`, selon l'existence de la ressource dans la nouvelle base. Ce lot n'est pas une série de commandes sur l'état live. Les zones sont déclarées avec `create` avant leurs enregistrements. Les ressources de base déjà présentes, dont les options usuelles et `logging`, se personnalisent avec `update`.

Par défaut, la requête est `{resource: batch, action: create, operations: []}` : aucune ressource personnalisée. Une liste vide est admise uniquement pour ce déploiement explicite. Un lot non vide est limité à **64 opérations et 256 Kio**, comme la gestion. Les lectures et listings appartiennent au playbook de gestion. Les mêmes ressources ne peuvent pas être ciblées deux fois dans un lot ; les doublons de suffixes ou de RRsets, y compris leurs variantes de casse, sont refusés.

## Types de ressources

| Ressource | Champs et comportement |
|---|---|
| `option` | `name`, `value` pour créer ou modifier une valeur BIND ; pas de point-virgule final. Les options de la base existent déjà : utiliser `update`. Les options de sécurité, chemins et runtime protégées restent réservées aux variables coordonnées du rôle. |
| `acl` | `name`, `value` contenant le corps BIND de l'ACL. Les ACL natives et celles de sécurité du rôle restent protégées. Le validateur BIND résout aussi les références des ACL non utilisées. |
| `logging` | Singleton `name: logging`, `value` contenant le corps du bloc. Utiliser `update` pour remplacer la journalisation de base. Les fichiers restent des enfants directs de `/var/log/named` ; liens et objets dangereux sont refusés. |
| `zone` autoritaire | `kind: authoritative`, `name`, `serial` et `ttl` facultatifs, ou objet `zone: {domain, serial, ttl, records}`. Les données initiales comprennent SOA, NS et les adresses des deux serveurs. Le format imbriqué conserve les champs de la déclaration de zone de gestion. |
| `zone` forward | `kind: forward`, `name`, `policy: only`, `forwarders` et `dnssec_validate` facultatif, ou objet `zone` équivalent. Les boucles de forwarding, zones natives et recouvrements dangereux sont refusés. |
| `record` | `zone`, `name`, `type`, `values` et `ttl` facultatif. `values` est une liste de chaînes RDATA, y compris pour plusieurs adresses. Les présentations TXT gardent leurs guillemets BIND. Le TTL du RRset est conservé. SOA et données de signature se gèrent par leurs mécanismes coordonnés. |

Les contrôles complets `named-checkconf -z`, `named-checkzone`, les ACL de transfert, les réponses DNS et la cohérence SOA primaire/secondaire restent obligatoires. Les types DNS ordinaires supplémentaires admis par la gestion sont soumis au parseur BIND natif. Les adresses A/AAAA déclarées conservent la génération automatique des zones inverses /24 et /64. La sélection des interfaces et `bind_trusted_networks` restent indépendantes de la liste de ressources.

Les trois fichiers natifs `named.localhost`, `named.loopback` et `named.empty` restent la propriété du paquet BIND. Le moteur lit les liens RPM exacts vers `../../usr/share/named/`, déjà admis par le contrôle du rôle, sans les remplacer. Il vérifie le propriétaire, les ancêtres, les permissions et les inodes de la cible et du lien ; les autres liens et les écritures d'enregistrements dans ces fichiers restent refusés. Cette prise en charge est nécessaire aux paquets récents de Rocky 10, y compris pour personnaliser options, ACL et journalisation lors du déploiement.

Les alias numériques de types DNS désignent la même ressource pour le dédoublonnage et les refus de signature ; leur identité provient du [registre IANA des types DNS](https://www.iana.org/assignments/dns-parameters/dns-parameters.xhtml#dns-parameters-4), figé pour cette livraison. La présentation RDATA transmise reste conservée, y compris le format générique RFC 3597. Les adresses A/AAAA encodées sous cette forme sont décodées uniquement pour les métadonnées et les PTR ; la validation native reste obligatoire.

## Exemple complet

Les adresses ci-dessous sont documentaires. Adapter les serveurs amont avant utilisation. L'[exemple copiable](../../../../examples/deployment/batch.yml) contient les cinq types de ressources.

```yaml
bind_resources_deploy:
  resource: batch
  action: create
  operations:
    - resource: option
      action: update
      name: max-cache-size
      value: 128m
    - resource: acl
      action: create
      name: monitoring
      value: '192.0.2.40;'
    - resource: logging
      action: update
      name: logging
      value: |
        channel operational {
            file "/var/log/named/operational.log" versions 5 size 10m;
            severity info;
            print-time yes;
        };
        category default { operational; };
        category security { operational; };
        category queries { null; };
    - resource: zone
      action: create
      name: service.example.test
      kind: authoritative
      ttl: 300
      serial: 1
    - resource: record
      action: create
      zone: service.example.test
      name: app
      type: A
      ttl: 300
      values: ['192.0.2.80', '192.0.2.81']
    - resource: record
      action: create
      zone: service.example.test
      name: description
      type: TXT
      ttl: 300
      values: ['"service de demonstration"']
    - resource: zone
      action: create
      name: private.example.test
      kind: forward
      policy: only
      dnssec_validate: false
      forwarders:
        - address: 10.11.0.53
          port: 53
        - address: 10.11.0.54
          port: 53
```

Le `serial: 1` reste celui de l'état initial même quand plusieurs enregistrements sont construits. Les ressources sont compilées avant le provisioning. Les options, ACL et journaux sont ensuite planifiés contre la base générée et ses véritables fichiers natifs, dans la transaction DNS protégée. Aucun changement de configuration ne provient du filtre exécuté sur le contrôleur.

```bash
ansible-playbook -i inventory/dev/hosts.ini playbooks/bind9-deploy.yml \
  -e @examples/deployment/batch.yml --tags deploy
```

<a id="gestion-ulterieure"></a>
## Gestion ultérieure

`bind_resources_manage` reste une requête ciblée sur une installation existante. Les lots se prévisualisent avec `action: read`, puis s'appliquent avec `action: apply` et les empreintes d'état et de plan obtenues. Ces garde-fous ne sont pas remplacés par l'enveloppe de création d'un déploiement neuf. Ne pas rejouer une création de zone sur une zone existante : utiliser l'action adaptée.

Les champs d'une opération sont identiques dans les deux variables. Pour conserver une reconstruction fidèle après des modifications de gestion, reporter l'état final voulu dans `bind_resources_deploy`, dans la même source d'inventaire. Le playbook de gestion ne réécrit pas l'inventaire du contrôleur. Consulter les [lots et listings](../../exploitation/MANAGEMENT_LOTS_ET_LISTES.md) pour les phases de prévisualisation et d'application.

## Migration et reprise

Transformer chaque ancien objet de zone en opération `resource: zone`, `action: create`, `kind: authoritative` ou `forward`, avec l'objet sous `zone`. Les enregistrements imbriqués existants restent acceptés ; ils peuvent aussi devenir des opérations `record` avec `values`. Supprimer les deux anciennes variables : leur présence provoque un refus explicite, même si `bind_resources_deploy` est également définie. Aucun repli silencieux n'est prévu.

Le déploiement complet conserve sa sauvegarde préalable, la restauration DNS/runtime et le rollback des routes de son opération. Un refus de personnalisation ou du validateur natif déclenche ce chemin d'échec. Une reprise incomplète conserve ses preuves et bloque la réussite. Les limitations des interventions partielles par tags restent décrites dans les [opérations](../../exploitation/OPERATIONS.md) : elles ne constituent pas une reconstruction complète.

Pour revenir au contrat précédent, restaurer ensemble les sources et l'inventaire de la livraison précédente, puis suivre sa procédure de restauration. Le simple ajout des anciens noms dans le nouvel inventaire n'est pas une migration.
