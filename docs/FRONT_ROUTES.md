# Ajouter les seules routes de retour FRONT

Le réseau du serveur est déjà configuré. Le rôle complète les routes IPv4 vers
`bind_cloud_clients` avec les outils natifs `ip` et `nmcli`. Il découvre le
profil NetworkManager actif par UUID, puis lit sa propriété `ipv4.gateway`.
Aucune variable de passerelle ou d'interface supplémentaire n'est nécessaire.

La révision de durcissement relit le profil, sa passerelle, les routes et les
règles sous verrou avant le premier ajout. Une dérive provoque un refus avant
écriture et libère seulement le verrou acquis par cette exécution. Les règles
standard sont contrôlées aussi avant commit et rollback ; des priorités correctes
avec un sélecteur `fwmark`, une destination ou une autre politique ne sont pas
assimilées au routage classique. Voir [DURCISSEMENT_SECURITE.md](DURCISSEMENT_SECURITE.md).

Les clients du sous-réseau directement connecté à FRONT ne nécessitent pas
de route via une passerelle ; leur chemin effectif est néanmoins contrôlé.
Une adresse de client sans préfixe est normalisée en `/32`.

## Configuration et commande

La gestion est **désactivée par défaut**, conformément à l'état demandé après le problème de plan vide. Définir le booléen YAML `bind_front_routes_enabled: true` pour l'activer. La reprise `routes-recover` reste disponible lorsque la gestion est désactivée. Une liste cloud vide ne déclenche aucun ajout ; dans ce cas, la liste interne doit être non vide. Deux listes vides sont refusées avant toute commande réseau.

Les réseaux des clients sont déjà déclarés dans les `group_vars` du site :

```yaml
bind_cloud_clients:
  - 198.51.100.0/24
  - 203.0.113.25
```

Ces adresses sont des exemples de documentation, pas les réseaux de production. `bind_internal_clients` ne fournit aucune destination de route : le réseau BACK existant est conservé. La fusion `bind_allowed_clients` sert exclusivement aux autorisations DNS.
`bind_front_route_metric` est un entier optionnel, par défaut `100`, appliqué
uniquement aux nouvelles routes spécifiques. Une route existante conserve sa
métrique. Les adresses FRONT, BACK et ADM restent celles de l'inventaire.

Depuis la racine du projet, exécuter uniquement les tâches de routage :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --tags routes \
  -e '{"bind_front_routes_enabled": true}'
```

Pour un seul serveur, ajouter `--limit` avec son nom d'inventaire. Les groupes
du site et les variables des deux pairs doivent rester présents dans l'inventaire.
Le tag `routes` n'installe pas de paquet et ne déploie pas de configuration DNS.
Les tâches existantes marquées `always` dérivent seulement les faits du rôle.
Les routes sont aussi incluses dans le tag `deploy` du déploiement complet lorsque ce booléen est activé. Éviter `-e bind_front_routes_enabled=true`, qui produit une chaîne avec Ansible 2.9 ; le rôle exige un véritable booléen.

Prévisualisation sans mutation du réseau ni création du journal :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --tags routes --check -v \
  -e '{"bind_front_routes_enabled": true}'
```

Les observations natives sont réellement lues en mode simulation ; les ajouts
proposés sont affichés. Cette garantie concerne le tag `routes`, pas l'ensemble
du déploiement DNS en mode simulation.

## Prérequis précis

- RHEL 8, Ansible 2.9 et Python 3.6.8 côté serveur constituent la cible demandée.
  Les modules Ansible standard s’exécutent sur le serveur. Aucun script ou filtre
  Python de routage personnalisé n’est fourni, aucun paquet contrôleur supplémentaire
  n’est nécessaire ; `netaddr` et les collections réseau ne sont pas requis.
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
puis dans la table principale du noyau avec `ip -4 route add ... via ... dev ...`, en conservant la métrique retenue.
La commande noyau ne réactive pas la connexion et ne réapplique pas les
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

