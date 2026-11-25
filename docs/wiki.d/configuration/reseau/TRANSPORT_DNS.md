# Transport natif BIND et écoute DNS

[Documentation](../../../index.md) › [Configuration](../index.md) › [Réseau et interfaces](index.md) › Transport natif BIND et écoute DNS

[Retour au Parent](index.md)

## Sommaire

- [Forwarders](#forwarders)
- [Qualification](#qualification)

## Forwarders

`bind_resources_deploy.operations[].forwarders[].address` pour une opération de zone forward accepte une adresse littérale
unicast IPv4 ou IPv6. Les noms d’hôte, adresses non spécifiées, multicast,
link-local/scopées et IPv4 mappées dans IPv6 sont refusés. Les alias ambigus
ne doivent pas contourner les contrôles de boucles locales. Le port conserve
son contrat : entier ou chaîne décimale de 1 à 65535, jamais un booléen.
Les délégations privées utilisent A pour IPv4 et AAAA pour IPv6.

```yaml
bind_dnssec_active: auto
bind_resources_deploy:
  resource: batch
  action: create
  operations:
    - resource: zone
      action: create
      name: corp.example.test
      kind: forward
      policy: only
      dnssec_validate: false
      forwarders:
        - address: 192.0.2.53
          port: 53
        - address: 2001:db8::53
          port: 53
```

Adresses de documentation uniquement : remplacer par des endpoints réellement
servis. Cet exemple ne promet pas un ordre strict entre les forwarders ; BIND
gère sa sélection. Le réseau, les routes IPv6 sortantes et le pare-feu existent
déjà et restent sous votre gestion. Aucun réglage IPv6 système n’est modifié.
Les listes clientes et adresses FRONT/BACK restent IPv4 puisque l’écoute DNS
IPv6 est fermée. Le mode DNSSEC n’affecte pas ce choix de transport.


## Qualification

Consulter les [essais de transport archivés](../../historique/CAMPAGNES_CONFIGURATION.md#transport) et la [qualification actuelle](../../qualification/QUALIFICATION_ACTUELLE.md) avant une mise en production.

---

Documentation DNSZIBO · contenu harmonisé le 25 novembre 2026.
