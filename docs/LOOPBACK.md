# Loopback natif dans le template canonique

`roles/bind9/templates/named.conf.j2` produit `/etc/named.conf` et écoute sur 127.0.0.1, FRONT et BACK. La vue `local`, après `peer`, exige une source ET une destination 127.0.0.1. Elle inclut uniquement `/etc/named.rfc1912.zones`, fourni par le paquet RHEL, et partage les zones métier via `in-view "peer"`. Les suffixes forwardés sont déclarés dans cette vue ; les autres noms restent bloqués par la racine privée.

Les fichiers natifs `named.localhost`, `named.loopback`, `named.empty` ne sont pas remplacés. Leur présence et l'absence de liens symboliques sont vérifiées après installation des paquets. L'écoute IPv6 reste désactivée ; une réponse AAAA `::1` est possible en transport IPv4. Le rôle ne modifie ni `/etc/resolv.conf` ni les adresses réseau.

Les permissions des zones partagées autorisent les clients prévus dans la vue peer d'origine ; la sélection des vues contrôle leurs sources et destinations. Les transferts primaires exigent le secondaire BACK et TSIG ; FRONT exclut les adresses BACK des pairs et leur clé. Voir [VALIDATION_NAMED.md](VALIDATION_NAMED.md) pour les corrections, les preuves et les limites de NOTIFY.

## Contrôles du serveur

```bash
sudo named-checkconf -z /etc/named.conf
dig -b 127.0.0.1 @127.0.0.1 localhost A +norecurse
dig -b 127.0.0.1 @127.0.0.1 localhost A +tcp +norecurse
dig -b 127.0.0.1 @127.0.0.1 1.0.0.127.in-addr.arpa PTR +norecurse
dig -b 127.0.0.1 @127.0.0.1 1.0.0.127.in-addr.arpa PTR +tcp +norecurse
```

Attendu : NOERROR, drapeau AA et A 127.0.0.1 ou PTR localhost. Le rôle vérifie ces quatre réponses, les SOA exacts des zones après réconciliation et les SOA des suffixes forwardés en UDP/TCP. Les applications locales voulant utiliser la vue récursive locale peuvent interroger explicitement 127.0.0.1.

Le moniteur facultatif ajoute ces contrôles natifs à son service/timer. Il faut `bind_monitoring_enabled: true` ET le tag `monitor` pour l'installer. Ses résultats sont dans le journal systemd.

## Recette et statut

**EXÉCUTÉ** : 44 configurations chargées et 107 contrôles DNS avec BIND 9.18.24 isolé, dont loopback, deux serveurs, forwarding et NOTIFY. Les fichiers natifs EL8 sont inchangés. Procédure et prérequis : [VALIDATION_NAMED.md](VALIDATION_NAMED.md).

**NON EXÉCUTÉ** : déploiement et fonctionnement réels sur votre RHEL 8/Python 3.6.8, SELinux, pare-feu et services cibles. Le rapport précité précise pour chaque contrôle la raison, la procédure, les prérequis, le résultat attendu et le risque restant. La qualification de production est **NON TERMINÉE**.

Les routes de retour et leur rollback sont décrits dans [FRONT_ROUTES.md](FRONT_ROUTES.md). Les preuves et le nettoyage de la livraison actuelle sont regroupés dans [VALIDATION_NETTOYAGE.md](VALIDATION_NETTOYAGE.md).