Le rollback vérifie désormais les identités persistantes **et** actives avant
de retirer la première route. Il refuse les attributs noyau inattendus, les
métriques modifiées, les changements de passerelle/règles et les recouvrements
des adresses actuelles ADM/FRONT/BACK ou du contrôleur SSH. Une route sans
métrique explicite dans la commande correspond à la métrique noyau zéro,
pas à une métrique arbitraire. Un conflit conserve le journal pour inspection.
Un journal de reprise doit être un fichier root régulier `0600` avec un seul
lien, dans les répertoires root `0700` attendus. Son schéma historique est
conservé, mais les doublons d’ajouts sont refusés avant toute commande de retrait.

Le plan est construit par les tâches natives : expressions Jinja standard, validation IPv4 stricte et comparaison d’intervalles numériques pour les réseaux. Les adresses primaires et secondaires sont observées avec `ip -j -4 address show`. Les références de réseaux sont comparées exactement, jamais par sous-chaîne. La déduplication et l’agrégation des préfixes adjacents/inclus restent identiques à la livraison source.

Les faits JSON sont acceptés sous leur forme sérialisée ou sous la forme de dictionnaire/liste déjà convertie par Ansible 2.9 ; ils sont ensuite contrôlés avant emploi. Une sortie vide ou malformée provoque un échec explicite. Les commandes de mutation peuvent réussir sans stdout : seul leur résultat d’exécution puis les observations réseau déterminent la réussite. Aucune erreur d’ajout ou de suppression n’est masquée par `failed_when: false`.

`-v` affiche périphérique, passerelle et ajouts proposés. La route persistante et la route active sont contrôlées séparément : une route correctement persistée mais absente du noyau est réinstallée à chaud. Une route contradictoire existante n’est pas supprimée automatiquement : le réseau préexistant reste sous la responsabilité de son gestionnaire.

Avant la première modification, le plan est enregistré dans
`/var/lib/Bind9-Ansible/front-routes.lock/plan.json` : répertoire root `0700`,
fichier root `0600`. Le journal est borné en taille. En cas d'échec, le rôle
retire seulement ses ajouts qui sont encore présents avec la même identité (préfixe, passerelle, interface et métrique),
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

### Diagnostiquer « Reject invalid IPv4 observations before any routing write »

L’existence des passerelles ne suffit pas à identifier la cause : la même
normalisation traite également les adresses locales, `ansible_host`, les
réseaux clients, les destinations noyau et le contenu d’un journal de reprise.
La valeur exacte de l’erreur initiale reste nécessaire pour attribuer cet
incident à une entrée particulière ; aucune cause n’est annoncée comme prouvée
sans cette valeur.

Le message indique désormais le contexte de lecture, le type et la valeur
échappée en cas de format invalide. Selon le contexte :

- **Interfaces/inventaire** : vérifier FRONT/BACK et l’IPv4 ADM littérale dans
  `ansible_host` ; un nom DNS n’est pas une IPv4 normalisable par cette fonction.
- **Plan** : contrôler les clients déclarés, la source SSH et `ipv4.gateway`
  du profil FRONT sélectionné ; aucune passerelle globale de remplacement.
- **Profil** : examiner uniquement `ipv4.routes` de ce profil, avec son format
  exact et ses réseaux/adresses ; une sortie incomprise n’est pas ignorée.
- **Noyau** : examiner `ip -j -4 address show` et
  `ip -j -4 route show table main`, sans modifier le réseau.
- **Journal** : inspecter le fichier privé conservé et sa provenance avant
  `routes-recover` ; ne pas remplacer des valeurs au hasard ni effacer le verrou.

Les observations non IPv4, non canoniques ou de mauvais type restent bloquantes.
Une source SSH IPv6 native n’est pas ajoutée aux gardes IPv4. Seules les formes
IPv4 mappées complètes `::ffff:a.b.c.d` et `::ffff:hhhh:hhhh` sont converties :
une adresse IPv6 contenant simplement `::ffff:` au milieu reste une IPv6.
Une normalisation exécutée pendant une vérification/rollback ne signifie pas
qu’aucune tâche antérieure n’a ajouté de route : lire le journal de l’opération.

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

Adaptation du playbook fourni et résultats actuels : [VALIDATION_ROUTES_NATIVES.md](VALIDATION_ROUTES_NATIVES.md).
