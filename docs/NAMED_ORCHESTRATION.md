# named.conf : orchestrateur exclusivement

`/etc/named.conf` est un point d'entrée déclaratif. Il ne contient **que** les quatre directives `include` des modules de niveau supérieur : TSIG, ACL, options et vues.

Les fichiers de zones sont inclus dans leurs vues respectives depuis `40-views.conf` :
- `50-authoritative-zones.conf` : vue `peer` ;
- `60-forward-zones.conf` : vue `external`.

Ne pas inclure ces deux fichiers directement depuis `named.conf` : les déclarations de zones dépendent du contexte de vue.

Les mises à jour sont non destructives : les templates gérés sont mis à jour si nécessaire, les fichiers absents sont créés, les autres fichiers sont préservés. La configuration doit passer `named-checkconf -z` avant activation.
