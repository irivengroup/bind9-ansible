# ACL et permissions de la configuration sans vues

[Documentation](../../../index.md) › [Configuration](../index.md) › [Réseau et interfaces](index.md) › ACL et permissions de la configuration sans vues

[Retour au Parent](index.md)


Les ACL sont définies une seule fois dans `roles/bind9/templates/named.conf.j2`, à partir des pairs du site courant. Elles ne sont pas des variables supplémentaires d'inventaire.

| ACL | Membres |
|---|---|
| `local_loopback` | Exactement `127.0.0.1` |
| `bind_primary_back_ip` | `hostvars[bind_primary_host].bind_back_ip` |
| `bind_secondary_back_ip` | `hostvars[bind_secondary_host].bind_back_ip` |
| `bind_primary_front_ip` | FRONT local, déclaré seulement sur le primaire |
| `bind_secondary_front_ip` | FRONT local, déclaré seulement sur le secondaire |
| `bind_servers_back_ip` | Les deux ACL individuelles BACK |
| `user_defined_clients` | Fusion calculée `bind_allowed_clients` = cloud + internes sans doublons ; non vide obligatoire |
| `dns_query_clients` | Loopback, pairs BACK et liste cliente unique |
| `dns_recursive_clients` | Exclut d'abord les pairs BACK et la clé TSIG ; autorise ensuite loopback et liste cliente unique |

## Applications

FRONT et BACK peuvent être calculés à partir d’une écoute unique ; les IP ne sont alors pas à dupliquer dans host_vars. Avec plusieurs écoutes, affecter explicitement les deux rôles. Les variantes et la règle de paire ISO sont décrites dans [les interfaces réseau](INTERFACES_RESEAU.md).

| Directive | Application |
|---|---|
| `listen-on` | Loopback et adresses DNS locales sélectionnées dans l’inventaire |
| `listen-on-v6` | `none` |
| `controls` | RNDC 127.0.0.1:953 seulement, `allow { local_loopback; }`, clé administrative native distincte |
| `allow-query` | `dns_query_clients` |
| `allow-query-on` | Mêmes adresses locales que `listen-on` |
| `allow-recursion`, `allow-query-cache` | `dns_recursive_clients` |
| `allow-recursion-on`, `allow-query-cache-on` | Loopback ; FRONT local si cloud non vide ; BACK local si internes non vides |
| Primaire, `allow-transfer` | ACL imbriquée : source secondaire BACK **ET** clé TSIG du site |
| Secondaire, `allow-notify` | ACL imbriquée : primaire BACK et clé ; les masters conservent leur permission NOTIFY implicite BIND |

`transfer-source`, `notify-source`, `masters`, `also-notify` et `forwarders` attendent des adresses de transport ; leurs IP explicites sont conservées. Les remplacer par des noms d'ACL serait incorrect. Voir la [référence BIND](https://bind9.readthedocs.io/en/v9.18.24/reference.html).

`controls inet` attend aussi une IP littérale : 127.0.0.1 y est nécessaire ; sa liste `allow` utilise bien `local_loopback`. Le drop-in commun conserve le lancement natif IPv4/IPv6 ; `listen-on-v6 { none; };` ferme l’écoute DNS IPv6. RNDC reste une fonction administrative indépendante et IPv4 locale. Les entrées `bind_trusted_networks.cloud_clients` et `bind_trusted_networks.internal_clients` sont fusionnées dans le fait `bind_allowed_clients`, rendu dans `user_defined_clients`.

`allow-query-on` et `listen-on` utilisent la même liste. Le primaire et le secondaire utilisent chacun les ACL FRONT et BACK locales. Une écoute unique regroupe ces rôles sur une seule adresse DNS. Les adresses d’écoute supplémentaires sont rendues explicitement dans ces deux directives et suivent l’ACL source commune des requêtes. La récursion et le cache restent réservés au loopback et aux rôles FRONT/BACK selon les listes de clients. L’agrégat BACK sert aux permissions de requête et au refus de récursion des pairs.

## Effets du retrait des vues

Les deux catégories partagent un espace de noms et une ACL source calculée.
Les destinations de récursion/cache sont activées selon les listes : FRONT
pour cloud, BACK pour internes et loopback dans tous les cas. Si les deux
listes sont actives, une source de la fusion est admise sur les deux
destinations. Cette configuration sans vues ne corrèle pas catégorie et
interface ; l’arrivée cloud → FRONT et interne → BACK dépend du réseau
existant. Aucun filtrage pare-feu ni route interne n’est ajouté par le rôle.

Les clients ordinaires, même munis de la clé TSIG, ne peuvent pas transférer les zones. Le secondaire authentifié depuis son adresse BACK peut en revanche transférer depuis **toute adresse d'écoute du primaire**, y compris FRONT : `allow-transfer` filtre sa source et sa clé, sans filtre de destination. Un test FRONT doit donc être exécuté depuis un client externe, pas depuis ce pair autorisé. Le secondaire interdit les transferts sortants. TSIG authentifie ; il ne chiffre pas les données.

La récursion reste refusée aux pairs BACK et aux requêtes avec la clé de transfert. Les sources inconnues sont refusées. Le refus par défaut s'applique aussi aux mises à jour dynamiques et transferts non explicitement autorisés.

Preuves et limites : [VALIDATION_RHEL8.md](../../qualification/VALIDATION_RHEL8.md). Commandes : [TESTS_FONCTIONNELS.md](../../qualification/TESTS_FONCTIONNELS.md).

---

Documentation DNSZIBO · navigation actualisée le 23 novembre 2026.
