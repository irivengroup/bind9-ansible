# Opérations BIND 9 (RHEL 8+)

Le rôle déploie **un seul fichier de configuration** : `/etc/named.conf`.
Les fichiers de données des zones primaires restent dans `/var/named/zones/` et les zones secondaires dans `/var/named/slaves/`.
Aucun include de configuration `/etc/named/*.conf` n'est créé.
La vue locale inclut uniquement `/etc/named.rfc1912.zones` du paquet RHEL,
sans modifier ce fichier ni ses fichiers de zones. Voir [LOOPBACK.md](LOOPBACK.md).

## Sauvegarde et restauration

Une sauvegarde complète et **forcée à chaque déploiement** est créée **avant** toute modification de la configuration ou des zones, avec SHA-256 et rétention glissante de 30 jours (sans limite sur le nombre d’archives). En cas d'échec, Ansible restaure l'archive exacte de cette exécution puis redémarre `named`.

```bash
sudo /usr/local/sbin/bind-configtool list --root /var/backups/Bind9-Ansible/bind
sudo systemctl stop named
sudo /usr/local/sbin/bind-configtool restore --root /var/backups/Bind9-Ansible/bind --archive named-config-YYYYMMDDTHHMMSSZ-XXXXXXXX.tar.gz
sudo systemctl start named
```

La restauration valide le checksum SHA-256 et `named-checkconf -z`. Les archives incluent `/etc/named.conf`, `/etc/named` s'il existe, et `/var/named` ; elles contiennent donc aussi les données des zones. Les fichiers `.zone` absents de l'archive et portant en première ligne `Bind9-Ansible-MANAGED-ZONE` sont supprimés par l'outil. Les fichiers non marqués sont préservés ; l'extraction tar seule ne supprime pas ces fichiers supplémentaires.

En cas d'échec pris en charge par le bloc DNS d'un déploiement complet, les routes FRONT créées par cette exécution sont retirées avant cette restauration. Les routes préexistantes restent. Une panne de suppression conserve le journal et reste signalée ; une panne SSH ou un arrêt brutal peut demander `routes-recover` après inspection. La commande manuelle `restore` ci-dessus ne supprime pas les routes déjà committées. Voir [FRONT_ROUTES.md](FRONT_ROUTES.md).

## Migration depuis l'architecture modulaire

Les fragments **spécifiques au projet** de l'ancienne architecture (`/etc/named/10-tsig.conf`, `20-acl.conf`, `30-options.conf`, `40-views.conf` et les quatre fichiers `/etc/named/zones/*.conf` connus) sont supprimés après sauvegarde. Les fichiers BIND/RHEL non gérés par le projet sont préservés.

## Déploiement

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --tags deploy
ansible-playbook -i inventory/dev/hosts.ini playbooks/bind9-deploy.yml --limit site1 --tags deploy
```

Ne pas lancer isolément les tags `zones`, `records` ou `configure` : ils ne garantissent pas la sauvegarde et le contrôle de santé. Les seuls ajouts réseau concernent les routes IPv4 manquantes vers les clients FRONT. Le tag `routes` exécute uniquement cette opération ; voir [FRONT_ROUTES.md](FRONT_ROUTES.md). Les paramètres et routes existants sont conservés.

### Test de refus AXFR depuis FRONT

Depuis un client FRONT autorisé (et non depuis le serveur DNS), avec `dig` installé et accès à la clé TSIG protégée :

```bash
bind-configtool check-front-axfr FRONT_DNS_IP ZONE CLIENT_SOURCE_IP /chemin/protege/tsig.key
```

Code 0 : AXFR refusé explicitement en non signé et signé ; 1 : AXFR réussi (faille) ; 2 : contrôle incomplet ou ambigu.

Une sortie vide, une erreur réseau, `Transfer failed` seul ou une erreur TSIG ne constituent pas un refus prouvé. Certaines versions anciennes de dig n'affichent pas le RCODE AXFR ; une preuve explicite complémentaire est alors nécessaire. Ne pas conclure au succès de la recette sur un code 2.
