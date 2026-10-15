# Opérations BIND 9 (RHEL 8+)

Le rôle déploie **un seul fichier de configuration** : `/etc/named.conf`.
Les fichiers de données des zones primaires restent dans `/var/named/zones/` et les zones secondaires dans `/var/named/slaves/`.
Aucun include de configuration `/etc/named/*.conf` n'est créé.
La configuration globale sans vues inclut `/etc/named.rfc1912.zones` du paquet RHEL,
sans modifier ce fichier ni ses fichiers de zones. Voir [LOOPBACK.md](LOOPBACK.md).

## Sauvegarde et restauration

Une sauvegarde des fichiers BIND, **forcée à chaque déploiement**, est créée **avant** toute modification de la configuration ou des zones, avec SHA-256 et rétention glissante de 30 jours (sans limite sur le nombre d’archives). En cas d'échec du bloc protégé, Ansible suspend les unités de renouvellement privées présentes, arrête named avant extraction, restaure l'archive exacte puis redémarre named. Le timer est relancé seulement s'il était actif avant cette restauration. Le rescue restaure aussi les six fichiers runtime capturés en mémoire avant la transaction, ainsi que l’état précédent du timer. Le backup ne couvre pas l'ensemble du système ; voir les limites de retour de version ci-dessous.

```bash
(
set -e
sudo /usr/local/sbin/bind-configtool list --root /var/backups/Bind9-Ansible/bind
renewal_was_active=false
if systemctl is-active --quiet named-private-dnssec.timer; then renewal_was_active=true; fi
for unit in named-private-dnssec.timer named-private-dnssec.service; do
  if systemctl cat "$unit" >/dev/null 2>&1; then sudo systemctl stop "$unit"; fi
done
sudo systemctl stop named
sudo /usr/local/sbin/bind-configtool restore --root /var/backups/Bind9-Ansible/bind --archive named-config-YYYYMMDDTHHMMSSZ-XXXXXXXX.tar.gz
sudo systemctl start named
if "$renewal_was_active"; then sudo systemctl start named-private-dnssec.timer; fi
)
```

Remplacer le nom d'archive par une entrée réellement retournée par `list`. Cette procédure suppose les unités de la présente livraison installées ; vérifier leur présence avant une restauration d'une autre version. Un échec de commande arrête la procédure : corriger sa cause avant de poursuivre.

La restauration contrôle le checksum SHA-256 et `named-checkconf -z`. Les archives incluent `/etc/named.conf`, `/etc/named` s'il existe, et `/var/named` ; elles contiennent donc aussi les données des zones. Les fichiers `.zone` absents de l'archive et portant en première ligne `Bind9-Ansible-MANAGED-ZONE` sont supprimés par l'outil. Les fichiers non marqués sont préservés ; l'extraction tar seule ne supprime pas ces fichiers supplémentaires.

En cas d'échec pris en charge par le bloc DNS d'un déploiement complet, les routes FRONT créées par cette exécution sont retirées avant cette restauration. Les routes préexistantes restent. Une panne de suppression conserve le journal et reste signalée ; une panne SSH ou un arrêt brutal peut demander `routes-recover` après inspection. La commande manuelle `restore` ci-dessus ne supprime pas les routes déjà committées. Voir [FRONT_ROUTES.md](FRONT_ROUTES.md).

## Migration depuis l'architecture modulaire

Les fragments **spécifiques au projet** de l'ancienne architecture (`/etc/named/10-tsig.conf`, `20-acl.conf`, `30-options.conf`, `40-views.conf` et les quatre fichiers `/etc/named/zones/*.conf` connus) sont supprimés après sauvegarde. Les fichiers BIND/RHEL non gérés par le projet sont préservés.

