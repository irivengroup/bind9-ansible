# Loopback et zones natives BIND

[Documentation](../../../index.md) › [Configuration](../index.md) › [Réseau et interfaces](index.md) › Loopback et zones natives BIND

[Retour au Parent](index.md)

## Sommaire

- [Retrait de la déclaration explicite](#retrait-de-la-déclaration-explicite)
- [Qualification](#qualification)

## Retrait de la déclaration explicite

Seule la ligne `empty-zones-enable yes;` est retirée de [named.conf.j2](../../../../roles/bind9/templates/named.conf.j2), ainsi que des rendus [primaire](../../../../examples/deux-zones-forwardees/named.conf) et [secondaire](../../../../examples/deux-zones-forwardees/named-secondary.conf). Le défaut natif reste actif ; aucune désactivation globale n’est introduite et DNSSEC conserve son défaut `auto`. Les tests [test_native_loopback.py](../../../../tests/test_native_loopback.py) et [integration_loopback.py](../../../../tests/integration_loopback.py), ce document et le README sont alignés. Les autres fichiers de production restent identiques à la livraison source ; la liste exacte figure dans `DELIVERY.json`.

Le test DNS vérifie en UDP et TCP une réponse NXDOMAIN autoritative pour `250.255.255.10.in-addr.arpa`, avec une SOA de `10.in-addr.arpa` : il distingue ainsi la zone vide automatique d’une réponse de la racine privée. Les comparaisons de noms DNS ignorent la casse. Les sources BIND et leur comportement natif sont décrits dans la [documentation ISC](https://kb.isc.org/docs/aa-00800).

Pour revenir à une déclaration explicite, rétablir la ligne `empty-zones-enable yes;` et appliquer la configuration après contrôle BIND ; cette valeur exprime le même comportement. Ne pas la remplacer par `no`, qui désactiverait les zones automatiques.

```bash
sudo named-checkconf -z /etc/named.conf
dig -4 -b 127.0.0.1 @127.0.0.1 localhost A +norecurse
dig -4 -b 127.0.0.1 @127.0.0.1 localhost A +tcp +norecurse
dig -4 -b 127.0.0.1 @127.0.0.1 1.0.0.127.in-addr.arpa PTR +norecurse
dig -4 -b 127.0.0.1 @127.0.0.1 1.0.0.127.in-addr.arpa PTR +tcp +norecurse
dig -4 -b 127.0.0.1 @127.0.0.1 localhost AAAA +norecurse
```

Attendu : NOERROR, AA, A `127.0.0.1`, PTR `localhost.` et AAAA `::1`. Le rôle contrôle automatiquement les quatre premières réponses locales, les SOA exacts des zones métier et les SOA forwardés en UDP/TCP. Le moniteur facultatif contrôle le loopback et la présence de SOA autoritatifs, sans comparaison des serials ni interrogation des zones forwardées ; demander `bind_monitoring_enabled: true` et le tag `monitor` pour son timer systemd.


## Qualification

Les [mesures historiques](../../historique/CAMPAGNES_CONFIGURATION.md#loopback) restent attribuées à leurs versions BIND. La [qualification actuelle](../../qualification/QUALIFICATION_ACTUELLE.md) ne les présente pas comme rejouées.

---

Documentation DNSZIBO · contenu harmonisé le 25 novembre 2026.
