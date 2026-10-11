# Gestion incrémentale non destructive

Le rôle n'efface plus `/etc/named.conf`, `/etc/named` ni `/var/named`.

- Avant la première modification, une archive de référence (ACL, xattrs, SELinux, propriétaires) est créée sous `bind_backup_root` et vérifiée par SHA-256.
- Les templates Ansible actualisent uniquement les fichiers gérés qui diffèrent et créent les fichiers absents.
- Les fichiers tiers non gérés restent sur disque, mais ne sont **pas** automatiquement inclus dans `named.conf`.
- Les zones primaires sont mises à jour par template. Les fichiers secondaires sont conservés et alimentés par BIND.
- Les fichiers d'une zone retirée de la liste restent sur disque; ils ne sont plus déclarés dans les includes générés.
- En cas d'échec de `named-checkconf`, Ansible arrête le déploiement; il ne supprime aucun ancien fichier. Une restauration manuelle peut être réalisée à partir de l'archive de référence.

**Attention** : le template `/etc/named.conf` reste géré dans son intégralité. Les directives personnalisées présentes dans l'ancien fichier sont sauvegardées, mais pas automatiquement fusionnées. Les reporter dans les variables ou templates avant activation. Vérifier aussi les conflits éventuels avec les fichiers historiques.