## Déploiement

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --tags deploy
ansible-playbook -i inventory/dev/hosts.ini playbooks/bind9-deploy.yml --limit site1 --tags deploy
```

Ne pas lancer isolément les tags `zones`, `records` ou `configure` : ils ne garantissent pas la sauvegarde et le contrôle de santé. La gestion des routes est désactivée par défaut (`bind_front_routes_enabled: false`). Lorsqu'elle est activée explicitement, les seuls ajouts réseau concernent les routes IPv4 manquantes vers les clients FRONT. Le tag `routes` exécute uniquement cette opération ; voir [FRONT_ROUTES.md](FRONT_ROUTES.md). Les paramètres et routes existants sont conservés.

### Test de refus AXFR depuis FRONT

Depuis un client FRONT autorisé (et non depuis le serveur DNS), avec `dig` installé et accès à la clé TSIG protégée :

```bash
bind-configtool check-front-axfr FRONT_DNS_IP ZONE CLIENT_SOURCE_IP /chemin/protege/tsig.key
```

Code 0 : AXFR refusé explicitement en non signé et signé ; 1 : AXFR réussi (faille) ; 2 : contrôle incomplet ou ambigu.

Cette interprétation concerne un client externe. Le secondaire BACK muni de sa clé est un pair autorisé, y compris lorsqu'il interroge l'adresse FRONT du primaire. La configuration sans vues ne filtre pas la destination des transferts ; voir [ACL_SERVEURS.md](ACL_SERVEURS.md).

Une sortie vide, une erreur réseau, `Transfer failed` seul ou une erreur TSIG ne constituent pas un refus prouvé. Certaines versions anciennes de dig n'affichent pas le RCODE AXFR ; une preuve explicite complémentaire est alors nécessaire. Ne pas conclure au succès de la recette sur un code 2.

## Passage depuis la livraison avec vues

Sauvegarder les deux pairs et relever leurs SOA avant le déploiement en recette. La configuration autorise la fusion calculée `bind_allowed_clients` des listes cloud et internes, charge les zones natives globalement et désactive DNSSEC par défaut (`bind_dnssec_active: false`). Déclarer `dnssec_validate: false` seulement sur les suffixes privés non signés qui nécessitent une exception. Les modifications des données exigent un serial supérieur au serial servi. Déployer le primaire avant le secondaire et appliquer [TESTS_FONCTIONNELS.md](TESTS_FONCTIONNELS.md). Le passage à chaud depuis des vues n'a pas été exécuté sur RHEL : contrôler le rechargement et les journaux ; planifier un restart en recette si nécessaire. Le rollback automatique porte sur l'hôte courant, sans restaurer les hôtes déjà déployés.

## DNSSEC, services et retour arrière

Le runtime commun est `/etc/systemd/system/named.service.d/bind9-runtime.conf`.
Avec `true`/`auto` seulement, le rôle ajoute `bind9-z-dnssec.conf`, le helper
et les unités privées. L’ancien `bind9-dnssec.conf` est retiré. Les opérations
DNSSEC sont incluses dans `deploy`, indépendamment du moniteur facultatif.
Surveiller le timer et ses erreurs lorsqu’il est activé ; voir [DNSSEC.md](DNSSEC.md).

L’archive BIND seule ne contient pas les unités, le helper ni le journal privé.
Le rescue du déploiement restaure désormais les fichiers runtime capturés en
mémoire et le précédent état du timer. Une restauration manuelle ou après
perte du contrôleur exige les sauvegardes persistantes correspondantes : ne
pas redémarrer un ancien named.conf avec des hooks manquants. L’effet sur
systemd/SELinux RHEL réel reste **NON EXÉCUTÉ**.

## Basculer la validation DNSSEC

Sauvegarder avant bascule les fichiers BIND et les six assets gérés :
`bind9-runtime.conf`, ancien `bind9-dnssec.conf`, `bind9-z-dnssec.conf` sous
`/etc/systemd/system/named.service.d`, le helper `/usr/local/sbin/bind-private-dnssec`
et les deux unités `/etc/systemd/system/named-private-dnssec.service` / `.timer`.
Conserver les fichiers existants, permissions et états du timer ; ne pas
inventer de fichiers absents. La transaction capture ces éléments en mémoire
Ansible avant mutation. Elle refuse les chemins non réguliers ou non root.

Avec `false`, arrêter/désactiver le timer et arrêter le service privé avant
de retirer hooks, unités et helper. Recharger les définitions systemd avant
de retirer l’exécutable ; conserver les ancres officielles et le journal
d’appartenance. Le drop-in commun conserve le lancement natif et le reload RNDC ; seule l’écoute DNS IPv6 est désactivée.

Si un échec déclenche le rescue, les unités privées sont arrêtées et leur timer
désactivé, puis named est arrêté. Les assets initiaux sont restaurés avec
leurs droits, les nouveaux fichiers absents à l’origine sont retirés, les
définitions sont rechargées, la sauvegarde BIND est restaurée et named redémarre.
Le timer reprend uniquement s’il était actif avant déploiement, avec son
enablement initial. Un processus Ansible tué ne déclenche pas ce rescue :
utiliser les sauvegardes persistantes et la version/variables précédentes
pour une reprise opérateur ; inspecter les fichiers et `systemctl cat named`
avant redémarrage. Ne pas mélanger un ancien named.conf avec des hooks manquants.

Qualifier la séquence sur une paire RHEL 8 de recette, dont SELinux et systemd.
Les tests locaux substituent systemd et ne prouvent pas les redémarrages réels.
Le helper source reste livré dans le ZIP pour les modes activés, mais n’est
pas installé sur une cible neuve en mode `false`.


## Migration de la variable et contrôle du contrôleur

Utiliser uniquement `bind_dnssec_active: false`, `true` ou `auto` comme entrée
DNSSEC. Remplacer l’ancien nom `bind_dnssec_enabled` dans les inventaires et
paramètres CLI. Ne pas définir un mode intermédiaire ; le rôle n’en calcule
plus et son assertion de surcharge est supprimée. Un `false` YAML sans
quotes est valide. Ne pas retirer la validation stricte au profit d’un simple
cast `bool` : une valeur invalide doit échouer avant toute modification.
Les contrôles de types utilisent `type_debug`, sans test Jinja 2.11 requis.
Voir [DURCISSEMENT_DNSSEC.md](DURCISSEMENT_DNSSEC.md) pour les blocs impactés.
