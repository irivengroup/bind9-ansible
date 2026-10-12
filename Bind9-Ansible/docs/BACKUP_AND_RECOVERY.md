# Sauvegarde et récupération

La tâche actuelle appelle `backup` et conserve les arborescences existantes. Elle ne crée pas de baseline via `snapshot` et ne consomme pas `bind_migration_state_dir`. Les archives complètes sont écrites dans `bind_backup_root`.
L'archive conserve ACL, attributs étendus, contextes SELinux et propriétaires. Le SHA-256 est enregistré à côté de l'archive.

Avant restauration, arrêter le service, vérifier l'archive et conserver une copie externe de l'état à protéger. La restauration complète extrait par-dessus l'existant ; elle ne supprime pas tous les fichiers supplémentaires. Le rollback automatique des fichiers gérés est un mécanisme distinct.

## Sauvegardes complètes horaires (V4)

Avant toute modification de configuration, le rôle installe puis exécute
`/usr/local/sbin/bind-backup.sh backup --root /var/backups/Bind9Project/bind`.
Le mécanisme sauvegarde `/etc/named.conf`, `/etc/named/` et `/var/named/`
avec les ACL, attributs étendus, propriétaires et métadonnées SELinux (GNU tar).
Il réutilise la sauvegarde de l'heure UTC courante et conserve les cinq archives
les plus récentes, avec SHA-256. Une archive manquante ou corrompue fait échouer
le déploiement avant les changements de configuration.

Liste :

```bash
sudo /usr/local/sbin/bind-backup.sh list
```

Restauration manuelle (arrêter les modifications concurrentes avant l'opération) :

```bash
sudo systemctl stop named
sudo /usr/local/sbin/bind-backup.sh restore --archive named-config-YYYYMMDDTHHMMSSZ.tar.gz
sudo named-checkconf -z /etc/named.conf
sudo systemctl start named
```

La restauration vérifie SHA-256 et les chemins des membres, refuse les liens
symboliques, crée un instantané de sécurité temporaire, restaure les métadonnées,
valide `named-checkconf -z` et rétablit les fichiers précédents en cas d'échec.
Le rollback transactionnel existant (`named-transaction`) demeure indépendant.

**Attention :** les sauvegardes horaires ne constituent pas un journal des
modifications : plusieurs changements durant la même heure partagent la même
sauvegarde de référence. Pour revenir exactement à l'état pré-déploiement,
utiliser le rollback transactionnel.

Implémentation : `bind-backup.sh` est un script Bash qui implémente directement `backup`, `list` et `restore`. La branche historique `snapshot` n'est pas appelée par le rôle. Aucun timer de sauvegarde n'est installé : la granularité horaire concerne la réutilisation d'une archive lors des appels. Voir le README pour une procédure interactive sans nom d'archive fictif.
