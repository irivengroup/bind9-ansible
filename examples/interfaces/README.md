# Exemples de host_vars pour les interfaces DNS

Choisir une seule ligne du tableau. Chaque dossier fournit les deux host_vars
du site 1 ; ils remplacent les fichiers actifs, sans ajout de fichiers alternatifs
dans l’inventaire. Adapter les IP fictives et les noms des interfaces existantes.
Les variables communes et les secrets restent dans leurs emplacements habituels.

| Topologie | Primaire | Secondaire |
|---|---|---|
| Une interface pour DNS et SSH | [site1-dns01.yml](mono-interface/host_vars/site1-dns01.yml) | [site1-dns02.yml](mono-interface/host_vars/site1-dns02.yml) |
| Plusieurs interfaces et une écoute DNS | [site1-dns01.yml](ecoute-unique/host_vars/site1-dns01.yml) | [site1-dns02.yml](ecoute-unique/host_vars/site1-dns02.yml) |
| Deux écoutes et ADMIN partagé avec FRONT | [site1-dns01.yml](admin-partage/host_vars/site1-dns01.yml) | [site1-dns02.yml](admin-partage/host_vars/site1-dns02.yml) |
| Deux écoutes et ADMIN dédié | [site1-dns01.yml](admin-dedie/host_vars/site1-dns01.yml) | [site1-dns02.yml](admin-dedie/host_vars/site1-dns02.yml) |
| Sélection explicite avec plusieurs IPv4 | [site1-dns01.yml](plusieurs-ipv4/host_vars/site1-dns01.yml) | [site1-dns02.yml](plusieurs-ipv4/host_vars/site1-dns02.yml) |
| SSH par nom et IPv4 ADMIN explicite | [site1-dns01.yml](ssh-par-nom/host_vars/site1-dns01.yml) | [site1-dns02.yml](ssh-par-nom/host_vars/site1-dns02.yml) |

Tous ces fichiers et les inventaires dev/preprod/prod utilisent la même liste
d'objets `bind_listen_interfaces`. `name` est obligatoire ; `ipaddress` choisit
une IPv4 locale et `scope: front/back` désigne le rôle avec plusieurs écoutes.
Une entrée unique peut contenir seulement `name` si l'interface n'a qu'une IPv4
utilisable. Les adresses des inventaires sont conservées. Les noms des interfaces
restent des paramètres à adapter ; aucun n'est imposé dans le rôle.
Ne pas répéter `bind_front_ip` ou `bind_back_ip` dans les nouveaux host_vars,
y compris via group_vars. Une liste vide et les déclarations ambiguës sont refusées.

La sélection des interfaces ne configure pas leurs IP. Les routes FRONT restent
désactivées par défaut ; elles ne s’appliquent qu’avec trois interfaces distinctes
et seulement aux clients cloud. Aucun ajout pour les clients internes.

Voir [la procédure complète](../../docs/wiki.d/configuration/reseau/INTERFACES_RESEAU.md), la règle de paire
ISO et les contrôles à effectuer avant déploiement. Les noms de serveur de ce
dossier doivent correspondre aux noms d’inventaire si ces fichiers sont copiés.
