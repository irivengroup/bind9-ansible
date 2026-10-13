# Bind9-Ansible

Déploiement déclaratif BIND 9 sur RHEL 8+, compatible Python 3.8+, avec inventaires indépendants par environnement et par site.

## Architecture

- **Un seul template BIND** : `roles/bind9/templates/named.conf.j2` produit `/etc/named.conf` ; aucun fichier de configuration `include` supplémentaire.
- **Données de zones** : fichiers primaires `/var/named/zones/*.zone`, fichiers secondaires `/var/named/slaves/` ; génération automatique des PTR.
- **Vues** : `peer` (réplication Primary/Secondary TSIG sur BACK), `external` (clients FRONT), `internal` (clients BACK), `deny` (refus par défaut).
- **Sécurité** : récursion et transferts interdits globalement, transferts TSIG sur les zones primaires de la vue `peer`, NOTIFY TSIG, secrets sensibles protégés par permissions et `no_log`.
- **Sauvegardes / restauration seulement** : sauvegarde complète avant déploiement, SHA-256, cinq archives conservées, restauration de l'archive de ce déploiement en cas d'échec. Aucun snapshot transactionnel.
- **Routage** : routes persistantes vers les clients FRONT via `bind-front-routes`, sans changer la route par défaut BACK.

## Inventaires

```text
inventory/
  prod/{hosts.ini,group_vars/,host_vars/}
  dev/{hosts.ini,group_vars/,host_vars/}
  preprod/{hosts.ini,group_vars/,host_vars/}
```

`bind_environment` et `bind_site_name` isolent les couples environnement/site. Les variables `bind_authoritative_zones`, `bind_forward_zones`, `bind_frontend_clients`, `bind_backend_clients` et la clé TSIG sont déclarées dans les variables du site. Les adresses ADM (`ansible_host`), FRONT et BACK sont déclarées dans les variables des hôtes.

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --tags deploy
ansible-playbook -i inventory/dev/hosts.ini playbooks/bind9-deploy.yml --limit site1 --tags deploy
```

Les exemples d'adresses et de zones doivent être remplacés avant production. Une seule arborescence d'inventaire par exécution.

## Contrôles

```bash
python -m pytest -q tests
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --syntax-check
sudo named-checkconf -z /etc/named.conf
```

Pour les commandes de backup/restore, les précautions de migration et les limites du rollback, consulter [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Correctifs sécurité — audit d'octobre 2026

- Le gestionnaire `bind-front-routes` refuse les routes IPv4 plus spécifiques via une autre interface, vérifie que la passerelle FRONT est directement joignable et enregistre les routes NetworkManager précédentes dans un état protégé. Le déploiement exécute `--rollback` en cas d'échec et `--commit` uniquement après validation de BIND. **Limites :** les routes de tables de routage alternatives, les règles de policy routing et les modifications réseau concurrentes nécessitent un contrôle d'exploitation distinct ; les routes ajoutées lors d'un déploiement réussi restent persistantes.
- `bind-configtool` crée des archives avec suffixe aléatoire pour éviter les collisions temporelles. Au restore, seuls les fichiers `.zone` portant le marqueur `Bind9-Ansible-MANAGED-ZONE` et absents de l'archive sont supprimés ; aucun fichier de zone non marqué n'est supprimé. Les archives incluent toujours les données de zones secondaires : une sauvegarde à chaud ne garantit pas un instantané atomique des journaux BIND ; une qualification opérationnelle des secondaires est requise.
- Les forwarders doivent être des adresses IP littérales valides ; les valeurs A/AAAA sont vérifiées avant génération, et les caractères de contrôle sont refusés dans les RDATA.

### Validation AXFR FRONT obligatoire avant production

Le partage des zones via `in-view "peer"` ne permet pas de **garantir statiquement** le refus d'un AXFR sur FRONT avec une clé TSIG valide. Depuis un client réellement autorisé dans `bind_frontend_clients`, lancer :

```bash
bind-configtool check-front-axfr <FRONT_IP> <ZONE> <IP_CLIENT_FRONT> /chemin/protege/tsig.key
```

La sous-commande `check-front-axfr` est intégrée à `bind-configtool` et doit être exécutée depuis un client FRONT autorisé (pas depuis le serveur DNS). Elle ne nécessite pas les privilèges root si le fichier TSIG est lisible. La commande refuse de conclure si le test signé n'a pas été réalisé ou si l'échec est ambigu. **Un succès AXFR signé sur FRONT bloque la mise en production** : dans ce cas il faut revoir la séparation des zones et la politique BIND sur la version exacte de RHEL, et non contourner le test. Ne jamais exposer la clé TSIG en argument `dig -y` ou dans les logs CI.

Les vérifications `named-checkconf -z`, AXFR/IXFR/NOTIFY, SELinux et idempotence sur RHEL 8/9/10 n'ont pas été exécutées dans cet environnement.

Le fichier source `roles/bind9/files/bind-configtool.sh` conserve son extension Bash, mais est installé comme `/usr/local/sbin/bind-configtool` (sans extension). L’ancien exécutable avec extension est supprimé pendant le déploiement.

### Sauvegardes et rétention

Chaque déploiement crée une archive UTC distincte avant les modifications BIND. Après chaque sauvegarde réussie, les archives âgées de plus de 30 jours et leurs empreintes SHA-256 sont supprimées. Aucune limite en nombre. La commande installée reste `bind-configtool`.
