# Journalisation BIND et contexte SELinux

[Documentation](../../../index.md) › [Configuration](../index.md) › [Configuration BIND](index.md) › Journalisation BIND et contexte SELinux

[Retour au Parent](index.md)

## Sommaire

- [Fichiers et catégories](#fichiers-et-catégories)
- [Création, permissions et SELinux](#création-permissions-et-selinux)
- [Déploiement et exploitation](#déploiement-et-exploitation)
- [Conservation et supervision](#conservation-et-supervision)

## Fichiers et catégories

| Fichier | Catégories | Niveau | Rotation native BIND |
|---|---|---|---|
| `/var/log/named/default.log` | `default`, `general`, `config` | `info` et plus sévère | 5 archives, seuil 10 Mio par fichier |
| `/var/log/named/security.log` | `security`, `dnssec` | `info` et plus sévère | 5 archives, seuil 10 Mio par fichier |
| `/var/log/named/xfer.log` | `xfer-in`, `xfer-out` | `info` et plus sévère | 3 archives, seuil 5 Mio par fichier |

Les deux premiers canaux impriment date, catégorie et sévérité ; le canal
de transfert imprime la date. Les catégories non redéfinies suivent le
canal `default`, sous réserve des comportements propres à BIND.
`dnssec` est ajouté au canal sécurité pour que les événements de cette
catégorie ne tombent pas dans le seul journal général.

`querylog no;` désactive explicitement la journalisation des requêtes.
La catégorie `queries` est dirigée vers le canal `queries_null { null; }`.
Le serveur continue de répondre normalement ; les refus peuvent toujours
apparaître dans `security.log`. Aucun fichier de requêtes n’est créé.
L’ancien canal personnalisé `default_debug` vers `data/named.run` est
remplacé par ces canaux. Le template n’associe plus les catégories usuelles
au syslog : leurs messages BIND vont dans les fichiers ci-dessus. Les
messages de démarrage précoces et ceux des unités restent consultables
dans le journal système. Les contrôles de santé et le renouvellement
DNSSEC conservent leurs journaux systemd.

`versions` compte les **archives**, auxquelles s’ajoute le fichier actif.
Le volume nominal représente donc environ 60 Mio pour chacun des deux
premiers canaux et 20 Mio pour les transferts, soit environ 140 Mio au
total. Une écriture peut dépasser légèrement le seuil. Ce réglage ne
garantit aucune durée de rétention en jours ; elle dépend du volume de
messages. La rotation est gérée par BIND, sans ajouter une seconde règle
logrotate pour les mêmes fichiers. [Référence ISC](https://bind9.readthedocs.io/en/v9.16.41/reference.html#the-channel-phrase)

## Création, permissions et SELinux

Le rôle crée `/var/log/named` en `named:named 0750`, puis prépare les trois
fichiers en `named:named 0640`. Les fichiers existants ne sont pas tronqués
et leurs dates d’accès/modification sont conservées. Le préflight refuse
les liens symboliques sur ces chemins gérés. Les tâches d’écriture des
répertoires refusent aussi un lien introduit après ce contrôle initial.

Quand SELinux est activé, le rôle :

1. Lit les règles avec `semanage fcontext -l`, en locale C.
2. Ajoute la règle tous types `/var/log/named(/.*)?` vers `named_log_t`, ou
   corrige cette règle exacte si elle existe avec un autre type.
3. Réutilise une règle exacte déjà correcte sans nouvelle mutation.
4. Applique `restorecon -RF` au répertoire et à son contenu, ainsi qu’aux
   chemins BIND déjà gérés.
5. Vérifie `named_log_t` sur chacun des quatre chemins exacts, sans doublon
   ni correspondance partielle de type ou de chemin.
6. Exige une étiquette `named_conf_t` pour le seul `/etc/named.conf`.
   Une étiquette absente ou une sortie ambiguë est refusée.

Cette association persistante couvre les fichiers créés lors des rotations
et les futurs relabels. Les outils SELinux proviennent du paquet
`policycoreutils-python` sur EL7 et `policycoreutils-python-utils` sur EL8+, installés par le rôle. Aucune
collection ni dépendance supplémentaire n’est ajoutée. Aucun changement
de mode enforcing ni autorisation SELinux globale n’est effectué.

Le drop-in `bind9-runtime.conf` fixe `UMask=0027` pour le seul service
`named`. Les fichiers recréés lors des rotations conservent ainsi le mode
`0640`. Le handler existant redémarre le service lorsque ce drop-in change,
puis conserve le chemin de rechargement natif lorsque l’unité est inchangée.
L’essai avant/après de cette correction est conservé dans la [campagne de journalisation](../../historique/CAMPAGNES_CONFIGURATION.md#journalisation). Le répertoire reste en `0750`.

Les journaux peuvent contenir des IP clientes, des noms demandés lors de
refus et des informations opérationnelles. Leur accès et leur conservation
relèvent des opérateurs autorisés. Aucun export central ni filtre Fail2ban
n’est installé par cette modification.

## Déploiement et exploitation

Le parcours habituel reste `playbooks/bind9-deploy.yml`, primaire avant
secondaire. Les répertoires et fichiers sont préparés avant la configuration,
les labels sont appliqués avant `named-checkconf` puis le démarrage/reload.
Une erreur de politique ou de label bloque le parcours et déclenche la
reprise DNS lorsqu’elle se produit dans le bloc protégé.

Après déploiement sur chaque serveur :

```bash
getenforce
sudo ls -Zd /var/log/named /var/log/named/default.log \
  /var/log/named/security.log /var/log/named/xfer.log
sudo semanage fcontext -l | grep -F '/var/log/named'
sudo named-checkconf -z /etc/named.conf
sudo semanage fcontext -l -C
sudo rndc -s 127.0.0.1 -k /etc/rndc.key status
sudo tail -n 100 /var/log/named/default.log
sudo tail -n 100 /var/log/named/security.log
sudo tail -n 100 /var/log/named/xfer.log
sudo journalctl -u named --since '-30 minutes' --no-pager
sudo du -sh /var/log/named /var/backups/Bind9-Ansible/bind
sudo ausearch -m AVC,USER_AVC -ts recent
```

Attendu : configuration chargée, type `named_log_t` sur les quatre chemins,
service actif, `query logging is OFF`, activité générale dans `default.log`,
refus et événements DNSSEC disponibles s’ils surviennent, transferts dans
`xfer.log` lorsqu’ils ont lieu. Un fichier sécurité/transfert vide peut être
normal en l’absence d’événements. Les rotations portent les suffixes `.0`,
`.1`, etc. Ne pas publier la sortie de `named-checkconf -p`, qui contient
le secret TSIG.

L’archive de sauvegarde BIND ne sauvegarde pas `/var/log/named` ni la base
SELinux. Le rollback DNS conserve les journaux et la règle de contexte,
afin de ne pas effacer les traces. Avant une intervention de politique,
conserver le relevé des règles locales ; une restauration éventuelle de
ces règles doit tenir compte de leur état antérieur. Le rôle ne retire
pas automatiquement une règle potentiellement préexistante.


## Conservation et supervision

Les journaux ne garantissent aucune rétention d’un mois ou quotidienne. `ansible.cfg` ne définit pas de `log_path` ; conserver les comptes rendus Ansible avec votre mécanisme protégé. Le [moniteur périodique](../../exploitation/SUPERVISION.md) garde ses unités systemd existantes ; il ne fournit ni collecte centrale ni notification externe.

---

Documentation DNSZIBO · contenu harmonisé le 25 novembre 2026.
