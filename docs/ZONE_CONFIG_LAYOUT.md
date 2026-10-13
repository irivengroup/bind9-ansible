# Configuration des zones sous /etc/named/zones

`/etc/named.conf` inclut seulement les quatre modules principaux de `/etc/named/`.

`/etc/named/40-views.conf` inclut, à l'intérieur des vues BIND :

- `/etc/named/zones/10-authoritative-zones.conf` dans `peer` ;
- `/etc/named/zones/15-external-authoritative-zones.conf` puis `/etc/named/zones/20-forward-zones.conf` dans `external`.

Les **fichiers de configuration** des zones sont distincts des **données DNS** stockées sous `/var/named/zones` (primaires) et `/var/named/slaves` (secondaires).

Ansible crée les répertoires manquants, met à jour les fichiers gérés sans supprimer les anciens fichiers et préserve les fichiers non gérés. Les anciens fichiers ne sont plus référencés par les vues déployées.

