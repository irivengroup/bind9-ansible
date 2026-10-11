# Sauvegarde et récupération

Le déploiement est non destructif. Une archive initiale est créée dans `bind_backup_root` et son chemin enregistré dans `bind_migration_state_dir/baseline.path`.
L'archive conserve ACL, attributs étendus, contextes SELinux et propriétaires. Le SHA-256 est enregistré à côté de l'archive.

Avant restauration, arrêter le service, vérifier l'archive et planifier la fusion des fichiers modifiés depuis la sauvegarde. La restauration automatique destructive est volontairement désactivée.
