# ACL des serveurs BIND — 14 octobre 2026

Cette livraison ajoute les six ACL demandées à `roles/bind9/templates/named.conf.j2`. Elles sont définies une seule fois avant les options et les vues, avec les pairs déjà découverts pour l'environnement et le site courants. Aucun hôte d'un autre site n'est ajouté.

| ACL | Membres |
|---|---|
| `bind_primary_back_ip` | `hostvars[bind_primary_host].bind_back_ip` |
| `bind_secondary_back_ip` | `hostvars[bind_secondary_host].bind_back_ip` |
| `bind_primary_front_ip` | `hostvars[bind_primary_host].bind_front_ip` |
| `bind_secondary_front_ip` | `hostvars[bind_secondary_host].bind_front_ip` |
| `bind_servers_back_ip` | ACL `bind_primary_back_ip` et `bind_secondary_back_ip` |
| `bind_servers_front_ip` | ACL `bind_primary_front_ip` et `bind_secondary_front_ip` |

Ces noms sont des ACL BIND générées, pas des variables d'inventaire supplémentaires. Les quatre adresses proviennent des `host_vars` existants ; leur présence est déjà exigée par `site_peers.yml`. L'ACL `local_loopback` reste exactement `127.0.0.1`.

## Application et invariants

| Emplacement | Application |
|---|---|
| `listen-on` | `local_loopback` et les deux ACL individuelles FRONT/BACK du rôle local ; aucune écoute sur les adresses du pair |
| `allow-query-on` global | `local_loopback`, `bind_servers_front_ip`, `bind_servers_back_ip` ; les destinations possibles sont celles du site, et l'écoute reste limitée à ce serveur |
| Vue peer, `match-clients` | `bind_servers_back_ip` et la clé TSIG, comme les deux anciennes adresses BACK et la clé |
| Vue peer, `match-destinations` | ACL BACK du rôle local |
| Vue peer, `allow-query` | `bind_servers_back_ip` remplace les deux adresses BACK ; loopback, clé et ACL clientes conservés |
| Primaire, `allow-transfer` | ACL imbriquée avec `bind_secondary_back_ip` **ET** clé TSIG ; aucune transformation en adresse OU clé |
| Secondaire, `allow-notify` | ACL imbriquée avec `bind_primary_back_ip` et clé ; l'autorisation implicite des masters par BIND reste inchangée |
| Vue external, `match-clients` | Exclusion `!bind_servers_back_ip`, puis exclusion de la clé de transfert, puis ACL clients FRONT |
| Vue external, `match-destinations` | ACL FRONT du rôle local |
| Vue internal, `match-destinations` | ACL BACK du rôle local |

L'ajout de `allow-query-on` rend explicite la liste des adresses DNS du site. Il autorise toutes les adresses sur lesquelles ce serveur écoute déjà ; il n'ajoute aucun listener et ne change pas les restrictions de source, de cache ou de récursion des vues.

Les destinations et sources de transport gardent leurs IP explicites : `transfer-source`, `notify-source`, `masters`, `also-notify` et les destinations `forwarders` ne sont pas des listes d'ACL. La [référence BIND](https://bind9.readthedocs.io/en/v9.18.24/reference.html) décrit les grammaires correspondantes. Les remplacer par des noms d'ACL rendrait la configuration incorrecte. Les RDATA de zones, commandes dig et contrôles réseau conservent également leurs adresses.

La racine privée, les données des zones, leurs serials, DNSSEC, les fichiers natifs RHEL, la sélection des clients et les routes de retour sont préservés. Les noms internes Jinja `primary_role`, `local_front_acl`, `local_back_acl` servent uniquement à choisir les ACL du rôle ; ils ne constituent pas des options Ansible publiques.

## Vérifications et provenance

Source immédiate : `Bind9-Ansible-acl-loopback.zip`, SHA-256 `4fbec03083aa49fc31663f5a49c942dba73e99cc4c71025fd862112d2d2b5108`. Cette archive précédente est conservée. Les différences et les empreintes des sources sont décrites par `DELIVERY.json` et `SOURCE-MANIFEST.sha256`.

