# Exemple : deux zones forwardées et liste cliente unique

Rendu du template livré pour le primaire FRONT `192.0.2.11`, BACK `10.20.1.11` ; pair secondaire FRONT `192.0.2.12`, BACK `10.20.1.12`. La liste `bind_trusted_networks.cloud_clients` contient `198.51.100.0/24` et `bind_trusted_networks.internal_clients` vaut `[]`. La fusion calculée `bind_allowed_clients` alimente l’ACL cliente commune `user_defined_clients`. La récursion BACK n’est donc pas activée dans cet exemple cloud seul.

Les rendus de ce dossier illustrent deux adresses d’écoute par serveur. Pour une interface ou une écoute unique, choisir d’abord [la paire host_vars adaptée](../interfaces/README.md), puis laisser le rôle rendre la configuration ; ne pas recopier ces IP de rendu dans une autre topologie.

- [variables.yml](variables.yml) déclare deux zones privées non signées, avec `dnssec_validate: false` explicite. Les deux listes d’entrée acceptent IP IPv4 et CIDR ; l’une peut être vide, jamais les deux. La fusion n’est pas une variable à déclarer.
- [named.conf](named.conf) est le rendu primaire ; [named-secondary.conf](named-secondary.conf) est le rendu secondaire. Chacun utilise ses propres ACL FRONT/BACK pour l'écoute et `allow-query-on`. Ils sont sans vues, DNSSEC en mode `auto` et avec écoute IPv4 uniquement, sans limiter les échanges sortants IPv4/IPv6 ; les fichiers natifs sont inclus. La clé TSIG affichée est **publique, fictive et interdite en production** ; le rôle génère sa véritable clé.
- [empty-root.zone](empty-root.zone) conserve NXDOMAIN hors périmètre et les délégations des deux suffixes forwardés. Il n'y a aucune zone autoritative métier à transférer.

Ce sont des valeurs d'illustration, pas une configuration à copier telle quelle sur un serveur. Le parcours `deploy` fournit le canal RNDC administratif et le drop-in commun de reload RNDC, sans restriction de famille au lancement. Le mode `auto`, désormais par défaut, installe le helper, les hooks et le renouvellement des exceptions. Les deux forwards privés non signés portent `dnssec_validate: false` ; leurs exceptions limitées sont appliquées via RNDC. Utiliser le parcours complet : copier seulement named.conf ne suffit pas. Une surcharge explicite `bind_dnssec_active: false` désactive ces composants.

Le rendu a été contrôlé avec `named-checkconf -z` EL8 dans un répertoire temporaire, avec chemins adaptés ; aucun déploiement cible de ces adresses n'est revendiqué. Voir [DNSSEC et limites](../../docs/wiki.d/configuration/bind/DNSSEC.md), [README du projet](../../README.md) et [recette](../../docs/wiki.d/qualification/TESTS_FONCTIONNELS.md).

## Configuration commune du site

Le fichier ci-dessous ajoute une autorité au scénario ; les rendus statiques fournis dans ce dossier illustrent les deux forwards seuls et doivent être régénérés pour cette variante.

`inventory/prod/group_vars/site1.yml` — exemple complet avec **deux zones forwardées** et une autorité :

```yaml
---
bind_site_name: site1
bind_tsig_key_name: prod-site1-transfer
bind_tsig_algorithm: hmac-sha256
bind_tsig_secret_file: "{{ lookup('env', 'HOME') }}/.local/share/bind9-ansible/tsig/prod/site1.key"

bind_trusted_networks:
  cloud_clients:
    - 198.51.100.0/24
    - 203.0.113.25
  internal_clients: []

bind_dnssec_active: auto
bind_front_routes_enabled: false
bind_monitoring_enabled: false

bind_resources_deploy:
  resource: batch
  action: create
  operations:
    - resource: zone
      action: create
      kind: authoritative
      zone:
        domain: site1.prod.example.test
        ttl: 300
        serial: 2026101601
        records:
          - name: app
            type: A
            value: 192.0.2.101
    - resource: zone
      action: create
      name: corp.site1.prod.example.test
      kind: forward
      policy: only
      dnssec_validate: false
      forwarders:
        - address: 10.11.0.53
          port: 53
    - resource: zone
      action: create
      name: apps.site1.prod.example.test
      kind: forward
      policy: only
      dnssec_validate: false
      forwarders:
        - address: 10.11.0.54
          port: 53
```

Les forwarders doivent réellement servir le SOA des suffixes déclarés et répondre en UDP/TCP. Ces enfants forwardés sont délégués sous l’autorité parente. Si aucune autorité n’est nécessaire, ne déclarer aucune opération de création autoritaire dans `bind_resources_deploy`. Un [exemple rendu primaire/secondaire avec deux zones forwardées](README.md) est livré séparément.


