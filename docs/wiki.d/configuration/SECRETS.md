# Secrets TSIG et Ansible Vault

[Documentation](../../index.md) › [Configuration](index.md) › Secrets TSIG et Ansible Vault

[Retour au Parent](index.md)

## Sommaire

- [TSIG, secrets et Ansible Vault](#tsig-secrets-et-ansible-vault)

## TSIG, secrets et Ansible Vault

### Cycle de vie de la clé

Au début de `deploy` ou `configure`, après validation clients/DNSSEC et identification des pairs, `tsig-secret.py` s’exécute sur le **contrôleur**, sans élévation. Il crée **32 octets aléatoires encodés en Base64** si le fichier manque, sinon vérifie et réutilise la clé existante. Le répertoire est `0700`, le fichier `0600`, avec contrôle des ancêtres/propriétaires, refus des liens et fichiers spéciaux, lecture bornée et verrou de 30 secondes. L’écriture complète et la relecture sont vérifiées avant publication atomique ; une écriture incomplète ou altérée est refusée avant de créer la clé définitive. La clé n’est pas régénérée à chaque passage.

Le helper crée les répertoires privés manquants après contrôle de chaque ancêtre. Un répertoire existant non sûr est refusé ; le rôle ne modifie plus ses permissions avant ces contrôles.

Les deux pairs doivent utiliser le même nom et le même chemin de clé ; chaque environnement/site a son propre secret. Le rôle le charge avec `no_log` puis le rend dans `/etc/named.conf`, `root:named 0640`. Configuration et sauvegardes sont donc sensibles.

**Base64 est un encodage ; l’authentification repose sur HMAC-SHA256 et le secret aléatoire de 256 bits.** Le fichier contrôleur contient seulement le Base64. `tsig-keygen` produit un bloc de clé BIND complet : ne pas placer ce bloc dans `bind_tsig_secret_file`. La clé RNDC native a un usage distinct.

Sauvegarder le secret contrôleur dans un stockage chiffré et restreint. Sa perte, son remplacement ou un changement de chemin peuvent désynchroniser les pairs. La rotation coordonnée avec coexistence de deux clés n’est pas automatisée : préparer un changement et un retour arrière des deux serveurs. Ne pas supprimer le fichier pour « forcer » une rotation en production.

### Usage exact de Vault

Vault peut protéger `ansible_password` et `ansible_become_password`. Le rôle **ne consomme pas `vault_tsig_secret`** et ne déchiffre pas un fichier TSIG brut chiffré par Vault. Ne pas appliquer `ansible-vault encrypt` à `bind_tsig_secret_file` : son lecteur attend du Base64 dans un fichier privé.

Sur le contrôleur, sans secret dans les arguments :

```bash
mkdir -p secrets
chmod 700 secrets
ansible-vault create secrets/prod.vault.yml
```

Saisir dans l’éditeur les seules variables de connexion/élévation nécessaires, puis :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --limit site1 --tags deploy -e @secrets/prod.vault.yml --ask-vault-pass
```

Garder `secrets` et les fichiers privés hors dépôt/ZIP. Privilégier l’authentification SSH par clé selon votre politique. Ne pas afficher la clé, utiliser `dig -y`, lancer `named-checkconf -p` ou exporter des variables complètes dans un ticket : ces sorties peuvent révéler les secrets. SHA-256 contrôle l’intégrité d’une archive ; il ne la chiffre pas et ne prouve pas seul sa provenance.

---

Documentation DNSZIBO · contenu harmonisé le 25 novembre 2026.
