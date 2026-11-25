# Clients cloud, clients internes et fusion DNS

[Documentation](../../../index.md) › [Configuration](../index.md) › [Réseau et interfaces](index.md) › Clients cloud, clients internes et fusion DNS

[Retour au Parent](index.md)

## Sommaire

- [Exemple avec clients internes seuls](#exemple-avec-clients-internes-seuls)
- [Limite de la configuration sans vues](#limite-de-la-configuration-sans-vues)
- [Migration, tests et rollback](#migration-tests-et-rollback)

## Exemple avec clients internes seuls

Ce cas autorise les réseaux internes sans alimenter le moteur de routes FRONT.

```yaml
bind_trusted_networks:
  cloud_clients: []
  internal_clients:
    - 192.168.20.25
    - 10.30.1.0/24
```

## Limite de la configuration sans vues

La fusion est une autorisation source partagée. Les ACL source et destination
de BIND sont indépendantes : quand les deux listes existent, les accès croisés
restent possibles pour les sources déclarées. La configuration ne garantit
donc pas une isolation stricte par catégorie/interface. Les demandes sont
attendues sur FRONT pour cloud et BACK pour internes selon votre réseau
existant ; le rôle ne reconfigure ni ce réseau ni son pare-feu.

Les zones natives, DNSSEC configurable (défaut `auto`), IPv4, réplication TSIG
et NXDOMAIN hors périmètre sont conservés. Les pairs BACK et la clé de
transfert restent exclus de la récursion.

## Migration, tests et rollback

Les anciens noms `bind_frontend_clients`, `bind_backend_clients`, `bind_cloud_clients` et `bind_internal_clients` doivent être retirés des inventaires et surcharges après transfert de leurs valeurs dans `bind_trusted_networks`. Les anciens réglages DNSSEC migrent vers [bind_dnssec_active](../bind/DNSSEC.md#compatibilité-et-migration).



Conserver la version et les variables précédentes. Déplacer les anciennes
listes dans les clés `cloud_clients` et `internal_clients` de
`bind_trusted_networks`, en conservant toutes leurs valeurs. Renseigner les
listes selon l’arrivée réelle et supprimer toute définition manuelle de la
fusion. Les anciennes variables de premier niveau doivent être retirées des
inventaires et des fichiers de surcharge pendant cette migration.
Les hôtes internes ne doivent pas rester dans la liste cloud uniquement pour
obtenir une autorisation DNS : cela les exposerait au calcul de routes FRONT.

Les tests de ce contrat se rejouent dans le [laboratoire isolé](../../qualification/QUALIFICATION_ACTUELLE.md#rejouer-les-contrôles). Les comptes et limites d’origine sont [archivés](../../historique/CAMPAGNES_CONFIGURATION.md#clients).

Pour revenir à la version précédente, restaurer ensemble l’archive source
et ses variables ; une sauvegarde BIND seule ne restaure pas les sources Ansible. Le rescue
restaure maintenant aussi les six fichiers runtime capturés en début de
transaction ; leur capture en mémoire ne survit pas à l’arrêt du contrôleur. Le rollback d’un échec DNS reste local à
l’hôte et retire seulement les routes de la transaction courante. Les routes
préexistantes ou déjà commises restent conservées. Voir
[OPERATIONS.md](../../exploitation/OPERATIONS.md), [FRONT_ROUTES.md](FRONT_ROUTES.md) et
[TESTS_FONCTIONNELS.md](../../qualification/TESTS_FONCTIONNELS.md).

---

Documentation DNSZIBO · contenu harmonisé le 25 novembre 2026.
