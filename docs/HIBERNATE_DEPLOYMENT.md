# Verrouillage optionnel du déploiement

**Mode temporaire actuel :** `bind_operation_lock_enabled: false` désactive la prise du verrou commun `.operation.lock` pour les opérations ordinaires, lectures et simulations de management comprises. Un ancien verrou est ignoré et conservé. **Une seule opération à la fois**, y compris toute intervention manuelle. Avec le booléen YAML `true`, les garanties de verrou coopératif décrites ci-dessous s’appliquent de nouveau. Le tag `hibernate` et le déploiement complet avec `hibernate_deployment: yes` conservent toujours ce verrou. Les journaux de reprise, contrôles de checksum et verrous propres aux routes/sauvegardes restent actifs. [Contrat et preuves de cette révision](VERROUS_OPERATIONS_OPTIONNELS.md).

Le paramètre public de scellement est `hibernate_deployment`, de type booléen, avec
`no` par défaut dans `roles/bind9/defaults/main.yml` (booléen YAML `false`). Les chaînes CLI
minuscules `true`, `false`, `yes`, `no` sont acceptées par Ansible 2.9.
Les entiers, listes, mappings, valeurs nulles et autres chaînes sont refusés
avant les observations ou écritures sur le serveur. Les anciens noms `force`,
`deployment_lock` et `deployment_locked` sont refusés : retirer leurs déclarations des inventaires,
playbooks et paramètres `-e`.

## Contrat et transitions

Le sceau persistant est `/var/lib/Bind9-Ansible/.deployment.lock`, fichier
masqué root `0600`, dans un dossier root `0700`. Il contient un manifeste
SHA-256 des fichiers déployés et son empreinte ; aucun secret ni contenu de
configuration. C’est une barrière contre les erreurs d’exploitation, pas une
signature authentifiée ni une protection contre root. Le garde bloque sur
sa présence, sans exiger que la configuration actuelle corresponde au manifeste :
une modification manuelle ne désactive donc jamais la protection.

| Sceau initial | Paramètre | Déploiement complet réussi | Résultat |
|---|---|---|---|
| Absent | `false` ou valeur omise | Autorisé | Aucun sceau créé ; un prochain déploiement reste possible. |
| Absent | `true` | Autorisé | Sceau créé seulement après tous les contrôles de santé. |
| Présent | `false` | Refusé avant reconstruction | Sceau conservé ; `false` n’est pas un déverrouillage. |
| Présent | `true` | Refusé avant reconstruction | Sceau conservé. |
| Absent | `true`, exécution partielle ordinaire, échec ou `--check` | Aucun scellement | Le parcours ne peut pas annoncer un déploiement complet scellé. |
| Absent | Tag isolé `hibernate`, paramètre `yes` | Aucun déploiement | Sceau du fichier canonique live après contrôle de sécurité et de syntaxe uniquement. |
| Retiré explicitement par l’opérateur | `true` | Autorisé | Nouveau sceau après réussite ; sauvegardes et contrôles conservés. |

Le contrôle est répété après acquisition du verrou d’opération pour éviter
qu’un sceau créé entre la première observation et la prise de contrôle soit ignoré.
Les sceaux des livraisons précédentes restent bloquants. Aucun retrait,
migration ou remplacement automatique d’un sceau existant n’est effectué.

Dans les variables du site :

```yaml
hibernate_deployment: yes
```

Ou depuis la racine du projet sur le contrôleur Linux :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --limit site1 --tags deploy -e hibernate_deployment=yes
```

Cette commande reconstruit les fichiers à partir de l’inventaire si aucun sceau
n’existe. Elle peut écraser des changements manuels ou du management : sauvegarder
et les réconcilier avant toute reconstruction. Pour conserver l’état live et
modifier seulement des ressources choisies, utiliser
[bind9-management.yml](MANAGEMENT.md).

## Tag d’hibernation seul

Pour protéger un serveur existant sans lancer de reconstruction :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --limit site1 --tags hibernate -e hibernate_deployment=yes
```

Le tag `hibernate` est marqué `never` : ni `all` ni `deploy` ne l’exécutent.
Il exige `hibernate_deployment=yes` et refuse de se combiner à une autre action.
Il utilise le même garde et le verrou commun. Après contrôle des chemins, du
type, du propriétaire, des droits, du lien unique et d’une taille de 1 octet
à 16 Mio de `/etc/named.conf`, il lance `named-checkconf` sur ce fichier réel.
GNU coreutils `timeout`, déjà présent sur EL8, envoie SIGTERM après 60 secondes,
puis SIGKILL 5 secondes après expiration si nécessaire. Une I/O noyau non
interruptible reste une limite du système, pas une garantie de cet outil. Les diagnostics
susceptibles de contenir une configuration sont masqués ; un échec contrôlé
laisse la configuration intacte et ne crée aucun sceau.

Le sceau porte `payload.kind: hibernate` et le SHA-256 du **seul fichier
canonique** ; celui du parcours complet porte `kind: deploy` et le manifeste
des fichiers déployés. Le manifeste n’est pas une révision complète du graphe
live : les empreintes de management sont distinctes. Aucun inventaire de zones
ou de clients n’est utilisé pour reconstruire des données pendant l’hibernation.
Aucun secret TSIG n’est créé, aucun paquet, fichier DNS, service, pare-feu ou
route n’est déployé ; l’état privé du verrou est la seule écriture persistante.
Les autres validations `always` du parcours de déploiement ne sont pas lancées.

