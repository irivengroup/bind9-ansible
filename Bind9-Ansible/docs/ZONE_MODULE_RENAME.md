# Noms des modules de zones

- `50-authoritative-zones.conf` : zones DNS autoritaires, incluses dans la vue peer.
- `60-forward-zones.conf` : forwarding conditionnel et références `in-view`, inclus dans la vue external.

`/etc/named.conf` demeure limité aux quatre includes de premier niveau.

**Migration depuis une version déjà déployée :** les anciens fichiers `50-zones.conf` et `60-front-zones.conf` ne sont pas effacés automatiquement. Ils deviennent non référencés par les vues après mise à jour. Contrôler et archiver ces fichiers séparément après validation de la nouvelle configuration.
