# Ajouter les seules routes de retour FRONT

Le réseau du serveur est déjà configuré. Le rôle complète les routes IPv4 vers
`bind_frontend_clients` avec les outils natifs `ip` et `nmcli`. Il découvre le
profil NetworkManager actif par UUID, puis lit sa propriété `ipv4.gateway`.
Aucune variable de passerelle ou d'interface supplémentaire n'est nécessaire.

Les clients du sous-réseau directement connecté à FRONT ne nécessitent pas
de route via une passerelle ; leur chemin effectif est néanmoins contrôlé.
Une adresse de client sans préfixe est normalisée en `/32`.

## Configuration et commande

Les réseaux des clients sont déjà déclarés dans les `group_vars` du site :

```yaml
bind_frontend_clients:
  - 198.51.100.0/24
  - 203.0.113.25
```

Ces adresses sont des exemples de documentation, pas les réseaux de production.
`bind_front_route_metric` est un entier optionnel, par défaut `100`, appliqué
uniquement aux nouvelles routes spécifiques. Une route existante conserve sa
métrique. Les adresses FRONT, BACK et ADM restent celles de l'inventaire.

Depuis la racine du projet, exécuter uniquement les tâches de routage :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --tags routes
```

Pour un seul serveur, ajouter `--limit` avec son nom d'inventaire. Les groupes
du site et les variables des deux pairs doivent rester présents dans l'inventaire.
Le tag `routes` n'installe pas de paquet et ne déploie pas de configuration DNS.
Les tâches existantes marquées `always` dérivent seulement les faits du rôle.
Les routes sont aussi incluses dans le tag `deploy` du déploiement complet.

Prévisualisation sans mutation du réseau ni création du journal :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --tags routes --check
```

Les observations natives sont réellement lues en mode simulation ; les ajouts
proposés sont affichés. Cette garantie concerne le tag `routes`, pas l'ensemble
du déploiement DNS en mode simulation.

## Prérequis précis

- RHEL 8, Ansible 2.9 et Python 3.6.8 côté serveur constituent la cible demandée.
  Les filtres Python s'exécutent sur le contrôleur, et les modules Ansible sur
  le serveur ; aucun script Python de routage n'est installé sur les cibles.
- SSH ADM et `become: true` ; `ip` et `nmcli` déjà disponibles ; NetworkManager
  déjà actif avec un profil FRONT et une passerelle IPv4 configurée, sur le lien.
- FRONT, BACK et ADM sont des interfaces distinctes avec les adresses IPv4
  littérales déclarées dans l'inventaire. Les adresses doivent être présentes
  sur le serveur ; la découverte refuse les profils absents ou ambigus.
- Routage IPv4 dans la table principale avec les trois règles standard
  `local`, `main`, `default`. Les VRF et règles de routage personnalisées sont
  refusées plutôt que modifiées ou interprétées implicitement.
- Les réseaux des clients ne doivent pas englober les réseaux BACK/ADM, les
  adresses des pairs, ni l'adresse IPv4 SSH du contrôleur. Celle-ci est lue depuis
  la session du compte SSH, sans dépendre de l'environnement filtré par sudo.
  Une session SSH non identifiable provoque un échec avant modification.
  Les routes par défaut, loopback, link-local, multicast et IPv6 sont refusées.
- Aucun autre déploiement ou opérateur ne doit modifier le profil pendant cette
  opération. Le verrou du rôle ne sérialise pas les commandes manuelles externes.

## Opération additive

Une route manquante est ajoutée avec l'opérateur `+ipv4.routes` dans le profil,
puis sur l'interface active avec `nmcli device modify`.
La seconde commande ne réactive pas la connexion et ne réapplique pas les
autres modifications éventuelles en attente dans le profil. Les paramètres
d'adressage, de passerelle, de DNS, de MTU et les profils BACK/ADM ne sont pas
écrits par ces tâches. Il n'y a aucun appel `connection up/down` dans le rôle.

Le choix des commandes suit la
[documentation NetworkManager](https://www.networkmanager.dev/docs/api/1.30/nmcli.html)
et la [procédure RHEL 8 pour les ajouts de routes](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/8/html/configuring_and_managing_networking/configuring-static-routes_configuring-and-managing-networking).

Les routes initiales sont photographiées avant modification. Après l'ajout,
le rôle contrôle leur conservation, la correspondance des routes persistantes
avec le plan et le chemin obtenu avec `ip route get CLIENT from FRONT_IP`.
La route par défaut existante doit rester identique. Un deuxième passage
n'effectue aucune mutation si les routes sont déjà présentes et correctes.

Une route contradictoire au même préfixe, ou plus spécifique, provoque un échec
avant modification. Une route active non persistée n'est pas revendiquée comme
propriété du rôle : la persister explicitement dans son profil avant de relancer.
Les routes présentes ne sont jamais remplacées pour imposer une nouvelle métrique.
Le rôle ajoute les préfixes demandés ; il ne supprime pas automatiquement une
ancienne route quand un client disparaît de la liste.

## Échec, interruption et reprise

Avant la première modification, le plan est enregistré dans
`/var/lib/Bind9-Ansible/front-routes.lock/plan.json` : répertoire root `0700`,
fichier root `0600`. Le journal est borné en taille. En cas d'échec, le rôle
retire seulement ses ajouts qui sont encore présents avec la même identité,
puis exige le retour à l'état initial. Une erreur de rollback est explicite
et laisse le journal disponible ; elle n'est pas ignorée.

Le journal est supprimé à la réussite. Un arrêt brutal laisse le verrou et
interdit un nouvel ajout. Vérifier qu'aucun déploiement n'est en cours, examiner
le journal et les routes, puis lancer la reprise explicite sur le serveur :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --tags routes-recover
```

Ajouter `--limit` pour reprendre un seul hôte. Si une autre personne a remplacé
une route ou changé le profil actif, la reprise s'arrête et conserve le journal
pour examen. Ne pas supprimer le verrou d'une opération encore active. Un
verrou sans journal exploitable exige une inspection manuelle ; aucune route
n'est supprimée à l'aveugle.

Avec `--tags deploy` ou l'exécution complète, les nouveaux ajouts restent
journalisés jusqu'à la réussite des contrôles DNS et de la vérification réseau
finale. Un échec dans ce bloc retire les ajouts persistants et actifs de cette
exécution, puis déclenche la restauration BIND. Les routes préexistantes restent
en place. Si une suppression échoue, le journal reste disponible et l'erreur
finale le signale ; la restauration BIND est malgré tout tentée.

Avec `--tags routes`, le commit suit immédiatement la vérification des routes.
Une restauration manuelle ultérieure avec `bind-configtool restore` restaure
uniquement les fichiers BIND : elle ne supprime pas des routes déjà committées.

## Vérification et limites

Voir [VALIDATION_FRONT_ROUTES.md](VALIDATION_FRONT_ROUTES.md) pour les résultats
exécutés et les contrôles restant à réaliser sur le serveur RHEL.

Le laboratoire NetworkManager est reproductible sur un contrôleur Linux
disposable disposant de root, `unshare`, `ip`, `dbus-daemon`, NetworkManager,
Ansible et Python avec PyYAML :

```bash
sudo bash tests/run_integration_front_routes.sh
```

Le lanceur crée les espaces réseau, processus et montages isolés AVANT le
démarrage du service de test. Les commandes de réactivation dans ce laboratoire
servent uniquement à prouver la persistance ; elles ne figurent pas dans les
tâches déployées. Aucun paquet n'est installé par ce test.
