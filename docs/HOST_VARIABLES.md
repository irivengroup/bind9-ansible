# Séparation des variables d’inventaire

`inventory/hosts.ini` contient uniquement les groupes et les noms d’hôtes.

`inventory/host_vars/<host>.yml` définit `ansible_host`, `bind_front_ip`, `bind_back_ip`, `bind_front_interface`, `bind_back_interface`.

`inventory/group_vars/site1.yml` et `site3.yml` définissent les politiques et les paramètres TSIG communs au site. **Aucune IP de serveur DNS n’y est déclarée.**

`tasks/tasks.d/site_peers.yml` détermine les noms du Primary et du Secondary par intersection entre le groupe du site et les groupes `dns_primary` et `dns_secondary`, puis résout leurs IP depuis `hostvars`. Les noms dérivés `bind_primary_back_ip`, `bind_secondary_back_ip`, `bind_primary_front_ip`, `bind_secondary_front_ip` sont maintenus en interne pour compatibilité des templates ; ils ne sont plus des variables à renseigner.

Les destinations de forwarding (IP des DNS amont) restent des paramètres fonctionnels globaux dans `defaults/main.yml`, distincts des IP des hôtes DNSZIBO.
