# Déclarer les interfaces dans host_vars

[Documentation](../../../index.md) › [Configuration](../index.md) › [Réseau et interfaces](index.md) › Déclarer les interfaces dans host_vars

[Retour au Parent](index.md)


## Périmètre

Le rôle utilise les interfaces et adresses IPv4 **déjà configurées** sur chaque
serveur. Il ne crée aucune interface, ne configure pas DHCP et ne déplace pas la
passerelle par défaut. Il distingue le nombre d'interfaces IPv4 actives du nombre
d'interfaces choisies pour l'écoute DNS. Loopback reste une écoute locale gérée
par le rôle et n'est pas à ajouter à `bind_listen_interfaces`.

Les noms d'hôtes et les adresses des exemples sont fictifs. Adapter les deux
fichiers de la paire au même scénario ; ne pas combiner plusieurs scénarios dans
le même `host_vars`.

## Choisir la topologie

| Cas | Déclaration dans chaque host_vars | Résolution des rôles | Routes FRONT |
|---|---|---|---|
| Une interface | `ansible_host` et liste d'une entrée `{name: ...}` | DNS, FRONT, BACK et ADMIN sur cette interface | Aucun ajout |
| Plusieurs interfaces, une écoute DNS | `ansible_host` et `bind_listen_interfaces` avec une entrée | FRONT et BACK sur l'unique écoute ; ADMIN peut être distinct | Aucun ajout |
| Deux interfaces, deux écoutes DNS | Entrées `name`, `ipaddress`, `scope: front/back` ; `ansible_host` sur FRONT ou BACK | ADMIN partage une interface DNS | Aucun ajout |
| FRONT, BACK et ADMIN distincts | Mêmes entrées ; `ansible_host` sur ADMIN | Trois interfaces distinctes | Optionnelles, désactivées par défaut |
| Plusieurs IPv4 sur une interface | Champ `ipaddress` de l'entrée correspondante | Une seule IPv4 choisie par interface d'écoute | Selon la séparation des trois rôles |
| Connexion SSH par nom ou adresse traduite | Ajouter `bind_admin_ip` avec l'IPv4 ADMIN locale | SSH se connecte à `ansible_host`, le rôle identifie ADMIN par son IP locale | Selon la séparation des trois rôles |

Les douze fichiers `inventory/*/host_vars/*.yml` livrés utilisent le modèle
multi-interface avec ADMIN dédié : `ens256` (FRONT) et `ens224` (BACK) sont les deux écoutes DNS,
et `ansible_host` reste l'adresse ADMIN. Les adresses FRONT/BACK existantes sont
conservées. Ces noms d'interface sont des exemples à adapter aux serveurs réels ;
ils ne sont pas imposés par le rôle. Pour une interface ou une écoute unique,
remplacer ensemble les deux fichiers de la paire par le scénario correspondant.