Si un sceau existe déjà et que ses chemins sont sûrs, il est conservé sans
modification, sans nouvelle validation de configuration et sans rafraîchissement
du manifeste. Le tag est idempotent sur cet état. Il ne bloque pas le service
DNS, ne l’arrête pas et **ne qualifie pas sa santé** : cela reste une opération
de verrouillage des futurs déploiements.

Simulation :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml \
  --limit site1 --tags hibernate -e hibernate_deployment=yes --check
```

La syntaxe peut être contrôlée et la création annoncée, sans sceau ni verrou
d’opération. Cette simulation n’offre pas la stabilité d’une observation sous
verrou ; une opération concurrente peut changer le résultat après le contrôle.

## Verrou d’opération indépendant

`/var/lib/Bind9-Ansible/.operation.lock` protège les opérations coopérantes
concurrentes lorsqu’elles l’utilisent. Il reste obligatoire pour l’hibernation,
mais il est ignoré par défaut pour les tags `routes`, `routes-recover`, `backup`,
le management et les déploiements sans scellement. Définir le booléen
`bind_operation_lock_enabled: true` pour rétablir sa prise sur ces parcours.
Un sceau existant autorise les opérations auxiliaires mais bloque la reconstruction DNS.

Le rôle libère uniquement le répertoire vide dont il a observé l’identité
(périphérique et inode), jamais par suppression récursive. Un remplacement,
un contenu inattendu ou une reprise DNS/FRONT incomplète conserve le verrou
et produit un échec explicite. Ne jamais retirer un verrou occupé pour poursuivre
un autre déploiement. Après interruption du contrôleur, examiner DNS, journaux,
backups et éventuel journal FRONT avant une reprise ; voir
[FRONT_ROUTES.md](FRONT_ROUTES.md) et
[INTEGRITE_CONFIGURATION_LIVE.md](INTEGRITE_CONFIGURATION_LIVE.md).

Les contrôles `--check` du déploiement n’acquièrent pas ce verrou et ne sont
pas une transaction ni une observation stable de l’état. Leur parcours complet
n’est pas qualifié de bout en bout. Le management, y compris ses lectures et
simulations, utilise ce verrou uniquement avec `bind_operation_lock_enabled: true`.

## Retrait explicite du sceau par un opérateur

**Impact :** autorise ensuite une reconstruction susceptible d’écraser les
changements manuels. Le retrait ne déploie rien et ne modifie aucune configuration.
Prérequis : fenêtre autorisée, accès root sur le serveur concerné, sauvegarde
BIND vérifiée, modifications manuelles réconciliées dans les sources, absence
d’opération en cours ou de reprise non résolue. Effectuer cette procédure
séparément sur chaque serveur à reconstruire ; les transactions ne sont pas
atomiques entre les deux hôtes.

Sur le serveur DNS concerné, le bloc suivant prend le verrou coopératif, refuse
les objets dangereux, conserve une copie privée exacte du sceau et retire
uniquement le fichier contrôlé. Si la prise de verrou échoue, il s’arrête.

```bash
sudo bash <<'OPERATOR'
set -euo pipefail
umask 077
state=/var/lib/Bind9-Ansible
seal=$state/.deployment.lock
lease=$state/.operation.lock
test -d "$state" && test ! -L "$state"
test "$(stat -c '%u:%a' -- "$state")" = '0:700'
mkdir -m 0700 -- "$lease"
trap 'rmdir -- "$lease"' EXIT
test ! -e "$state/front-routes.lock" && test ! -L "$state/front-routes.lock"
test -f "$seal" && test ! -L "$seal"
test "$(stat -c '%u:%a:%h' -- "$seal")" = '0:600:1'
saved=$state/.deployment.lock.operator-$(date -u +%Y%m%dT%H%M%SZ)-$$
cp --no-clobber --preserve=mode,ownership,timestamps -- "$seal" "$saved"
original_digest=$(sha256sum -- "$seal")
saved_digest=$(sha256sum -- "$saved")
test "${original_digest%% *}" = "${saved_digest%% *}"
sync -- "$saved" "$state"
rm -- "$seal"
sync -- "$state"
printf 'Sceau retiré ; copie privée conservée : %s\n' "$saved"
OPERATOR
```

La copie permet de rétablir la barrière si l’opération est annulée **avant toute
reconstruction**, après contrôle opérateur et sous le même verrou. Après
reconstruction, ne pas réinstaller un ancien manifeste comme preuve du nouvel
état : laisser le parcours complet réussi créer son sceau, ou utiliser le tag
`hibernate` avec `hibernate_deployment: yes` pour un nouveau sceau limité au
fichier canonique. Une modification seule du paramètre dans l’inventaire ne crée
pas de sceau à distance ; le parcours choisi doit réellement réussir.

Ce protocole suppose la coopération des administrateurs root. Un processus root
qui ignore ou remplace les verrous peut toujours intervenir entre deux contrôles.
Les fichiers sont remplacés atomiquement individuellement ; cela ne constitue
pas une transaction atomique de tous les fichiers ou de la paire.

Les contrôles et limites actuels sont détaillés dans
[VERROUS_OPERATIONS_OPTIONNELS.md](VERROUS_OPERATIONS_OPTIONNELS.md).
[AUDIT_HIBERNATE_DEPLOYMENT.md](AUDIT_HIBERNATE_DEPLOYMENT.md) décrit l’archive initiale.
