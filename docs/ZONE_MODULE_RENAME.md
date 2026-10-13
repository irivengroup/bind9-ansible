# Modules des zones DNS

- `10-authoritative-zones.conf` : déclarations primaires/secondaires des zones autoritaires dans la vue `peer`.
- `15-external-authoritative-zones.conf` : références `in-view "peer"` depuis la vue `external` (y compris la zone racine locale).
- `20-forward-zones.conf` : exclusivement les zones `type forward`, dans la vue `external`.

Les inclusions sont définies dans `40-views.conf`. Aucun transfert de zone n'est autorisé depuis la vue `external`.