Les six paires complètes sont regroupées dans
[les exemples d'interfaces](../../../../examples/interfaces/README.md). Leurs
fichiers portent les noms `site1-dns01.yml` et `site1-dns02.yml`, identiques à ceux
de l'inventaire du site 1.

## Une seule interface

Dans `inventory/prod/host_vars/site1-dns01.yml` :

```yaml
---
ansible_host: 192.0.2.11
bind_listen_interfaces:
  - name: ens192
```

Dans `inventory/prod/host_vars/site1-dns02.yml` :

```yaml
---
ansible_host: 192.0.2.12
bind_listen_interfaces:
  - name: ens192
```

Ne pas déclarer séparément `bind_front_ip` ou `bind_back_ip`. Le rôle résout
FRONT et BACK vers l'IPv4 de l'écoute unique et conserve le routage existant.
Avec plusieurs IPv4 sur cette interface, ajouter `ipaddress` pour éviter toute
ambiguïté. `name` doit toujours correspondre à une interface locale active.

## Plusieurs interfaces avec une seule écoute DNS

Exemple primaire, ADMIN sur l'interface portant `10.10.1.11`, DNS sur `ens192` :

```yaml
---
ansible_host: 10.10.1.11
bind_listen_interfaces:
  - name: ens192
```

Le secondaire utilise `ansible_host: 10.10.1.12` et sa propre interface DNS.
L'interface DNS doit porter une seule IPv4 utilisable si seul son nom est donné.
FRONT et BACK sont résolus vers cette écoute, également utilisée entre les pairs.
Ne pas conserver d'anciennes IP FRONT/BACK différentes de cette adresse.

## Plusieurs écoutes DNS et ADMIN dédié

Exemple primaire :

```yaml
---
ansible_host: 10.10.1.11
bind_listen_interfaces:
  - name: ens256
    ipaddress: 192.0.2.11
    scope: front
  - name: ens224
    ipaddress: 10.20.1.11
    scope: back
```

Le secondaire déclare respectivement `10.10.1.12`, `192.0.2.12` et
`10.20.1.12`. Les deux IP DNS doivent appartenir à deux interfaces d'écoute
sélectionnées. L'interface ADMIN est retrouvée grâce à son IPv4 locale ; aucun
nom d'interface ADMIN supplémentaire n'est à déclarer.

Avec seulement deux interfaces, utiliser par exemple `ansible_host: 192.0.2.11`
sur le primaire et `ansible_host: 192.0.2.12` sur le secondaire : ADMIN partage
FRONT. BIND conserve ses deux écoutes, mais le rôle n'ajoute aucune route FRONT.

Déclarer exactement une entrée `scope: front` et une entrée `scope: back` avec
plusieurs écoutes. Une troisième écoute DNS ou davantage peut être ajoutée sans
champ `scope`. Les
adresses supplémentaires suivent les autorisations de requête communes ; la
récursion et le cache restent limités à loopback et aux rôles FRONT/BACK selon
les listes clientes. Voir [les ACL](ACL_SERVEURS.md).

## Plusieurs IPv4 et connexion SSH par nom

Si `ens192` porte plusieurs IPv4, choisir explicitement celle utilisée par DNS :

```yaml
---
ansible_host: 10.10.1.11
bind_listen_interfaces:
  - name: ens192
    ipaddress: 192.0.2.111
    scope: front
  - name: ens224
    ipaddress: 10.20.1.11
    scope: back
```

`ipaddress` sélectionne une adresse déjà configurée sur l'interface indiquée ;
`scope` attribue son rôle DNS. Une interface ou un scope FRONT/BACK en doublon,
un champ inconnu, un scope autre que `front`/`back`, une liste vide ou une IPv4
absente de l'interface sont refusés. `scope: admin` n'est pas une écoute DNS :
ADMIN est identifié séparément par l'adresse locale de la connexion SSH.

Avec une destination SSH par nom ou une traduction d'adresse :

```yaml
ansible_host: dns01.example.test
bind_admin_ip: 10.10.1.11
bind_listen_interfaces:
  - name: ens256
    ipaddress: 192.0.2.11
    scope: front
  - name: ens224
    ipaddress: 10.20.1.11
    scope: back
```

Ces deux variables ont des fonctions distinctes. `bind_admin_ip` doit désigner
une IPv4 réellement présente sur la cible, pas l'adresse du contrôleur ni la
passerelle. Il est inutile de la répéter si `ansible_host` est déjà cette IPv4.

La liste d'objets est le seul format de sélection. Les anciennes listes de noms,
tables nom → IPv4 et sélections par variables FRONT/BACK séparées sont retirées.
Migrer les deux pairs ensemble ; toutes leurs adresses restent conservées dans
les entrées `ipaddress`. Les identités FRONT/BACK utilisées en interne sont
calculées après vérification de la topologie ; elles ne sont pas des déclarations
supplémentaires à fournir dans les host_vars.

## Paire ISO et variables communes

Les deux serveurs d'un même site doivent avoir le même nombre d'interfaces IPv4
actives utilisables, le même nombre d'écoutes DNS et la même affectation d'ADMIN
(FRONT, BACK, autre écoute ou interface dédiée). Les noms d'interfaces et les IP
peuvent différer. Les adresses de service des deux pairs doivent rester distinctes.

Conserver les paramètres communs dans `inventory/prod/group_vars/site1.yml` :
listes clientes, zones et réglages de site. Les paramètres d'écoute et l'adresse
SSH propres à chaque serveur vont dans son `host_vars`. Cette séparation évite
les valeurs réseau héritées qui contredisent un changement de topologie.

Le contrat des clients reste :

```yaml
bind_trusted_networks:
  cloud_clients: []
  internal_clients:
    - 10.30.0.0/16
```

Les deux listes sont vides par défaut ; au moins une doit être remplie pour un
déploiement. Les clients internes utilisent le routage existant, sans route
ajoutée. Seules les destinations cloud peuvent alimenter le mécanisme FRONT.

## Routage et pare-feu

`bind_front_routes_enabled` reste `false` par défaut. Une paire multi-interface
ne nécessite pas automatiquement des routes supplémentaires. Si elles sont
nécessaires, activer ce booléen YAML dans les variables de site, uniquement avec
FRONT, BACK et ADMIN sur trois interfaces distinctes. La passerelle FRONT est
recherchée dans les routes de cette interface ; aucune passerelle d'ADMIN, de
BACK ou globale n'est utilisée comme repli. Voir [les routes FRONT](FRONT_ROUTES.md).

BIND écoute sur loopback et les adresses DNS sélectionnées. Une interface ADMIN
dédiée non sélectionnée ne devient pas une écoute DNS. Une autorisation
firewalld porte toutefois sur une zone entière : une zone partagée avec ADMIN
demande une attention particulière. Voir [firewalld](FIREWALLD.md).

## Appliquer les exemples

1. Sauvegarder les deux `host_vars` et les variables de site actuellement utilisés.
2. Choisir un seul dossier sous `examples/interfaces/` et remplacer les deux
   `host_vars` par cette paire, en adaptant les IP et noms aux serveurs réels.
3. Retirer les anciennes déclarations séparées, y compris celles héritées
   de `group_vars`. Ne pas ajouter les variantes à côté des fichiers actifs.
4. Sur chaque serveur, comparer la déclaration avec la sortie de
   `ip -o -4 address show up`, puis contrôler le routage existant avec
   `ip -4 route show table main`.
5. Depuis la racine du projet, contrôler les deux points d'entrée :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --syntax-check
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-management.yml --syntax-check
```

Un contrôle de syntaxe ne vérifie pas la présence des IP sur les serveurs. Les
hôtes d'exemple sont fictifs ; aucune commande de déploiement n'est déclenchée
par ces fichiers. Le déploiement réel observe les deux pairs et refuse une paire
non ISO avant d'appliquer la configuration DNS, même avec `--limit` sur un seul
hôte. Une simulation globale `--check` ne remplace pas la recette de déploiement.

Pour revenir à l'inventaire précédent, restaurer ensemble les deux `host_vars`
et leurs variables de site sauvegardées. Cela ne retire aucune route déjà
committée : leur reprise suit la procédure dédiée aux routes FRONT.

---

Documentation DNSZIBO · contenu actualisé le 25 novembre 2026.
