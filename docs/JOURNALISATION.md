# Journalisation BIND et contexte SELinux

La configuration canonique active trois journaux sous `/var/log/named`.
L’ACL cliente s’appelle désormais `user_defined_clients` ; sa source reste
le fait Ansible calculé `bind_allowed_clients`, fusion des listes cloud et
internes. Aucun paramètre DNSSEC, réseau ou de routage n’est renommé.

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
les liens symboliques sur ces chemins gérés.

Quand SELinux est activé, le rôle :

1. Lit les règles avec `semanage fcontext -l`, en locale C.
2. Ajoute la règle tous types `/var/log/named(/.*)?` vers `named_log_t`, ou
   corrige cette règle exacte si elle existe avec un autre type.
3. Réutilise une règle exacte déjà correcte sans nouvelle mutation.
4. Applique `restorecon -RF` au répertoire et à son contenu, ainsi qu’aux
   chemins BIND déjà gérés.
5. Vérifie le type `named_log_t` sur le répertoire et les trois fichiers.

Cette association persistante couvre les fichiers créés lors des rotations
et les futurs relabels. Les outils SELinux proviennent du paquet
`policycoreutils-python-utils`, déjà installé par le projet. Aucune
collection ni dépendance supplémentaire n’est ajoutée. Aucun changement
de mode enforcing ni autorisation SELinux globale n’est effectué.

Le démon utilise son comportement natif pour les permissions des nouveaux
fichiers après rotation. Le laboratoire observe notamment `0644` pour le
nouveau `default.log` avec le masque natif ; le répertoire `0750` limite
toujours l’accès à named, son groupe et root. Les fichiers préparés par
Ansible sont remis en `0640` au prochain déploiement. Aucun réglage global
de masque du service n’est introduit.

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
sudo named-checkconf -z /etc/named.conf
sudo ls -ldZ /var/log/named
sudo ls -lZ /var/log/named/default.log /var/log/named/security.log /var/log/named/xfer.log
sudo semanage fcontext -l -C
sudo tail -n 50 /var/log/named/default.log
sudo tail -n 50 /var/log/named/security.log
sudo tail -n 50 /var/log/named/xfer.log
sudo rndc -s 127.0.0.1 -k /etc/rndc.key status
sudo journalctl -u named --since '15 minutes ago' --no-pager
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

## Parties impactées

| Fichier | Modification nécessaire |
|---|---|
| `roles/bind9/templates/named.conf.j2` | Canaux, catégories, désactivation explicite des requêtes ; renommage de l’ACL et de ses deux références |
| `roles/bind9/tasks/tasks.d/directories.yml` | Répertoire privé et préparation non destructive des trois fichiers |
| `roles/bind9/tasks/tasks.d/path_safety.yml` | Protection des nouveaux chemins contre les liens symboliques |
| `roles/bind9/tasks/tasks.d/security.yml` | Règle SELinux persistante, relabel récursif et contrôle des labels |
| `roles/bind9/tasks/main.yml`, `roles/bind9/tasks/tasks.d/firewall.yml` | Import et ajout idempotent du service DNS runtime/permanent dans les zones FRONT/BACK ; détails dans [FIREWALLD.md](FIREWALLD.md) |
| `examples/deux-zones-forwardees/` | Deux configurations régénérées et texte aligné sur l’ACL |
| `tests/` | Tests des tâches, de l’idempotence, des canaux réels, de la rotation et de l’ACL ; adaptation des chemins du laboratoire et du test restorecon |
| `.github/workflows/ci.yml` | Ajout du script d’intégration logging au contrôle de syntaxe shell existant |
| `README.md`, `docs/OPERATIONS.md`, `docs/ACL_SERVEURS.md`, `docs/CLIENTS_UNIQUES.md`, `docs/VALIDATION_RHEL8.md` | Exploitation et contrat d’ACL alignés |

Le point d’entrée, les routes, scripts Python de production, handlers
et variables publiques restent ceux de la livraison précédente. Le tag
`firewall` et sa gestion sont ajoutés à la demande. Les tests
pytest ajoutés sont découverts par la CI existante ; la CI distante n’a pas
été exécutée. Les métadonnées et le manifeste décrivent le diff exact.

## Vérifications et limites

Les résultats exacts courants sont enregistrés dans `DELIVERY.json`.

La recette locale de cette livraison comprend 489 tests contrôleur sous
chacun des moteurs Jinja 2.10.3 et 3.0.3, puis 12 workflows de fichiers/SELinux
et 8 workflows firewalld sous Jinja 2.10.3, soit 509 tests distincts avec ce
moteur. Les deux scénarios BIND de journalisation s’ajoutent aux laboratoires
DNS existants, chacun 109 configurations et 282 contrôles DNS.

- **EXÉCUTÉ** : tests contrôleur Ansible 2.9.27, Jinja 2.10.3 et 3.0.3,
  vérification de la règle exacte, de ses trois branches et des labels,
  rendus de l’ACL pour les deux rôles et les différentes listes de clients.
- **EXÉCUTÉ** : vrai runner Ansible et vrais modules de fichiers, avec
  doubles `semanage`/`restorecon`/`ls` : préservation des données et dates,
  idempotence, SELinux désactivé et erreurs explicites de politique/label.
- **EXÉCUTÉ** : BIND EL8 9.11.36 et BIND 9.18.24, dans des espaces réseau
  isolés, exécutés sous l’utilisateur non privilégié `nobody` : trois
  fichiers écrits, refus et AXFR TSIG journalisés, requêtes désactivées,
  rotation réelle au seuil de 10 Mio et suppression de la plus ancienne
  des cinq archives. Les chemins seuls sont adaptés au laboratoire.
- **EXÉCUTÉ** : configurations et essais DNS existants, exemples primaire
  et secondaire contrôlés par le parseur natif, syntaxe Ansible, Pyflakes,
  syntaxe shell et inspection des références.
- **NON EXÉCUTÉ** : SELinux enforcing réel et domaine `named_t` sur RHEL,
  unités système réelles, disque/saturation, charge et durée de rétention
  observée. Raison : aucun accès à vos serveurs. Prérequis : paire de
  recette RHEL 8, outils SELinux, espace disque et sauvegardes. Procédure :
  déployer, effectuer les commandes ci-dessus, provoquer des refus et des
  transferts de recette, contrôler les AVC puis répéter le déploiement.
  Résultat attendu : écritures par named, labels persistants après rotation
  et relabel, absence d’AVC lié aux logs, historique conservé. Risques :
  politique locale, options de service, système de fichiers et charge réelle.

La qualification sur vos serveurs reste **NON TERMINÉE**. Les procédures
générales de recette et de rollback restent dans
[VALIDATION_RHEL8.md](VALIDATION_RHEL8.md) et [OPERATIONS.md](OPERATIONS.md).
Pour la recette isolée, sur un contrôleur Linux avec Ansible et les outils
BIND de test préparés comme dans le rapport de validation :

```bash
python -m pytest -q tests/test_logging.py tests/test_logging_ansible.py tests/test_user_defined_client_acl.py
sudo bash tests/run_integration_logging.sh --bin-dir /opt/recette/bind/bin --native-dir /opt/recette/bind/native --named /opt/recette/bind/sbin/named
```

Le second test nécessite root, `unshare`, `ip`, l’utilisateur `nobody` et
les binaires BIND. Il ne modifie ni les interfaces ni la politique SELinux
du système hôte. Les particularités BIND `querylog`/canaux suivent les
[recommandations ISC](https://kb.isc.org/docs/aa-01526).