| Contrôle | Statut | Résultat |
|---|---|---|
| Tests contrôleur | **EXÉCUTÉ** | 201 tests réussis, Python 3.8.20 ; deux nouvelles régressions primaire/secondaire vérifient les membres exacts, l'exclusion d'un autre site, les listeners locaux et les conditions TSIG ; les huit rendus loopback existants sont conservés |
| Syntaxe Ansible 2.9.27 | **EXÉCUTÉ** | Dev/preprod/prod, trois codes de sortie 0 |
| Pyflakes 3.2.0 | **EXÉCUTÉ** | Filtres, scripts Python et tests analysés ; aucun diagnostic |
| BIND 9.18.24 isolé | **EXÉCUTÉ** | 44 configurations chargées et 107 contrôles DNS réels ; loopback UDP/TCP, vues clientes, forwarding, transferts autorisés/interdits, NOTIFY et convergence des pairs ; fichiers natifs EL8 inchangés |
| Packaging | **EXÉCUTÉ** | 104 fichiers ; six sources modifiées, deux fichiers ajoutés, 94 sources inchangées, aucune suppression ; métadonnées régénérées, manifeste/CRC/liens locaux contrôlés et deux constructions binaires identiques dans le même runtime |

Les huit workflows Ansible de routes n'ont pas été relancés pour cette évolution de template : leur code et leurs tests sont inchangés. Les preuves précédentes NetworkManager, couverture des filtres et préflight des 12 hôtes sont conservées dans [VALIDATION_NETTOYAGE.md](VALIDATION_NETTOYAGE.md) ; elles ne sont pas présentées comme de nouvelles exécutions pour cette livraison. La CI existante découvre automatiquement `test_server_acls.py` avec sa commande pytest ; aucune dépendance supplémentaire n'est nécessaire.

Les régressions contrôlent notamment qu'une ACL agrégée ne remplace pas l'adresse locale dans l'écoute, qu'un autre site n'est pas inclus et qu'une négation de source/condition TSIG reste présente. Le laboratoire BIND vérifie le comportement réel des ACL nommées, notamment les cas AXFR signé/non signé et les mauvaises sources/destinations. Les détails des scénarios sont dans [VALIDATION_NAMED.md](VALIDATION_NAMED.md).

## Recette et retour arrière

**NON EXÉCUTÉ** : fonctionnement sur vos serveurs RHEL 8 / Python 3.6.8 et leur version BIND, faute d'accès aux cibles. Prérequis : paire de recette, SSH/sudo, sauvegarde vérifiée, clients FRONT/BACK et fichiers natifs installés. Déployer le primaire avant le secondaire, puis :

```bash
sudo named-checkconf -z /etc/named.conf
sudo systemctl status named
dig -b 127.0.0.1 @127.0.0.1 localhost A +norecurse
dig -b 127.0.0.1 @127.0.0.1 1.0.0.127.in-addr.arpa PTR +tcp +norecurse
```

Depuis les clients de recette, appliquer aussi les contrôles FRONT/BACK, SOA des pairs, forwarding et AXFR indiqués dans [VALIDATION_NAMED.md](VALIDATION_NAMED.md). Attendu : réponses autorisées inchangées, refus des sources interdites, transfert uniquement vers le secondaire BACK authentifié et convergence SOA. Risque restant : différences de version, SELinux, filtrage réseau et runtime cible. Les raisons/procédures des autres contrôles de production manquants sont dans ce rapport ; la qualification reste **NON TERMINÉE**.

Le retour au code précédent utilise l'archive source conservée. Pour restaurer un serveur déjà déployé, suivre [OPERATIONS.md](OPERATIONS.md) avec la sauvegarde préalable. Le rollback DNS et le journal de routes du rôle sont inchangés ; aucun effacement de routes préexistantes ou committées n'est introduit.
