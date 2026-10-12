# TSIG automatique — DNSZIBO

## Source de vérité

Les fichiers `inventory/group_vars/site1.yml` et `site3.yml` définissent
`bind_tsig_key_name`, `bind_tsig_algorithm` et `bind_tsig_secret_file`.
Le chemin `bind_tsig_secret_file` désigne **le contrôleur Ansible**, et non le serveur DNS.
La clé est générée une fois par site (32 octets aléatoires, HMAC-SHA256) puis réutilisée.

## Garanties

- `tsig-secret.py` sérialise la création par verrou `flock` et `O_EXCL`.
- Répertoire privé `0700`, secret `0600`, liens symboliques interdits.
- Clé identique sur les deux homologues d'un site, sans partage intersite.
- Les tâches de manipulation des secrets utilisent `no_log: true`.
- Le template `/etc/named/10-tsig.conf` est déployé avec propriétaire `root`, groupe `named`, mode `0640`.
- Le secret n'est pas inclus dans l'archive ZIP.

## Exploitation

1. Sauvegarder les secrets du contrôleur dans un coffre chiffré et restreint.
2. Conserver les fichiers entre les exécutions et changements de contrôleur.
3. Ne **jamais supprimer** les secrets pour tenter une rotation : cela désynchroniserait les homologues.
4. Pour une rotation sans interruption, préparer une deuxième clé, la déployer sur les deux pairs,
   basculer les références de transferts, vérifier AXFR/IXFR/NOTIFY puis retirer l'ancienne clé.
   **La rotation automatique n'est pas implémentée dans cette livraison.**
5. Ne pas exécuter le rôle en `--check` pour une première génération ; la création d'un secret est une opération réelle.

## Vérification

`ansible-inventory -i inventory/hosts.ini --host site1-dns01`
`ansible-playbook -i inventory/hosts.ini playbooks/site.yml --syntax-check`
`pytest -q`

La qualification réelle sur RHEL 8 reste obligatoire.
