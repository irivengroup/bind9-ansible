# Opérations BIND 9 (RHEL 8+)

Le rôle déploie **un seul fichier de configuration** : `/etc/named.conf`.
Les fichiers de données des zones primaires restent dans `/var/named/zones/` et les zones secondaires dans `/var/named/slaves/`.
Aucun include de configuration `/etc/named/*.conf` n'est créé.

## Sauvegarde et restauration

Une sauvegarde complète et **forcée à chaque déploiement** est créée **avant** toute modification de la configuration ou des zones, avec SHA-256 et rétention glissante de 30 jours (sans limite sur le nombre d’archives). En cas d'échec, Ansible restaure l'archive exacte de cette exécution puis redémarre `named`.

```bash
sudo /usr/local/sbin/bind-configtool list --root /var/backups/Bind9-Ansible/bind
sudo systemctl stop named
sudo /usr/local/sbin/bind-configtool restore --root /var/backups/Bind9-Ansible/bind --archive named-config-YYYYMMDDTHHMMSSZ-XXXXXXXX.tar.gz
sudo systemctl start named
```

La restauration valide le checksum SHA-256 et `named-checkconf -z`. Les archives incluent `/etc/named.conf`, `/etc/named` s'il existe, et `/var/named` ; elles contiennent donc aussi les données des zones. Les éventuels fichiers créés après la sauvegarde et non présents dans l'archive ne sont pas automatiquement effacés par l'extraction tar.

## Migration depuis l'architecture modulaire

Les fragments **spécifiques au projet** de l'ancienne architecture (`/etc/named/10-tsig.conf`, `20-acl.conf`, `30-options.conf`, `40-views.conf` et les quatre fichiers `/etc/named/zones/*.conf` connus) sont supprimés après sauvegarde. Les fichiers BIND/RHEL non gérés par le projet sont préservés.

## Déploiement

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --tags deploy
ansible-playbook -i inventory/dev/hosts.ini playbooks/bind9-deploy.yml --limit site1 --tags deploy
```

Ne pas lancer isolément les tags `zones`, `records` ou `configure` : ils ne garantissent pas la sauvegarde et le contrôle de santé. La route par défaut BACK et les routes de retour FRONT restent gérées séparément du backup BIND.

### Test de refus AXFR depuis FRONT

Depuis un client FRONT autorisé (et non depuis le serveur DNS), avec `dig` installé et accès à la clé TSIG protégée :

```bash
bind-configtool check-front-axfr FRONT_DNS_IP ZONE CLIENT_SOURCE_IP /chemin/protege/tsig.key
```

Code 0 : AXFR refusé explicitement en non signé et signé ; 1 : AXFR réussi (faille) ; 2 : contrôle incomplet ou ambigu.
