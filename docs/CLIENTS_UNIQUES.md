# Clients cloud, clients internes et fusion DNS

Le rôle expose deux listes plates d’IP IPv4 ou de sous-réseaux CIDR :

```yaml
bind_cloud_clients:
  - 192.0.2.0/24
  - 10.150.1.23
bind_internal_clients:
  - 192.168.20.25
  - 10.30.1.0/24
```

Ces valeurs sont fictives. Les inventaires conservent les internes à `[]`
tant que leurs réseaux réels ne sont pas renseignés.

| Entrée cloud | Entrée interne | Résultat |
|---|---|---|
| Non vide | Vide | DNS sur FRONT/loopback ; routes cloud possibles |
| Vide | Non vide | DNS sur BACK/loopback ; aucune opération de routes FRONT |
| Non vide | Non vide | DNS sur FRONT/BACK/loopback ; seules routes cloud possibles |
| Vide | Vide | Refus avant toute modification, pour tous les tags |

La colonne DNS décrit les destinations de récursion/cache. Les requêtes
autoritatives restent admises sur les écoutes locales pour la fusion et les
ACL techniques. Le loopback et les pairs BACK ne sont pas des entrées clientes.

`bind_allowed_clients` est un **fait calculé**, pas une troisième entrée :
`(bind_cloud_clients + bind_internal_clients) | unique | list`. Les doublons
textuellement identiques sont retirés ; aucune liste n’est modifiée et les
sous-réseaux ne sont pas élargis pour l’autorisation DNS. Une surcharge
contradictoire par extra-vars échoue explicitement. Les deux entrées sont
typées et validées avant concaténation ; IPv6, `/0`, CIDR non canoniques,
structures de groupes et caractères d’injection sont refusés.

La fusion alimente uniquement l’ACL BIND commune `user_defined_clients` et ses
contrôles de requêtes/récursion/cache. Le plan de routes lit exclusivement
`bind_cloud_clients` : IP seule normalisée en `/32`, déduplication/agrégation
des routes, profil et passerelle FRONT existants, gardes BACK/ADM/SSH et
rollback des seuls ajouts possédés conservés. Les réseaux internes ne passent
pas au plan, même s’ils appartiennent directement au sous-réseau BACK.

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

Conserver la version et les variables précédentes. Renseigner les deux listes
selon l’arrivée réelle ; supprimer toute définition manuelle de la fusion.
Les hôtes internes ne doivent pas rester dans la liste cloud uniquement pour
obtenir une autorisation DNS : cela les exposerait au calcul de routes FRONT.

Depuis le contrôleur Linux de recette, à la racine du projet :

```bash
sudo python -m pytest -q tests
python tests/integration_preflight.py
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --syntax-check
sudo bash tests/run_integration_front_routes.sh
```

Les nombres et résultats exécutés sont consignés dans `DELIVERY.json`.
Les rendus où les deux listes sont vides vérifient seulement les branches et
références du template ; ces entrées sont refusées par le rôle avant déploiement.
Les cas couvrent fusion/doublons, une liste vide, refus des deux vides,
listes mal typées, accès DNS FRONT/BACK, routes exclusivement cloud,
internes seuls sans routage, surcharge et rollback.

**NON EXÉCUTÉ** : déploiement sur vos serveurs RHEL 8, DNF/Python cible réel,
SELinux, unités systemd, réseau/SSH réels, reboot cible, renouvellement DNSSEC
sur sept jours, restauration système, charge et CI distante. Raison : aucune
paire cible accessible. Prérequis, procédures, attendus et risques :
[VALIDATION_RHEL8.md](VALIDATION_RHEL8.md). Qualification de production
**NON TERMINÉE**.

Pour revenir à la version précédente, restaurer ensemble l’archive source
et ses variables ; une sauvegarde BIND seule ne restaure pas les sources Ansible. Le rescue
restaure maintenant aussi les six fichiers runtime capturés en début de
transaction ; leur capture en mémoire ne survit pas à l’arrêt du contrôleur. Le rollback d’un échec DNS reste local à
l’hôte et retire seulement les routes de la transaction courante. Les routes
préexistantes ou déjà commises restent conservées. Voir
[OPERATIONS.md](OPERATIONS.md), [FRONT_ROUTES.md](FRONT_ROUTES.md) et
[TESTS_FONCTIONNELS.md](TESTS_FONCTIONNELS.md).
