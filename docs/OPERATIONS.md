# Opérations BIND 9 (RHEL 8+)

**Recette courante :** [tag routes seul](FRONT_ROUTES.md#recette-du-tag-routes-seul). La section de déploiement combiné ci-dessous conserve les preuves de la livraison précédente.

**Exclusion actuelle :** routes et management utilisent toujours un verrou de processus commun `/var/lib/Bind9-Ansible/operations.flock`, libéré automatiquement à la fin du processus. Le fichier vide peut rester présent sans bloquer une nouvelle opération. `bind_operation_lock_enabled: false` ignore uniquement le répertoire historique `.operation.lock` hors hibernation ; il ne désactive pas cette exclusion de processus. Les journaux incomplets sont des données de reprise, jamais des verrous à effacer. Organiser les déploiements DNS complets et interventions manuelles dans une fenêtre exclusive. [Fonctionnement, preuves et limites](REFONTE_ROUTES_MANAGEMENT.md).

**Contrat actuel :** `hibernate_deployment: yes` génère un sceau après réussite du déploiement complet ; défaut `no`, sans nouveau sceau. Le tag isolé `hibernate`, avec ce paramètre à `yes`, permet de sceller uniquement la configuration existante sans redéploiement. Un sceau existant bloque toute reconstruction, même avec `no`, jusqu’à son [retrait explicite par un opérateur](HIBERNATE_DEPLOYMENT.md). `force` est retiré et refusé. Pour une modification ciblée préservant les autres interventions, utiliser [MANAGEMENT.md](MANAGEMENT.md), lecture et checksum obligatoires. Les procédures de reconstruction ci-dessous nécessitent le retrait préalable de tout sceau existant.

Le rôle déploie **un seul fichier de configuration** : `/etc/named.conf`.

Les préconditions renforcées des secrets, archives, chemins et reprises FRONT
figurent dans [DURCISSEMENT_SECURITE.md](DURCISSEMENT_SECURITE.md).
La restauration refuse désormais un `named` actif, les destinations contenant
des liens/fichiers spéciaux et les erreurs SELinux. Si la récupération de sécurité
échoue, son archive privée est conservée avec un message explicite. Une racine de
backup existante doit déjà être root `0700` : aucune correction aveugle des droits
d’un répertoire fourni par erreur n’est faite par le rôle.
Les fichiers de données des zones primaires restent dans `/var/named/zones/` et les zones secondaires dans `/var/named/slaves/`.
Aucun include de configuration `/etc/named/*.conf` n'est créé.
La configuration globale sans vues inclut `/etc/named.rfc1912.zones` du paquet RHEL,
sans modifier ce fichier ni ses fichiers de zones. Voir [LOOPBACK.md](LOOPBACK.md).

## Sauvegarde et restauration

La journalisation fichier et les labels SELinux persistants de
`/var/log/named` sont décrits dans [JOURNALISATION.md](JOURNALISATION.md).
Les journaux et la base SELinux ne sont pas inclus dans l’archive BIND ;
le rollback DNS conserve ces traces et la règle de contexte.
Les autorisations DNS firewalld sont également conservées :
[FIREWALLD.md](FIREWALLD.md) décrit les zones, les périmètres et la reprise.

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

Les anciens fragments de configuration présents sous `/etc/named` et `/etc/named/zones` sont conservés. La tâche de suppression des huit fragments historiques a été retirée. Le déploiement continue à produire `/etc/named.conf` depuis `named.conf.j2`, sans inclure automatiquement ces anciens fragments. Leur présence sur disque ne déclenche aucun nettoyage.

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

Sauvegarder les deux pairs et relever leurs SOA avant le déploiement en recette. La configuration autorise la fusion calculée `bind_allowed_clients` des listes cloud et internes, charge les zones natives globalement et active DNSSEC en mode automatique par défaut (`bind_dnssec_active: auto`). Déclarer `dnssec_validate: false` seulement sur les suffixes privés non signés qui nécessitent une exception. Les modifications des données exigent un serial supérieur au serial servi. Déployer le primaire avant le secondaire et appliquer [TESTS_FONCTIONNELS.md](TESTS_FONCTIONNELS.md). Le passage à chaud depuis des vues n'a pas été exécuté sur RHEL : contrôler le rechargement et les journaux ; planifier un restart en recette si nécessaire. Le rollback automatique porte sur l'hôte courant, sans restaurer les hôtes déjà déployés.

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

Utiliser uniquement `bind_dnssec_active: auto` (défaut), `true` ou `false` comme entrée
DNSSEC. Remplacer l’ancien nom `bind_dnssec_enabled` dans les inventaires et
paramètres CLI. Ne pas définir un mode intermédiaire ; le rôle n’en calcule
plus et son assertion de surcharge est supprimée. Un `false` YAML sans
quotes est valide. Ne pas retirer la validation stricte au profit d’un simple
cast `bool` : une valeur invalide doit échouer avant toute modification.
Les contrôles de types utilisent `type_debug`, sans test Jinja 2.11 requis.
Voir [Compatibilité DNSSEC](DNSSEC.md#compatibilité-et-migration) pour les blocs impactés.


## Catalogues d’archives incomplets

Les lectures initiales, de rétention et des membres avant restauration doivent réussir entièrement. Les messages `Archive catalog read failed`, `Retention catalog read failed; no archive purged` et `Restore member catalog read failed; live data preserved` signalent un refus. Contrôler la cause de la commande native et son code retour avant une relance. Après une erreur de rétention, la nouvelle archive et son SHA-256 peuvent déjà exister ; aucune purge ni attestation `BACKUP_CREATED` n’a lieu. Un échec de la liste des membres avant restauration préserve la configuration et les zones live. Les préconditions d’arrêt du service, de permissions et de sauvegarde restent obligatoires. Voir [les preuves avant/après](VERIFICATION_SYNTAXE_SCRIPTS.md).

Les lectures de métadonnées doivent également réussir : `Backup ancestor metadata read failed` et `Backup root metadata read failed` arrêtent la commande avant création de l’état de sauvegarde. Une erreur `stat` sur l’archive, son empreinte ou sa taille bloque aussi la vérification. Rechercher la cause de lecture ou de permissions ; ne pas modifier aveuglément les droits pour contourner le refus.


## Préservation des fragments historiques

Rapport de la révision source `2026.10.19-fragments.1`. Le retrait reste conservé ; les contrôles courants de consolidation sont dans [DNSSEC.md](DNSSEC.md).

Révision `2026.10.19-fragments.1`, issue de `Bind9-Ansible-syntaxe-verifiee.zip` (SHA-256 `2f5be38370d540cf74a4d89a868e75c61f5e2ff65723776dccce96affddebb8e`). La seule modification de production retire la tâche `Remove obsolete Bind9-Ansible configuration fragments` dans `roles/bind9/tasks/tasks.d/deploy/configuration.yml`. Les huit chemins de sa boucle ne sont plus supprimés par cette étape.

Le rendu de `/etc/named.conf`, ses droits, `no_log`, la notification `Reload named configuration` et le nettoyage distinct de `/usr/local/sbin/named-transaction` sont conservés. Aucun changement de variable, tag, template, script ou dépendance. Les suppressions de zones explicitement prévues par d’autres opérations restent régies par leurs propres contrats.

**EXÉCUTÉ** : le test de préservation échoue sur la livraison source, puis réussit après retrait. Les 41 tests ciblés (architecture, contrat de déploiement et nom de l’utilitaire installé) réussissent avec Python 3.6.8, Ansible 2.9.27 et Jinja2 2.10.3. Les deux playbooks passent le contrôle de syntaxe avec les trois inventaires ; 74 fichiers Python sont compilés et sept scripts Bash passent `bash -n`. Les preuves et empreintes de production figurent dans `DELIVERY.json`. La CI existante exécute déjà le fichier de test modifié ; son workflow est conservé.

Les campagnes plus larges de la livraison source restent historiques : elles ne sont pas présentées comme réexécutées pour cette suppression locale. Le packaging conserve tous les fichiers précédents, recalcule le manifeste et fournit une empreinte externe. La source est conservée intacte.

**NON EXÉCUTÉ** : exécution sur vos serveurs RHEL 8, faute d’accès. Prérequis : hôte de recette représentatif, accès ADM de secours et sauvegarde vérifiée. Lors d’un déploiement de recette prévu, relever les empreintes des huit fragments présents avant et après, puis exécuter les tests DNS du README. Attendu : fragments inchangés, configuration principale déployée et DNS opérationnel. Le risque restant concerne l’état réel du serveur et les autres étapes de déploiement ; qualification de production **NON TERMINÉE**.

Retour de version : remettre la copie précédente du projet sur le contrôleur avant une nouvelle exécution. Cela réintroduit la suppression automatique ; sauvegarder les fragments concernés avant un tel déploiement. Cette livraison ne restaure pas des fichiers déjà supprimés sur un serveur par une exécution antérieure.


La préparation DNSSEC est désormais regroupée dans `dnssec_prepare.yml`, sans modifier les composants capturés et restaurés. Voir [périmètre et recette](DNSSEC.md#consolidation-des-fichiers--20261019-dnssec1).


## Déploiement combiné DNSSEC auto et routes FRONT

Révision `2026.10.19-deploy-auto-routes.1`, issue de
`Bind9-Ansible-dnssec-consolide.zip`, SHA-256
`eb3d0345a3ee3575569f82809ee640cd1f24687140cd061a2e468e8ae9aa7ba8`.
L’objectif est de tester simultanément `bind_dnssec_active: auto` et le booléen
`bind_front_routes_enabled: true` via le playbook public `bind9-deploy.yml`.
Tous les fichiers de production (`roles/`), inventaires, playbooks et le
workflow CI sont identiques à la source ; aucun défaut de production n’a été
reproduit sur le périmètre exercé. Les plafonds de cache restent présents.

### Périmètre et preuves

**EXÉCUTÉ** : deux conteneurs EL8 séparés, sans réseau externe et avec des
ressources bornées, exécutent les rôles primaire et secondaire. Le contrôleur
emploie Python 3.6.8, Ansible 2.9.27 et Jinja 2.10.3 ; les outils natifs sont
BIND 9.11.36 et NetworkManager 1.40.16. L’image locale est identifiée par
`sha256:215db17f4fd2f7632d2d7025ccd8cf0d14e28a2040807ec83c931f98a0441f01`.
L’OS de laboratoire est Rocky EL8 ; les faits `RedHat`/version majeure `8`
sont fournis au playbook pour exercer le garde de plateforme. Cela ne
constitue pas un déploiement RHEL réel.

Le playbook conserve son point d’entrée, ses imports, conditions et tags.
Les tâches DNSSEC, fichiers, sauvegarde/restauration, validation, santé DNS,
routes, vérification et commit/rollback sont réellement exécutées. Les
seules adaptations, dans une copie jetable du rôle, sont : transport des
appels systemd par le module de laboratoire, vérification des paquets déjà
installés à la place de DNF et insertion d’un échec de santé contrôlé.
Les commandes firewalld et restorecon sont des doubles de contrat ; SELinux
est désactivé dans le conteneur. Les clés éphémères et archives de laboratoire
ne sont pas livrées.

| Scénario | Résultat observé |
|---|---|
| Déploiement initial primaire et secondaire | Playbook réussi avec DNSSEC `auto`, deux zones privées forwardées et exceptions NTA natives. |
| Trajet réseau FRONT | Adresse de service secondaire sur `front0`, passerelle propre au profil FRONT, aucune route par défaut FRONT et défaut système sur BACK. |
| Clients cloud | Un sous-réseau et une IP seule créent les routes nécessaires ; un client en espace réseau distinct interroge réellement le serveur en UDP et TCP par FRONT. |
| Second passage des deux rôles | Configuration, fichiers runtime, profil FRONT et routes inchangés ; la sauvegarde et le renouvellement NTA restent des opérations attendues. Il ne s’agit pas d’une promesse `changed=0` sur le playbook complet. |
| Préservation | Routes préexistantes, profils BACK/ADM, défaut BACK, modification DNS de profil laissée en attente, fichiers natifs et NTA manuelle conservés. |
| Échec contrôlé sur le primaire | Une destination supplémentaire est ajoutée avant l’échec de santé ; le rescue retire uniquement cet ajout, restaure les fichiers antérieurs et redémarre BIND. Les requêtes cloud réussissent après reprise, sans journal FRONT restant. Le code Ansible `2` est attendu pour cette exécution refusée après rollback. |

Cinq exécutions complètes du playbook sont observées : quatre réussites et
un échec contrôlé suivi d’une restauration vérifiée. Les contrôles réseau
et les requêtes DNS utilisent les vrais binaires ; les tests ne remplacent
ni `ip`, ni `nmcli`, ni BIND, ni RNDC. Le module de laboratoire démarre BIND,
effectue les rechargements RNDC et exécute les hooks DNSSEC réels, mais ne
simule pas un boot ni la totalité du comportement de systemd.

Une première préparation a été refusée parce que l’inventaire de laboratoire
omettait `bind_environment`. L’entrée obligatoire a été ajoutée au banc de
test, sans modifier ni affaiblir l’assertion du rôle. Ce refus ne correspond
pas à un défaut de production. Les campagnes décrites ci-dessus ont ensuite
été réellement exécutées avec l’inventaire complet.

Le lanceur lui-même a aussi été durci : un refus des préconditions ne doit
déclencher aucune tentative d’arrêt de service. Une non-régression reproduit
l’ancien appel de nettoyage avec un enregistreur en mémoire, puis cinq tests
réussissent après correction. Le laboratoire refuse un démon ou des ports
DNS/RNDC déjà présents et n’arrête que le PID enregistré lors de son propre
démarrage. Les deux campagnes natives ont été rejouées avec ces gardes.
Cette correction concerne uniquement les tests, sans modification du rôle.

### Reproduction

N’exécuter ce scénario que dans un **conteneur Linux jetable**, déconnecté,
root, sans réseau/PID/bus ni répertoires système partagés avec l’hôte. Il
exige les capacités de création d’interfaces et d’espaces réseau, les paquets
BIND/NetworkManager/D-Bus/iproute EL8, ainsi que les dépendances de contrôle
Ansible/Jinja/PyYAML/pytest du README. Les sources peuvent être montées en
lecture seule ; les écritures ciblent uniquement le système du conteneur.
Ne jamais lancer ce programme sur un DNS opérationnel.

Avec ces prérequis et le contrôleur présent dans le PATH, dans un conteneur
dont la seule interface initiale est `lo`, depuis la racine du projet :

```bash
export BIND_DEPLOY_ISOLATED=1
python3 tests/integration_deploy_auto_routes.py --role primary
```

Le lanceur prépare ses wrappers Ansible depuis `/runtime/bin`, disposition
du laboratoire hors ligne. Préparer ce répertoire avec Ansible 2.9.27 et
ses dépendances ou adapter uniquement ce chemin de banc. Dans un second
conteneur neuf, répéter avec `--role secondary`. Les privilèges servent à
isoler le client et à configurer les interfaces privées du conteneur.
Les journaux temporaires restent sous `/tmp/combined-deploy`. Les empreintes
des sources, résultats et empreintes des preuves sont dans `DELIVERY.json`.
Les nouveaux fichiers de test sont déjà inclus dans la compilation Python
et l’analyse des imports de la CI existante ; ce scénario privilégié reste
une recette native explicite, comme les autres laboratoires du projet.

### Limites et recette restante

**NON EXÉCUTÉ** : déploiement sur vos serveurs RHEL 8, systemd/firewalld/SELinux
réels et reboot, faute d’accès et de système RHEL complet dans ce laboratoire.
Prérequis : paire de recette représentative, sauvegardes BIND et runtime
exportées et restaurables, fenêtre exclusive et accès ADM de secours.
Exécuter le playbook avec les deux paramètres ci-dessus, puis les tests
[fonctionnels](TESTS_FONCTIONNELS.md), les contrôles de routes et un reboot
planifié. Attendu : DNS UDP/TCP opérationnel, routes présentes à chaud puis
après reboot, timer actif, labels attendus, DNS autorisé dans les seules
zones firewalld concernées et défaut BACK préservé. Risque restant : profils,
versions, confinement et ordonnancement réels non reproduits.

**NON EXÉCUTÉ** : réplication entre les deux conteneurs, zones autoritatives
métier, validation d’une chaîne signée de production, observation de plus
de sept jours et tests de charge. Les deux rôles sont testés séparément avec
deux zones privées non signées, protégées par leurs exceptions limitées.
Le laboratoire ne fournit ni paire interconnectée, ni upstreams signés de
production, ni la volumétrie et la durée d’observation nécessaires.
Pour compléter : utiliser une paire interconnectée et des upstreams de
recette représentatifs ; vérifier transferts TSIG, signatures valides et
invalides hors exceptions, renouvellement avant expiration et capacité.
Ces fonctions conservent leurs preuves historiques ; elles ne sont pas
requalifiées par le seul scénario combiné. Qualification de production
**NON TERMINÉE**.

**NON EXÉCUTÉ** : CI distante et autres contrôleurs, faute d’exécution dans
un dépôt distant. Lancer la matrice existante après import ; attendu : tous
les contrôles applicables réussissent sans abaisser les seuils. Le retour
de livraison consiste à restaurer la source intégrale sur le contrôleur ;
aucun changement de production de cette révision ne nécessite une migration
sur les serveurs. Les procédures de restauration existantes restent valables.


### Contrôles complémentaires de cette révision

**EXÉCUTÉ** : 232 tests ciblés sans exclusion, six contrôles de syntaxe Ansible, compilation de 76 fichiers Python et syntaxe de sept scripts Bash. Les tests existants ne sont pas affaiblis ; les cinq cas de garde du laboratoire complètent la suite globale. Les campagnes des versions précédentes restent historiques, sans addition des comptes entre révisions.

| Fichier concerné | Rôle |
|---|---|
| `tests/integration_deploy_auto_routes.py` | Exécution du playbook public, réseau privé natif, client cloud et reprise. |
| `tests/fixtures/deployment_systemd.py` | Adaptateur de services limité au laboratoire ; BIND/RNDC et hooks réels, arrêt réservé au PID possédé. |
| `tests/test_global_review.py` | Non-régression de refus sans effet de service et d’identité du démon lors du nettoyage. |
| README, OPERATIONS, DELIVERY et manifeste | Périmètre, preuves, reproductibilité et limites. |
