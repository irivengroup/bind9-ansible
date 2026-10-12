# Configuration Bind9-Ansible sous /etc/named

Les six modules BIND sont directement déployés sous `/etc/named/`.
`/etc/named.conf` contient uniquement les quatre inclusions de premier niveau.
Les fichiers `50-authoritative-zones.conf` et `60-forward-zones.conf` sont inclus depuis `40-views.conf` dans les vues appropriées.

Le déploiement est non destructif : les anciens fichiers dans `/etc/named/named.d/` sont laissés en place, mais ne sont plus référencés par les templates Bind9-Ansible. Avant mise en production, vérifier qu'aucun fichier externe ne continue à les inclure.
