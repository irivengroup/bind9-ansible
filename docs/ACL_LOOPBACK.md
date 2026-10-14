# ACL du loopback local — 14 octobre 2026

Ce rapport décrit la livraison initiale de l'ACL loopback. L'évolution suivante ajoute les ACL des serveurs et une référence loopback dans `allow-query-on` ; son périmètre et ses contrôles actuels figurent dans [ACL_SERVEURS.md](ACL_SERVEURS.md).

La configuration canonique définit une seule ACL explicite, avant les options et les vues :

```bind
acl "local_loopback" {
    127.0.0.1;
};
```

Elle désigne exactement `127.0.0.1`, sans élargir l'accès à `127.0.0.0/8`, à IPv6 ou aux adresses FRONT/BACK. Le nom public est fixe ; aucune variable d'inventaire supplémentaire n'est nécessaire.

## Intégration

| Emplacement dans named.conf.j2 | Utilisation |
|---|---|
| Options | `listen-on port 53 { local_loopback; ... };` |
| Vue peer | Entrée `local_loopback` dans `allow-query` des zones partagées |
| Vue local | `match-clients`, `match-destinations`, `allow-recursion`, `allow-query-cache`, `allow-query` |

Cela remplace les sept occurrences de l'adresse dans les listes d'accès et d'écoute par sept références à l'ACL. L'adresse littérale ne subsiste qu'une fois dans `named.conf.j2`, dans sa définition. Les fichiers natifs RHEL restent inclus et inchangés.

Les commandes `dig`, les vérifications d'adresses et les enregistrements A conservent l'adresse littérale : une ACL BIND ne peut pas remplacer une adresse réseau dans ces contextes. La zone racine privée, les vues FRONT/BACK, TSIG, DNSSEC et les routes de retour gardent leur comportement.

## Vérification

| Contrôle | Statut | Preuve |
|---|---|---|
| Tests contrôleur | **EXÉCUTÉ** | 199 tests réussis ; notamment huit rendus primaire/secondaire et ACL clientes présentes/absentes vérifiant la définition unique et tous ses usages |
| Syntaxe Ansible 2.9.27 | **EXÉCUTÉ** | Trois inventaires dev/preprod/prod, codes de sortie 0 |
| BIND 9.18.24 isolé | **EXÉCUTÉ** | 44 configurations chargées et 107 contrôles DNS réels réussis, dont loopback UDP/TCP et refus des sources interdites ; fichiers natifs EL8 inchangés |
| Packaging | **EXÉCUTÉ** | 102 fichiers ; quatre sources modifiées, un rapport ajouté, aucune suppression et 95 sources inchangées ; manifeste, liens locaux, CRC et reproduction binaire du ZIP contrôlés |

Les huit workflows Ansible de routes n'ont pas été relancés pour ce changement de template. Leur code et leurs tests sont inchangés ; leur exécution dans la livraison source est décrite dans [VALIDATION_NETTOYAGE.md](VALIDATION_NETTOYAGE.md). Les anciennes preuves de couverture, NetworkManager et préflight des 12 hôtes restent des preuves de cette livraison précédente, pas de nouvelles exécutions pour l'ACL.

La source immédiate est `Bind9-Ansible-nettoye.zip`, SHA-256 `f202cc65e62af16bf621eda3ea83b2375d39c4610b0b328ee323eb2ea8871541`. Cette archive est conservée. Le manifeste et `DELIVERY.json` recensent les modifications ; aucune dépendance de production n'est ajoutée.

## Recette cible restante

**NON EXÉCUTÉ** : RHEL 8 / Python 3.6.8 / version BIND cible, faute d'accès aux serveurs. Prérequis : hôte de recette, SSH/sudo, fichiers natifs installés et sauvegarde vérifiée. Après déploiement, exécuter :

```bash
sudo named-checkconf -z /etc/named.conf
dig -b 127.0.0.1 @127.0.0.1 localhost A +norecurse
dig -b 127.0.0.1 @127.0.0.1 localhost A +tcp +norecurse
dig -b 127.0.0.1 @127.0.0.1 1.0.0.127.in-addr.arpa PTR +norecurse
dig -b 127.0.0.1 @127.0.0.1 1.0.0.127.in-addr.arpa PTR +tcp +norecurse
```

Attendu : configuration chargée sans erreur, NOERROR et AA, A `127.0.0.1` et PTR `localhost.`. Vérifier aussi les réponses métier FRONT/BACK et le refus d'une source non autorisée selon [VALIDATION_NAMED.md](VALIDATION_NAMED.md). Risque restant : particularités de version, labels SELinux et filtrage sur la cible. La qualification production reste **NON TERMINÉE**.

Pour revenir à la livraison précédente, utiliser l'archive source conservée ; pour restaurer un serveur déjà déployé, suivre [OPERATIONS.md](OPERATIONS.md) avec sa sauvegarde préalable. Ce changement d'ACL ne crée ni ne supprime de routes.
