# Campagnes de configuration et politiques antérieures

[Documentation](../../index.md) › [Historique des évolutions](index.md) › Campagnes de configuration et politiques antérieures

[Retour au Parent](index.md)

## Sommaire

- [Préservation des fragments — 2026.10.19-fragments.1](#préservation-des-fragments--20261019-fragments1)
- [Banc combiné DNSSEC et FRONT — 2026.10.19-deploy-auto-routes.1](#banc-combiné-dnssec-et-front--20261019-deploy-auto-routes1)
- [Essais DNSSEC antérieurs](#essais-dnssec-antérieurs)
- [Consolidation DNSSEC — 2026.10.19-dnssec.1](#consolidation-dnssec--20261019-dnssec1)
- [Journalisation, rotation et SELinux : campagnes antérieures](#journalisation-rotation-et-selinux--campagnes-antérieures)
- [Routes : syntaxe et recette du tag seul](#routes--syntaxe-et-recette-du-tag-seul)
- [Transport DNS IPv4/IPv6 : essais antérieurs](#transport-dns-ipv4ipv6--essais-antérieurs)
- [Firewalld : observations initiales et limites](#firewalld--observations-initiales-et-limites)
- [Loopback et zones vides : essais datés](#loopback-et-zones-vides--essais-datés)
- [Réseaux clients : campagne de migration](#réseaux-clients--campagne-de-migration)
- [Politique de verrou historique optionnel](#politique-de-verrou-historique-optionnel)

<a id="fragments"></a>
## Préservation des fragments — 2026.10.19-fragments.1

Source documentaire du chapitre « Préservation des fragments — 2026.10.19-fragments.1 » : `docs/wiki.d/exploitation/OPERATIONS.md`, SHA-256 `e5de3260a5b48f99ee4a18f64274dc74ca281987bb70296b4911d9473327caff` (copie antérieure conservée dans l’archive source de référence).

### Préservation des fragments historiques

Rapport de la révision source `2026.10.19-fragments.1`. Le retrait reste conservé ; les contrôles courants de consolidation sont dans [DNSSEC.md](../configuration/bind/DNSSEC.md).

Révision `2026.10.19-fragments.1`, issue de `Bind9-Ansible-syntaxe-verifiee.zip` (SHA-256 `2f5be38370d540cf74a4d89a868e75c61f5e2ff65723776dccce96affddebb8e`). La seule modification de production retire la tâche `Remove obsolete Bind9-Ansible configuration fragments` dans `roles/bind9/tasks/tasks.d/deploy/configuration.yml`. Les huit chemins de sa boucle ne sont plus supprimés par cette étape.

Le rendu de `/etc/named.conf`, ses droits, `no_log`, la notification `Reload named configuration` et le nettoyage distinct de `/usr/local/sbin/named-transaction` sont conservés. Aucun changement de variable, tag, template, script ou dépendance. Les suppressions de zones explicitement prévues par d’autres opérations restent régies par leurs propres contrats.

**EXÉCUTÉ** : le test de préservation échoue sur la livraison source, puis réussit après retrait. Les 41 tests ciblés (architecture, contrat de déploiement et nom de l’utilitaire installé) réussissent avec Python 3.6.8, Ansible 2.9.27 et Jinja2 2.10.3. Les deux playbooks passent le contrôle de syntaxe avec les trois inventaires ; 74 fichiers Python sont compilés et sept scripts Bash passent `bash -n`. Les preuves et empreintes de production figurent dans `DELIVERY.json`. La CI existante exécute déjà le fichier de test modifié ; son workflow est conservé.

Les campagnes plus larges de la livraison source restent historiques : elles ne sont pas présentées comme réexécutées pour cette suppression locale. Le packaging conserve tous les fichiers précédents, recalcule le manifeste et fournit une empreinte externe. La source est conservée intacte.

**NON EXÉCUTÉ** : exécution sur vos serveurs RHEL 8, faute d’accès. Prérequis : hôte de recette représentatif, accès ADM de secours et sauvegarde vérifiée. Lors d’un déploiement de recette prévu, relever les empreintes des huit fragments présents avant et après, puis exécuter les tests DNS du README. Attendu : fragments inchangés, configuration principale déployée et DNS opérationnel. Le risque restant concerne l’état réel du serveur et les autres étapes de déploiement ; qualification de production **NON TERMINÉE**.

Retour de version : remettre la copie précédente du projet sur le contrôleur avant une nouvelle exécution. Cela réintroduit la suppression automatique ; sauvegarder les fragments concernés avant un tel déploiement. Cette livraison ne restaure pas des fichiers déjà supprimés sur un serveur par une exécution antérieure.


La préparation DNSSEC est désormais regroupée dans `dnssec_prepare.yml`, sans modifier les composants capturés et restaurés. Voir [périmètre et recette](../configuration/bind/DNSSEC.md#consolidation-des-fichiers--20261019-dnssec1).



<a id="banc-combine"></a>
## Banc combiné DNSSEC et FRONT — 2026.10.19-deploy-auto-routes.1

Source documentaire du chapitre « Banc combiné DNSSEC et FRONT — 2026.10.19-deploy-auto-routes.1 » : `docs/wiki.d/exploitation/OPERATIONS.md`, SHA-256 `e5de3260a5b48f99ee4a18f64274dc74ca281987bb70296b4911d9473327caff` (copie antérieure conservée dans l’archive source de référence).

### Déploiement combiné DNSSEC auto et routes FRONT

Révision `2026.10.19-deploy-auto-routes.1`, issue de
`Bind9-Ansible-dnssec-consolide.zip`, SHA-256
`eb3d0345a3ee3575569f82809ee640cd1f24687140cd061a2e468e8ae9aa7ba8`.
L’objectif est de tester simultanément `bind_dnssec_active: auto` et le booléen
`bind_front_routes_enabled: true` via le playbook public `bind9-deploy.yml`.
Tous les fichiers de production (`roles/`), inventaires, playbooks et le
workflow CI sont identiques à la source ; aucun défaut de production n’a été
reproduit sur le périmètre exercé. Les plafonds de cache restent présents.

#### Périmètre et preuves

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

#### Reproduction

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

#### Limites et recette restante

**NON EXÉCUTÉ** : déploiement sur vos serveurs RHEL 8, systemd/firewalld/SELinux
réels et reboot, faute d’accès et de système RHEL complet dans ce laboratoire.
Prérequis : paire de recette représentative, sauvegardes BIND et runtime
exportées et restaurables, fenêtre exclusive et accès ADM de secours.
Exécuter le playbook avec les deux paramètres ci-dessus, puis les tests
[fonctionnels](../qualification/TESTS_FONCTIONNELS.md), les contrôles de routes et un reboot
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


#### Contrôles complémentaires de cette révision

**EXÉCUTÉ** : 232 tests ciblés sans exclusion, six contrôles de syntaxe Ansible, compilation de 76 fichiers Python et syntaxe de sept scripts Bash. Les tests existants ne sont pas affaiblis ; les cinq cas de garde du laboratoire complètent la suite globale. Les campagnes des versions précédentes restent historiques, sans addition des comptes entre révisions.

| Fichier concerné | Rôle |
|---|---|
| `tests/integration_deploy_auto_routes.py` | Exécution du playbook public, réseau privé natif, client cloud et reprise. |
| `tests/fixtures/deployment_systemd.py` | Adaptateur de services limité au laboratoire ; BIND/RNDC et hooks réels, arrêt réservé au PID possédé. |
| `tests/test_global_review.py` | Non-régression de refus sans effet de service et d’identité du démon lors du nettoyage. |
| README, OPERATIONS, DELIVERY et manifeste | Périmètre, preuves, reproductibilité et limites. |

<a id="dnssec-preuves"></a>
## Essais DNSSEC antérieurs

Source documentaire du chapitre « Essais DNSSEC antérieurs » : `docs/wiki.d/configuration/bind/DNSSEC.md`, SHA-256 `acda07e67f8885c3172decbbfac040e4808fe89ce42027871adc3c94c6c29165` (copie antérieure conservée dans l’archive source de référence).

### Preuves historiques et limites

Les campagnes ci-dessous proviennent des livraisons antérieures ; elles ne sont pas présentées comme réexécutées pour la consolidation. Les contrôles propres à cette révision figurent dans la section suivante et dans `DELIVERY.json`.

**EXÉCUTÉ** : BIND natif EL8 9.11.36 et BIND 9.18.24, chacun 109 configurations
et 270 contrôles DNS lors de cette campagne historique : mode désactivé, validation explicite et auto,
signatures valides/invalides, AD/CD, forwarding privé, NTA, réplication TSIG et
zones natives inchangées. Une ancre signée temporaire de laboratoire rend les
essais cryptographiques indépendants d’Internet ; elle n’est pas livrée en production.
Les exceptions privées ne fournissent pas de garantie DNSSEC sur leurs suffixes.

**EXÉCUTÉ** : tests des valeurs, rendu sans directives/markers privés en mode
désactivé, anciens noms forcés à priorité extra-vars, typage Jinja classique et
natif ; suite avec Jinja 2.10.3 et 3.0.3. Flux Ansible avec opérations de fichiers
réelles et double systemd : installation, deuxième passage, nettoyage, migration
et rollback dans les deux directions. Nombres exacts et périmètre : `DELIVERY.json`.

**NON EXÉCUTÉ** : activation systemd et SELinux RHEL réels, boot, rotation RFC5011,
chaînes des upstreams de production, renouvellement sur sept jours et restauration
système après perte du contrôleur. Prérequis, procédures, attendus et risques :
[VALIDATION_RHEL8.md](../qualification/VALIDATION_RHEL8.md). Qualification de production
**NON TERMINÉE**. La racine privée et NXDOMAIN hors périmètre sont conservés ;
les essais ne démontrent pas une résolution Internet générale.



<a id="dnssec-consolidation"></a>
## Consolidation DNSSEC — 2026.10.19-dnssec.1

Source documentaire du chapitre « Consolidation DNSSEC — 2026.10.19-dnssec.1 » : `docs/wiki.d/configuration/bind/DNSSEC.md`, SHA-256 `acda07e67f8885c3172decbbfac040e4808fe89ce42027871adc3c94c6c29165` (copie antérieure conservée dans l’archive source de référence).

### Consolidation des fichiers — 2026.10.19-dnssec.1

La source est `Bind9-Ansible-fragments-preserves.zip`, SHA-256
`41453d2a415983bb040fac1e57c8c44c6d1ed42f4bc60228c4c307f392b22578`.
La réduction porte sur les sources du contrôleur : 213 fichiers deviennent
209. Aucun fichier runtime supplémentaire n’est supprimé sur les serveurs.

| Partie impactée | Décision et invariant |
|---|---|
| `roles/bind9/tasks/tasks.d/dnssec_prepare.yml` | Regroupe les anciennes préparations de retrait puis d’installation, avec conditions exclusives héritées par chaque tâche. |
| `roles/bind9/tasks/tasks.d/deploy/main.yml` | Un import de préparation remplace les deux imports précédents ; tags `deploy`/`configure` et emplacement conservés. |
| Ancien `named-dnssec-override.j2` | Contenu intégré dans une tâche `copy`, même destination, droits, propriétaire, notification et variable enregistrée. |
| `dnssec_settings.yml` et `dnssec_activate.yml` | Conservés : validation précoce, activation après démarrage/reload et avant les contrôles DNS. |
| Helper, unités et journal | Algorithme, chemins, renouvellement quotidien, verrou et appartenance des exceptions inchangés. |
| Documentation | `DURCISSEMENT_DNSSEC.md` et `DEFAUT_DNSSEC_AUTO.md` sont consolidés ici ; leurs liens sont redirigés. |

Les ACL, routes, zones natives, transport, clés TSIG,
handler, snapshot/restore, management et hibernation restent identiques à la
source. Aucun paramètre public, tag ni dépendance runtime ne change.
La tâche de suppression des anciens fragments reste retirée.

Le template `named.conf.j2` reçoit également les trois plafonds de cache
demandés pendant cette révision : `max-cache-size 256m;`,
`max-cache-ttl 3600;` et `max-ncache-ttl 300;`, dans le bloc `options`.
La taille concerne le cache et ses métadonnées, pas toute la mémoire de
`named`. Les durées plafonnent la conservation des réponses positives et
négatives sans augmenter les TTL plus courts. Elles ne remplacent pas les
TTL des zones autoritatives. Le dimensionnement et la charge des forwarders
restent à mesurer sur votre environnement ; aucun benchmark n’est revendiqué.

Les risques de cette réorganisation sont une condition perdue, une activation
trop précoce, une différence de contenu/droits du drop-in ou une référence
vers un fichier retiré. Les tests conservent les scénarios précédents, étendent
les chaînes CLI et les retours arrière entre modes actifs, et une comparaison
avant/après utilise de vrais fichiers et le vrai moteur Ansible. Le transport
systemd reste un double de contrat : il ne prouve pas le comportement au boot.

**EXÉCUTÉ — révision courante** : 107 tests ciblés sur le template final, six contrôles de syntaxe Ansible, analyse des sources et préflight des trois inventaires (12 hôtes simulés), sous Python 3.6.8/Ansible 2.9.27/Jinja 2.10.3. Avant le seul ajout des trois plafonds de cache au template, la suite de consolidation a exécuté 120 cas, dont 13 workflows runtime. Les comptes se recoupent et ne s’additionnent pas. La comparaison source/révision a exécuté 12 parcours Ansible pour `false`, `true`, `auto` et leur second passage : contenus normalisés uniquement pour les chemins du laboratoire, droits et séquences de services identiques. Les tâches runtime sont restées identiques après cette comparaison ; les workflows utilisent des fichiers réels et un double systemd, avec restauration dans les deux directions et entre modes actifs. Le test des plafonds échoue dans les six rendus contrôlés avec l’ancien template, puis réussit sur le template final. BIND EL8 9.11.36 charge six configurations finales (deux rôles, trois modes) avec `named-checkconf -z` ; sa sortie canonique confirme 268435456 octets, 3600 et 300 secondes. Aucun contrôle de charge ni mesure de TTL en trafic réel n’est revendiqué. La première tentative du laboratoire ignorait 13 tests car son répertoire temporaire interdisait les exécutables ; le lanceur a été corrigé, puis les contrôles applicables ont été réellement exécutés. Aucun changement produit n’a été nécessaire pour cette correction de laboratoire.

#### Contrôles reproductibles

Sur un laboratoire Linux jetable avec Python/Ansible et les dépendances QA du
README, depuis la racine du projet :

```bash
python -m pytest -q tests/test_optional_dnssec.py tests/test_dnssec_settings_ansible.py tests/test_private_dnssec.py tests/test_private_dnssec_hardening.py tests/test_global_review.py tests/test_dnssec_workflows.py tests/test_single_named_architecture.py tests/test_deployment_contract.py tests/test_installed_configtool_name.py
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --syntax-check
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-management.yml --syntax-check
```

Les workflows de fichiers exigent root dans ce laboratoire isolé. Ne pas
les lancer sous root sur un DNS de production. La CI existante conserve les
mêmes parcours et seuils, et découvre les tests modifiés sans nouveau job.
Les résultats et empreintes de cette révision figurent dans `DELIVERY.json`.
Les mesures antérieures de couverture restent historiques ; aucune mesure
de couverture du YAML n’est revendiquée. Il n’y a pas de modification d’un
algorithme de production ni d’optimisation de débit revendiquée.

#### Recette système et retour de version

**NON EXÉCUTÉ** : vos serveurs RHEL 8 ne sont pas accessibles. Prérequis :
paire de recette représentative, sauvegardes exportées et vérifiées de BIND
et runtime, accès ADM de secours, ancres à jour et forwarders représentatifs.
Exécuter les modes `false`, `true`, `auto`, un second passage, les tests
[fonctionnels](../qualification/TESTS_FONCTIONNELS.md), un reload puis un redémarrage contrôlé.
Vérifier les unités et labels SELinux, les signatures valides/invalides, les
exceptions limitées et la préservation des NTA manuelles. Attendu : mêmes
fichiers et droits, politique sélectionnée respectée, réponses UDP/TCP et
renouvellement opérationnels. Risque restant : versions, confinement,
ordonnancement systemd et chaînes DNSSEC réels. Qualification de production
**NON TERMINÉE**.

**NON EXÉCUTÉ** : observation sur plus de sept jours, rotation RFC5011 et
charge représentative, faute de durée et d’environnement de production.
Sur un laboratoire représentatif, suivre le timer quotidien, les ancres,
la latence, les erreurs et la mémoire pendant cette période ; attendu :
exceptions renouvelées avant expiration, ancres maintenues et service stable.
Les expirations à long terme et la capacité réelle restent à qualifier.

**NON EXÉCUTÉ** : matrice CI distante et autres versions de contrôleur,
faute de dépôt distant exécuté. Importer la livraison dans votre dépôt et
lancer `.github/workflows/ci.yml` avec ses prérequis. Attendu : contrôles
applicables réussis sans réduction des seuils ; les différences de moteurs
hors environnement local restent à qualifier.

Le retour de version consiste à restaurer l’intégralité de la livraison
source sur le contrôleur ; les chemins et formats runtime sont identiques.
Ne pas mélanger des fichiers des deux révisions. Cette consolidation ne
requiert aucun déploiement immédiat. Si une exécution ultérieure échoue,
utiliser la restauration existante décrite dans [OPERATIONS.md](../exploitation/OPERATIONS.md),
avec sauvegardes persistantes en cas de perte du contrôleur.

<a id="journalisation"></a>
## Journalisation, rotation et SELinux : campagnes antérieures

Source documentaire du chapitre « Journalisation, rotation et SELinux : campagnes antérieures » : `docs/wiki.d/configuration/bind/JOURNALISATION.md`, SHA-256 `58996fdb9443c3bdb848f4450124014bb2ce6e77fea0624b07620c0deb89fecc` (copie antérieure conservée dans l’archive source de référence).

La rotation réelle sous BIND EL8 9.11.36 a reproduit le mode `0644` avant
cette correction, puis confirmé `0640` sur les trois journaux avec le même
scénario et la politique du drop-in corrigé.

### Parties impactées

| Fichier | Modification nécessaire |
|---|---|
| `roles/bind9/templates/named.conf.j2` | Canaux, catégories, désactivation explicite des requêtes ; renommage de l’ACL et de ses deux références |
| `roles/bind9/templates/named-runtime-override.j2` | Masque de création `0027`, conservant les journaux en `0640` après rotation |
| `roles/bind9/tasks/tasks.d/deploy/directories.yml` | Répertoire privé et préparation non destructive des trois fichiers |
| `roles/bind9/tasks/tasks.d/path_safety.yml` | Protection des nouveaux chemins contre les liens symboliques |
| `roles/bind9/tasks/tasks.d/deploy/security.yml` | Règle SELinux persistante, relabel récursif et contrôle des labels |
| `roles/bind9/tasks/main.yml`, `roles/bind9/tasks/tasks.d/deploy/firewall.yml` | Import et ajout idempotent du service DNS runtime/permanent dans les zones FRONT/BACK ; détails dans [FIREWALLD.md](../configuration/reseau/FIREWALLD.md) |
| `examples/deux-zones-forwardees/` | Deux configurations régénérées et texte aligné sur l’ACL |
| `tests/` | Tests des tâches, de l’idempotence, des canaux réels, de la rotation et de l’ACL ; adaptation des chemins du laboratoire et du test restorecon |
| `.github/workflows/ci.yml` | Ajout du script d’intégration logging au contrôle de syntaxe shell existant |
| `README.md`, `docs/wiki.d/exploitation/OPERATIONS.md`, `docs/wiki.d/configuration/reseau/ACL_SERVEURS.md`, `docs/wiki.d/configuration/reseau/CLIENTS_UNIQUES.md`, `docs/wiki.d/qualification/VALIDATION_RHEL8.md` | Exploitation et contrat d’ACL alignés |

Le point d’entrée, les routes, scripts Python de production, handlers
et variables publiques restent ceux de la livraison précédente. Le tag
`firewall` et sa gestion sont ajoutés à la demande. Les tests
pytest ajoutés sont découverts par la CI existante ; la CI distante n’a pas
été exécutée. Les métadonnées et le manifeste décrivent le diff exact.

### Vérifications et limites

La passe courante conserve ses preuves de recette et de couverture séparément
de la livraison antérieure. Les comptages ci-dessous sont historiques ; ils
ne constituent pas les mesures globales de la version en cours.

La recette actuelle a exécuté le scénario de rotation avec le démon BIND EL8
non privilégié et vérifié les trois modes `0640`, les cinq archives et la
suppression de la plus ancienne. Le validateur d’unités EL8 a lu `UMask=0027`.
Les modules Ansible de publication et le handler ont également été exercés,
avec une frontière de commande systemd doublée, pour le redémarrage et
l’idempotence. Ces contrôles ne qualifient pas un démarrage système réel.

#### Recettes historiquement consignées

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
[VALIDATION_RHEL8.md](../qualification/VALIDATION_RHEL8.md) et [OPERATIONS.md](../exploitation/OPERATIONS.md).
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

<a id="routes-seules"></a>
## Routes : syntaxe et recette du tag seul

Source documentaire du chapitre « Routes : syntaxe et recette du tag seul » : `docs/wiki.d/configuration/reseau/FRONT_ROUTES.md`, SHA-256 `930e05d6ffb415f08160491aacb0553bfe274d0b82fb13b9a9fec602c6fa580f` (copie antérieure conservée dans l’archive source de référence).

### Lisibilité et analyse du script

La révision `2026.10.19-syntax.1` rend explicites les littéraux de phase (`'done'`, `'intent'`), les gardes conditionnelles et l’initialisation de la variable de capture. Les expressions régulières restent des expressions Bash natives : une coloration d’éditeur ne constitue pas une erreur de parsing. Les callbacks de sortie sont appelés par `trap` ; seules deux exceptions locales SC2329 documentent cette limite de l’analyseur. Les protections d’application/reprise ne sont pas supprimées. Voir [le contrôle des scripts](../qualification/VERIFICATION_SYNTAXE_SCRIPTS.md).


### Recette du tag routes seul

**Recette historique de la révision citée ci-dessous ; ses compteurs et sa découverte par profil ne qualifient pas les sources de travail actuelles.**

Révision `2026.10.19-routes-only.1`. **EXÉCUTÉ** dans deux conteneurs EL8
jetables distincts, un par rôle primaire/secondaire, avec Python 3.6.8,
Ansible 2.9.27, Jinja2 2.10.3, BIND 9.11.36 et NetworkManager 1.40.16 natifs.
Le code du rôle, les playbooks, les inventaires et la CI sont identiques à la
livraison source ; aucun défaut de production n’a été reproduit dans ce périmètre.

La préparation du banc installe une configuration DNS avec `dnssec-validation auto;`
et routes désactivées, puis pose un vrai sceau avec `--tags hibernate`.
Ces deux exécutions préparent uniquement le BIND déjà déployé du laboratoire.
Le test demandé utilise ensuite **exclusivement `--tags routes`** :

| Étape par rôle | Résultat observé |
|---|---|
| `--tags routes --check` | Aucun changement de route, de profil NetworkManager ou de fichiers DNS. |
| `--tags routes` | Ajout de `203.0.113.0/24` et `172.21.7.23/32` dans le profil FRONT et dans le noyau. |
| Même commande une seconde fois | `changed=0`, mêmes routes et même profil. |
| Requêtes depuis un client cloud séparé | Réponses DNS attendues sur FRONT en UDP et TCP. |

Le récapitulatif de simulation annonce `changed=1` pour les ajouts prévus ;
ce compteur n'indique pas une écriture réseau. L'application annonce aussi
`changed=1`, puis la répétition annonce `changed=0` sur chaque rôle.

La passerelle système est uniquement sur BACK. L’IP de service FRONT est
secondaire ; sa passerelle est récupérée depuis son propre profil, sans
route par défaut FRONT active. Les routes préexistantes, les profils BACK/ADM
et un changement DNS en attente sur le profil FRONT sont préservés.

Les empreintes, inodes, dates de modification, droits et propriétaires des
fichiers DNS statiques, clés TSIG/RNDC, helpers et unités sont comparés avant
et après chaque étape. Le PID BIND et le registre des opérations de service
restent identiques : aucun redémarrage/rechargement demandé par ces appels.
Les exceptions DNSSEC du rôle et une exception manuelle sont conservées,
ainsi que le sceau d’hibernation et les traces de l’ancien `.operation.lock`.
Aucun journal `front-routes.pending` ne subsiste après succès. La simulation
peut créer le fichier vide `operations.flock` et son dossier privé ; le champ
de preuve `check_writes_nothing` désigne l’absence de changement réseau/DNS,
pas l’absence de toute écriture temporaire de contrôle.

**EXÉCUTÉ** : 180 tests ciblés sans exclusion ni test ignoré ; six
contrôles de syntaxe Ansible, compilation de 76 fichiers Python et analyse
syntaxique de sept scripts Bash. Les comptes des livraisons précédentes ne
sont pas additionnés. `DELIVERY.json` contient les résultats et empreintes.
Un avertissement de dépréciation de l'import `_yaml` provient du runtime
PyYAML du laboratoire ; il est conservé dans la preuve, sans échec de test.

#### Reproduire le laboratoire

Uniquement dans deux conteneurs neufs, isolés et déconnectés, disposant des
prérequis du [banc combiné](../exploitation/OPERATIONS.md#reproduction), avec ses dépendances
hors ligne dans `/runtime`. Ne jamais lancer le banc sur un serveur DNS :

```bash
export BIND_DEPLOY_ISOLATED=1
python3 tests/integration_deploy_auto_routes.py --role primary --scenario routes
# Dans un autre conteneur neuf :
python3 tests/integration_deploy_auto_routes.py --role secondary --scenario routes
```

Le même fichier de test est étendu ; aucun nouveau fichier de production,
service résident ou dépendance runtime n’est ajouté. Les tests existants et
la protection du nettoyage du banc sont conservés.

#### Limites et recette restante

**NON EXÉCUTÉ** : essai sur vos RHEL 8 avec systemd, firewalld, SELinux, SSH
et reboot réels, faute d’accès et de système RHEL complet. Le banc substitue
le transport systemd et firewalld, avec SELinux désactivé ; les commandes
BIND/RNDC, les profils NetworkManager et le noyau réseau sont réels.
Prérequis : hôte de recette représentatif, configuration BIND déjà active,
inventaire complet des deux pairs, sauvegarde des profils/routes, fenêtre
exclusive et accès ADM de secours. Lancer les deux commandes de la section
« Préparer puis appliquer », puis contrôler `ip -4 route get` vers un client,
`nmcli -g ipv4.routes connection show` sur le UUID FRONT et des requêtes DNS
UDP/TCP depuis un client autorisé. Refaire les contrôles après reboot planifié.
Attendu : deux magasins cohérents, retour par FRONT, défaut BACK conservé,
DNS inchangé. Risque restant : différences de versions, confinement, accès
SSH réel et persistance après redémarrage non reproduits ici.

**NON EXÉCUTÉ** : CI distante, matrice de tous les contrôleurs et charge,
sans dépôt distant ni environnement représentatif de capacité. Importer la
livraison et exécuter la CI existante, puis mesurer sous la charge de recette ;
attendu : contrôles réussis sans changement de seuil et capacité suffisante.
Cette campagne ciblée ne requalifie pas tout le management ou le déploiement.
Qualification de production **NON TERMINÉE**.

Pour revenir à la livraison source, restaurer son archive sur le contrôleur :
aucun changement runtime de cette révision n’exige de migration serveur.
Les routes déjà committées restent additives ; les supprimer volontairement
nécessite une intervention réseau ciblée et une sauvegarde préalable. Ne pas
effacer les fichiers d’exclusion ou les journaux pour annuler des routes.

<a id="transport"></a>
## Transport DNS IPv4/IPv6 : essais antérieurs

Source documentaire du chapitre « Transport DNS IPv4/IPv6 : essais antérieurs » : `docs/wiki.d/configuration/reseau/TRANSPORT_DNS.md`, SHA-256 `489722c42a5db282a2f73443d22e29f74667aa8628c4e569372414aeb1c666ad` (copie antérieure conservée dans l’archive source de référence).

### Preuves et limites

**EXÉCUTÉ** : BIND EL8 9.11.36 et BIND 9.18.24, sans option de famille forcée.
Un client IPv4 reçoit une réponse A puis AAAA via un upstream uniquement IPv6.
Une réponse TXT supérieure à la limite UDP force le repli TCP IPv6, confirmé
par le journal du vrai upstream. Les requêtes entrantes IPv6 UDP et TCP vers
le résolveur échouent. Les fichiers natifs restent inchangés.
Les nombres exacts figurent dans `DELIVERY.json`.

**NON EXÉCUTÉ** : service natif et réseau de vos cibles RHEL 8. Prérequis :
paire de recette sauvegardée, IPv6 sortant opérationnel si utilisé, forwarder
de recette IPv6 avec enregistrements connus et options systemd cohérentes.
Appliquer les commandes ci-dessus, interroger en IPv4 une donnée servie par
le forwarder IPv6, puis contrôler un RRset forçant le repli TCP et les journaux.
Attendu : réponses exactes, trafic sortant IPv6 autorisé, aucun listener DNS
IPv6. Risques : réseau/pare-feu cible, option externe de famille, confinement
SELinux et ancres réelles. Qualification de production **NON TERMINÉE**.

<a id="firewalld"></a>
## Firewalld : observations initiales et limites

Source documentaire du chapitre « Firewalld : observations initiales et limites » : `docs/wiki.d/configuration/reseau/FIREWALLD.md`, SHA-256 `111897ecdf02adbf76f244a4a085eb840ed0b307470f8cd5304d88d6c870b0a7` (copie antérieure conservée dans l’archive source de référence).

**EXÉCUTÉ** : expressions réelles Ansible 2.9/Jinja, et workflows du vrai
runner avec doubles `firewall-cmd`/`ip` : zones distinctes/runtime-permanent,
interface commune, zone par défaut, adresses secondaires, idempotence,
préservation SSH/affectations et refus de données ou commandes invalides.
Résultats courants dans `DELIVERY.json`.

**NON EXÉCUTÉ** : règles noyau/firewalld réels, trafic distant et reboot
RHEL. Raison : aucune cible accessible ; le laboratoire utilise des
doubles CLI. Prérequis : hôtes de recette avec firewalld actif et clients
autorisés. Procédure : contrôles ci-dessus, deuxième exécution et reboot.
Attendu : `dns` actif et permanent, accès DNS UDP/TCP autorisé, aucun
changement d’affectation ou de service sans rapport. Risques : règles
source/politiques locales prioritaires, service `dns` personnalisé,
versions cibles et dérive de configuration. La qualification opérationnelle
reste **NON TERMINÉE** tant que cette recette n’est pas effectuée.

<a id="loopback"></a>
## Loopback et zones vides : essais datés

Source documentaire du chapitre « Loopback et zones vides : essais datés » : `docs/wiki.d/configuration/reseau/LOOPBACK.md`, SHA-256 `b24902e8aa78ff6e34b5fab678547dda9da110804a398a9f851efe5c5524870d` (copie antérieure conservée dans l’archive source de référence).

**EXÉCUTÉ** : laboratoire BIND EL8 9.11.36 et BIND 9.18.24, chacun 109 configurations et 284 contrôles DNS, avec fichiers natifs inchangés. Les vraies tâches Ansible loopback, disponibilité primaire et vérification des fichiers sont aussi exercées ; voir [CORRECTION_CONTROLES_DNS.md](CORRECTION_CONTROLES_DNS.md) pour le défaut d’échappement Jinja corrigé et les parties impactées. **NON EXÉCUTÉ** : recette sur votre système RHEL réel ; raisons, procédure et risques dans [VALIDATION_RHEL8.md](../qualification/VALIDATION_RHEL8.md). Les modes DNSSEC `false`/`true`/`auto`, le canal RNDC uniquement IPv4 et les exceptions privées optionnelles sont décrits dans [DNSSEC.md](../configuration/bind/DNSSEC.md).

**EXÉCUTÉ sur cette révision :** 48 tests ciblés réussis avec chacun des moteurs Ansible 2.9.27/Jinja 2.10.3 et Jinja 3.0.3 ; contrôle syntaxique de l’inventaire de production, Pyflakes et compilation du test DNS. Le contrôle natif de la zone vide automatique réussit dans les deux moteurs BIND. Les suites complètes et workflows de la livraison précédente restent des preuves héritées, distinguées dans `DELIVERY.json`.

<a id="clients"></a>
## Réseaux clients : campagne de migration

Source documentaire du chapitre « Réseaux clients : campagne de migration » : `docs/wiki.d/configuration/reseau/CLIENTS_UNIQUES.md`, SHA-256 `1f0f5b78107dd334eda3a1d84ccdd91523749deadda867290e2a3d85382f5bf5` (copie antérieure conservée dans l’archive source de référence).

Depuis le contrôleur Linux de recette, à la racine du projet :

```bash
sudo python -m pytest -q tests
python tests/integration_preflight.py
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --syntax-check
sudo bash tests/run_integration_front_routes.sh
```

Les nombres et résultats exécutés sont consignés dans `DELIVERY.json`.
Les rendus où les deux listes sont vides vérifient seulement les branches et
références du template ; ces entrées sont refusées par le rôle avant déploiement.
Les cas couvrent fusion/doublons, une liste vide, refus des deux vides,
listes mal typées, accès DNS FRONT/BACK, routes exclusivement cloud,
internes seuls sans routage, surcharge et rollback.

**NON EXÉCUTÉ** : déploiement sur vos serveurs RHEL 8, DNF/Python cible réel,
SELinux, unités systemd, réseau/SSH réels, reboot cible, renouvellement DNSSEC
sur sept jours, restauration système, charge et CI distante. Raison : aucune
paire cible accessible. Prérequis, procédures, attendus et risques :
[VALIDATION_RHEL8.md](../qualification/VALIDATION_RHEL8.md). Qualification de production
**NON TERMINÉE**.


<a id="verrou-historique"></a>
## Politique de verrou historique optionnel

Source documentaire du chapitre « Politique de verrou historique optionnel » : `docs/wiki.d/exploitation/VERROUS_OPERATIONS_OPTIONNELS.md`, SHA-256 `61223ecdfb2d9a54302ffc5f4ef8e5929c5373668258af173f808951e859e5bc` (copie antérieure conservée dans l’archive source de référence).

La politique de verrou reproduite dans ce chapitre appartient à son archive source ; le [contrat de concurrence courant](../exploitation/VERROUS_OPERATIONS_OPTIONNELS.md) a depuis évolué.

Cette révision repart exclusivement de l’archive fournie `Bind9-Ansible-hibernate-deployment.zip`, SHA-256 `63f826d85deb0ce68ee45dbe87a77fade81df44f636e5fdd0749281d2052c992`. Elle applique la demande d’ignorer temporairement le verrou commun hors hibernation. Elle ne réimporte pas les modifications des livraisons intermédiaires de diagnostic ou de découverte des passerelles.

### Contrat d’exploitation

```yaml
bind_operation_lock_enabled: false
```

Cette nouvelle entrée du rôle est un booléen YAML strict, par défaut `false`. Une chaîne `"false"`, un entier ou une valeur nulle sont refusés. Pour la passer sur la ligne de commande, employer du JSON typé : `-e '{"bind_operation_lock_enabled":false}'`. Avec `true`, le rôle réactive le verrou commun pour les opérations ordinaires. L’argument de bas niveau `operation_lock` du module `bind9_manage` reste à `true` par défaut pour préserver les appels directs ; le rôle lui transmet explicitement son nouveau réglage.

| Opération | Réglage par défaut | Avec `bind_operation_lock_enabled: true` |
|---|---|---|
| Déploiement sans création du sceau | Verrou commun ignoré | Verrou commun requis |
| Routes, reprise de routes, sauvegarde | Verrou commun ignoré | Verrou commun requis |
| Management : lecture, listing, simulation, écriture unitaire ou lot | Verrou commun ignoré | Verrou commun requis |
| Tag isolé `hibernate` | Verrou commun requis | Verrou commun requis |
| Déploiement complet `all` ou `deploy` avec `hibernate_deployment: yes` | Verrou commun requis | Verrou commun requis |
| `--check` | Aucun verrou commun acquis | Aucun verrou commun acquis par le garde de déploiement ; le management conserve son contrat de simulation avec verrou en mode strict |

La présence de `hibernate_deployment: yes` dans l’inventaire ne force pas le verrou commun pour un parcours isolé `routes`, `routes-recover` ou `backup`. La création d’un sceau, elle, reste protégée.

Un ancien `/var/lib/Bind9-Ansible/.operation.lock` est **ignoré sans être supprimé, modifié ni adopté** en mode ordinaire sans verrou. Il n’est pas nécessaire de le nettoyer pour ce contournement. Un sceau `/var/lib/Bind9-Ansible/.deployment.lock` existant continue de bloquer toute reconstruction DNS, même avec les deux réglages à `false`. Il ne bloque pas les routes ou le management.

Les journaux FRONT, `front-routes.lock`, sauvegardes de transaction, contrôles des chemins et de révision, protections des fichiers live et verrou interne des sauvegardes restent en place. Ne pas les effacer pour forcer une opération. Une reprise incomplète nécessite toujours une analyse de son journal ; sans verrou commun, elle ne bloque plus automatiquement tous les autres parcours.

**Une seule opération doit être lancée à la fois**, sur chaque serveur, y compris les autres automatisations et les interventions manuelles. Pendant une hibernation, arrêter également ces autres opérations : celles qui ignorent le verrou commun ne peuvent pas être exclues par ce verrou. Les checksums détectent les conflits observables, mais ne remplacent pas l’exclusion mutuelle. L’atomicité d’un lot de plusieurs fichiers face à une écriture concurrente ou un crash n’est pas garantie.

### Ajouter uniquement une route

Depuis la racine du projet sur le contrôleur Linux, avec les variables existantes `bind_route_inventory` et `bind_route_host` renseignées :

```bash
ansible-playbook -i "$bind_route_inventory" playbooks/bind9-deploy.yml \
  --limit "$bind_route_host" --tags routes --check \
  -e '{"bind_front_routes_enabled":true,"bind_operation_lock_enabled":false}'

ansible-playbook -i "$bind_route_inventory" playbooks/bind9-deploy.yml \
  --limit "$bind_route_host" --tags routes \
  -e '{"bind_front_routes_enabled":true,"bind_operation_lock_enabled":false}'
```

Conserver l’inventaire complet des deux pairs, les variables clientes et les secrets existants. Ce parcours ne reconstruit pas BIND. Les autres gardes du routage restent actifs : cette modification ne corrige pas une passerelle absente du profil, un réseau ambigu ou une reprise FRONT en attente. Le tag `routes-recover` sert uniquement à reprendre un journal FRONT effectivement présent ; ce n’est pas une suppression générale des traces d’opération.

Pour revenir au mode strict, déclarer `bind_operation_lock_enabled: true`. Avant cette réactivation, examiner tout ancien verrou et les journaux associés, vérifier qu’aucune opération n’est active et achever toute reprise. Ne jamais supprimer un verrou protégeant une opération active. Le retour au code de l’archive initiale restaure aussi le verrou obligatoire, sans annuler les routes ou données déjà modifiées.

### Parties du code impactées

| Fichier | Changement |
|---|---|
| `roles/bind9/defaults/main.yml` | Nouvelle entrée publique, défaut `false`. |
| `roles/bind9/tasks/tasks.d/deploy/guard.yml` | Validation du type et acquisition conditionnelle ; acquisition imposée pour l’hibernation. Libération uniquement si acquise. |
| `roles/bind9/tasks/tasks.d/manage/main.yml` | Validation et transmission du réglage au module. |
| `roles/bind9/library/bind9_manage.py` | Argument optionnel ; exécution avec ou sans contexte de verrou commun. |

Le cœur du manager, les tâches de routes, les templates DNS, les inventaires et les playbooks sont identiques à l’archive initiale. Le banc existant de transport management choisit explicitement le mode strict afin de conserver ses assertions de concurrence. Trois nouveaux fichiers de tests couvrent la politique, le contournement et l’entrée réelle du parcours routes. La CI ajoute ces trois fichiers à sa commande privilégiée existante, sans changement de dépendances ni de seuils.

### Vérifications et limites

Les résultats exacts, empreintes des fichiers de production et contrôles d’archive sont enregistrés dans `DELIVERY.json`. Les rapports d’autres révisions sont historiques. Les tests locaux utilisent Python 3.6.8 EL8, Ansible 2.9.27 et Jinja2 2.10.3. Les commandes système DNS et réseau des scénarios Ansible sont des doublures isolées ; aucune route du serveur réel n’a été ajoutée.

La suite générale compte **960 scénarios réussis**, complétés par **2 scénarios routes**. Sur les **22 scénarios ciblés**, 21 ont réussi lors de la première exécution ; un nouveau test ne propageait pas son tag à l’inclusion dynamique et n’exécutait aucune tâche. Seul ce test a été corrigé et réexécuté avec succès, sans modification de la production ni affaiblissement d’une assertion. Le journal initial et celui de la correction sont distingués dans les preuves. Les contrôles de syntaxe couvrent 6 combinaisons playbook/inventaire, 73 fichiers Python et 6 scripts shell.

| Statut | Contrôle et portée |
|---|---|
| **EXÉCUTÉ** | Le scénario « routes avec ancien verrou présent » échoue sur l’archive initiale à `mkdir` avec `File exists` : reproduction déterministe de la restriction supprimée, sans établir la cause de l’incident distant où le chemin était annoncé absent. |
| **EXÉCUTÉ** | Politique de sélection, types invalides, opérations ordinaires avec ancien verrou conservé, hibernation toujours exclusive, sceau toujours bloquant pour la reconstruction, management et refus du checksum périmé ; résultats détaillés dans `DELIVERY.json`. |
| **EXÉCUTÉ** | Entrée réelle du rôle pour routes : simulation, ajout puis répétition sans nouvel ajout, anciens verrou et sceau conservés, configuration DNS inchangée ; doublures `ip`/`nmcli`, deux modes Jinja. |
| **EXÉCUTÉ** | Suite générale, syntaxe des deux playbooks avec trois inventaires, compilation Python 3.6.8, syntaxe des scripts shell ; résultats et empreintes dans `DELIVERY.json`. |
| **NON EXÉCUTÉ** | Recette sur vos serveurs : aucun accès au réseau cloisonné. Prérequis : fenêtre exclusive, sauvegarde et inventaire existants, SSH/sudo, outils RHEL/NetworkManager disponibles. Exécuter les deux commandes routes ci-dessus, vérifier `nmcli -g ipv4.routes connection show uuid UUID_FRONT`, `ip -4 route show`, puis répéter l’application. Attendus : acquisition commune sautée, seule la route nécessaire ajoutée, profil et noyau cohérents, répétition sans ajout. Risque restant : particularités réelles des profils, adresses, passerelles et versions. |
| **NON EXÉCUTÉ** | Recette hibernation et management sur clone RHEL : BIND opérationnel, sauvegarde et absence d’exécution concurrente requis. Exécuter le tag `hibernate` selon son guide ; vérifier le sceau, le refus d’une reconstruction et une lecture/checksum/simulation management selon son guide. Attendus : sceau conservé et écriture ciblée seulement. Risque restant : intégration système réelle, réplication et reprise après incident. |
| **NON EXÉCUTÉ** | Matrice CI complète, charge et crash physique : environnement de qualification indisponible. Lancer la CI livrée et les scénarios de panne documentés sur clone jetable ; attendus : contrôles réussis et reprise sans suppression des données d’un opérateur. Risque restant : combinaisons de versions, stockage et disponibilité non qualifiés. |

**Qualification production : NON TERMINÉE**, dans l’attente de ces recettes. La livraison retire un blocage identifié dans le flux de contrôle ; elle ne démontre ni la cause distante du verrou ni l’absence de tout incident possible.

---

Documentation DNSZIBO · contenu harmonisé le 25 novembre 2026.
