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

Les inventaires dev/preprod/prod existants gardent leurs adresses et leur
sélection FRONT/BACK par IP. Aucun nom d’interface fictif ne leur est imposé.
Pour une seule écoute, supprimer les anciennes valeurs FRONT/BACK incompatibles,
y compris celles héritées de group_vars. Une liste d’interfaces vide est refusée.

La sélection des interfaces ne configure pas leurs IP. Les routes FRONT restent
désactivées par défaut ; elles ne s’appliquent qu’avec trois interfaces distinctes
et seulement aux clients cloud. Aucun ajout pour les clients internes.

Voir [la procédure complète](../../docs/wiki.d/configuration/reseau/INTERFACES_RESEAU.md), la règle de paire
ISO et les contrôles à effectuer avant déploiement. Les noms de serveur de ce
dossier doivent correspondre aux noms d’inventaire si ces fichiers sont copiés.
