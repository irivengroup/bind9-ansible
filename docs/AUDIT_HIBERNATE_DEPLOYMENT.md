# Audit du projet et hibernation — 18 octobre 2026

**Rapport historique :** ces résultats décrivent la révision indiquée ci-dessous. Le contrat et les preuves de la livraison courante figurent dans [REFONTE_ROUTES_MANAGEMENT.md](REFONTE_ROUTES_MANAGEMENT.md).

**Rapport historique de l’archive restaurée.** Les résultats ci-dessous ne sont pas une exécution de la révision courante. Pour le changement de verrou demandé et ses vérifications, voir [VERROUS_OPERATIONS_OPTIONNELS.md](VERROUS_OPERATIONS_OPTIONNELS.md).

Révision **2026.10.18-hibernate-deployment.1**, projet **Bind9-Ansible**.
Source : `Bind9-Ansible-management-fiabilite.zip`, SHA-256
`212c5f983928d39972060f49a40134f36b95459199b6af823109e007d699344a`.
Les références synchronisées et les livraisons précédentes restent inchangées.

## Contrat et périmètre

L’entrée publique finale est **`hibernate_deployment: no`**. À `yes`, le rôle
crée le sceau après déploiement complet réussi et contrôles de santé. Le tag
isolé **`hibernate`** crée uniquement le sceau du fichier canonique live après
contrôles de sécurité et de syntaxe, sans redéploiement ni preuve de santé DNS.
Un sceau existant reste bloquant avec `yes` et `no`, jusqu’à retrait opérateur,
comme confirmé par l’utilisateur. Anciens noms `force`, `deployment_lock` et
`deployment_locked` refusés. Voir [HIBERNATE_DEPLOYMENT.md](HIBERNATE_DEPLOYMENT.md).

La passe parcourt points d’entrée, graphe des tâches/imports, defaults, handlers,
filtres/utilitaires, templates/exemples, trois inventaires, management, tests,
CI et exploitation. Les tests de joignabilité contrôlent tâches, assets,
filtres exportés et variables produites sans consommateur. La liste exhaustive
des changements est dans `DELIVERY.json`. Le moteur I/O du management,
les inventaires, templates DNS et algorithmes de routes sont conservés.
Le management utilise les données live ; le déploiement reste une reconstruction.

## Impacts, risques traités et décisions

| Partie | Modification et invariant |
|---|---|
| `defaults/main.yml`, `tasks/main.yml`, `deploy/guard.yml` | Paramètre unique, validation compatible Ansible 2.9/Jinja 2.10, refus explicite des anciens noms. Un défaut `no` ne supprime ni ne contourne un sceau. Le garde appartient au bloc protégé. |
| `deploy/hibernate.yml`, `seal.yml` | Tag `never, hibernate`, isolé des autres actions, exige `yes`. Fichier canonique root, régulier, lien unique, non modifiable par groupe/autres, de 1 octet à 16 Mio ; parser natif sous GNU `timeout` (TERM après 60 s, KILL 5 s après). Simulation sans sceau ; sceau existant conservé sans modification. |
| Sceau | Création atomique privée, manifeste SHA-256 et empreinte du manifeste ; `payload.kind` distingue `deploy` et `hibernate`. Le tag seul empreinte uniquement `/etc/named.conf`, pas tout le graphe d’includes/masters. Ce n’est ni une signature ni la révision complète du management. |
| Concurrence | Relecture du sceau après acquisition. Verrou commun également pour `routes`, `routes-recover`, `backup`. La présence d’un sceau ne bloque pas ces opérations auxiliaires sous verrou commun. |
| `deploy/lease_release.yml`, `deploy/main.yml` | Suppression récursive retirée ; identité périphérique/inode, type, propriétaire et mode contrôlés, seul notre répertoire vide est retiré. Une reprise DNS/FRONT incomplète, y compris partielle ou routes seules, un contenu inattendu ou un remplacement conserve le verrou et échoue explicitement. |
| `soa_health.py`, tâches DNS, moniteur | Refus TC, statuts dupliqués/contradictoires et compteurs ANSWER incohérents. `dig -r` isole de `.digrc`, IPv4 explicite ; UDP sans repli implicite via `+ignore`, TCP séparé/explicite. Propriétaire/type/serial restent vérifiés. |
| Tests et CI | Appels `subprocess` et contrôle de grammaire adaptés à Python 3.6, assertions conservées. Absence de namespaces signalée, alternative en conteneur avec les cinq corps Bash originaux. CI `contents: read`, nouveaux tests, seuils 98 % conservés ; le job de namespaces exigés échoue si cette capacité manque. |
| Documentation/packaging | README, tags, migration, reprise des routes et retrait opérateur alignés ; rapports anciens identifiés, manifeste et ZIP reproductible actualisés. |

Les menaces traitées sont l’écrasement après maintenance, les observations
périmées, les écritures coopérantes concurrentes, la perte de preuves et les faux
résultats de santé. Les protections I/O précédentes restent présentes :
[FIABILITE_DONNEES_ET_IO.md](FIABILITE_DONNEES_ET_IO.md).

## Preuves observées

Laboratoire : conteneur standard Rocky Linux 8.9 avec paquets EL8.10,
Python **3.6.8**, Ansible **2.9.27**, Jinja2 **2.10.3**, BIND
**9.11.36-16.el8_10.16**. Sources montées en lecture seule, aucune capacité
supplémentaire. Recettes natives sans réseau externe, interface loopback seule,
aucun port publié. Les démons des bancs sont root, pas le service systemd RHEL.

| Statut | Contrôle | Résultat et portée |
|---|---|---|
| **EXÉCUTÉ** | Suite complète source précédente | 60 échecs, 908 réussites avec Python 3.6.8. Les incompatibilités du banc sont corrigées ; l’indisponibilité des namespaces imbriqués est identifiée séparément. Ce ne sont pas 60 défauts du rôle déployé. |
| **EXÉCUTÉ** | Reproduction avant correction | 7 scénarios de verrouillage et 5 de sondes échouent sur les sources précédentes correctement relocalisées. La suppression des données d’un verrou remplacé est aussi constatée directement sur disque. |
| **EXÉCUTÉ** | Suite globale | **1046 tests réussis, 5 ignorés** : les cinq chemins pytest de namespaces ne disposent pas de la capacité nécessaire (`unshare: Operation not permitted`). Leurs corps fonctionnels sont exécutés séparément dans le conteneur. Dernier garde FRONT et scénario ajouté ensuite contrôlés par les passes ciblées ci-dessous ; ce scénario n’est pas compté dans les 1046. |
| **EXÉCUTÉ** | Passe finale verrouillage/routes | 99 tests réussis : paramètres, garde, sceau, tag isolé, simulation, refus, leases, routes et reprise. Recouvrement avec la suite globale : ne pas additionner les comptes. |
| **EXÉCUTÉ** | Derniers gardes | 19 tests réussis : parcours d’hibernation, relocalisation finale du banc, reprise partielle et conservation du verrou commun pour une opération FRONT isolée encore en attente. |
| **EXÉCUTÉ** | Moteur de management | 352 tests réussis ; **1313/1337 lignes, 98,20 %**, seuil 98 %. Moteur de production conservé octet pour octet ; mesure ciblée, pas une couverture exhaustive du YAML ou des branches. |
| **EXÉCUTÉ** | Cinq modules DNS/secrets | 265 tests réussis ; **391/392 lignes, 99,74 %** sur `dns_topology`, `soa_health`, `client_acl`, `bind-private-dnssec`, `tsig-secret`. `soa_health` : 100 % des lignes. |
| **EXÉCUTÉ** | BIND EL8 et hibernation native | 108 configurations chargées : 12 hôtes et 96 combinaisons de template. Les rendus à deux listes clientes vides ne sont pas des entrées acceptées par le rôle. Huit sondes A/PTR UDP/TCP via Ansible, moniteur sur réponses réelles avec systemctl doublé, `.digrc` perturbateur. Playbook public : simulation, création, répétition sans changement, refus de reconstruction avec `no`. Chemins d’outils avec espaces contrôlés. |
| **EXÉCUTÉ** | Management natif | DNSSEC `no` et `auto` : 12 transactions par mode, dont 2 lots mixtes ; listings, validation/reload/DNS, vraies archives, refus RDATA invalide, rollbacks unitaire et commun. SELinux doublé. |
| **EXÉCUTÉ** | Sauvegarde/restauration en conteneur | 5 corps de tests Bash originaux, assertions conservées : restauration, refus service actif, symlink, droits d’une racine générale, erreur de labels. Vrais tar/gzip/fichiers/SHA ; systemd/BIND/SELinux doublés. Racine du conteneur jetable, sans namespace de montage imbriqué. |
| **EXÉCUTÉ** | Retrait opérateur | Bloc Bash documenté exécuté dans le conteneur : copie privée, comparaison SHA, synchronisation, retrait du seul sceau, libération du répertoire vide. Pas de dépendance `cmp`/diffutils ajoutée. |
| **EXÉCUTÉ** | Syntaxe et inventaires | Deux playbooks × trois inventaires en syntax-check ; preflight des 12 hôtes avec facts RHEL simulés ; compilation Python 3.6.8 et syntaxe Bash. |
| **EXÉCUTÉ** | Analyse statique | Bandit 1.7.10 sur tout le Python de production et Pyflakes 3.2.0 sur production/tests : aucune alerte. Outils modernes exécutés dans un second conteneur, pas ajoutés à la cible Python 3.6. |
| **EXÉCUTÉ** | Packaging | 212 fichiers ; source ZIP vérifiée, 67 fichiers de production comparés octet pour octet au banc final, inventaires/templates/moteur management et algorithme FRONT inchangés. Manifestes, CRC, contenu et permissions préservées ; deux constructions identiques. Caches, secrets générés et journaux bruts exclus du ZIP. |

Journaux bruts hors ZIP, empreintes et sources testées dans
[PREUVES_HIBERNATE_DEPLOYMENT.txt](PREUVES_HIBERNATE_DEPLOYMENT.txt).
Les suites recouvrantes ne sont pas additionnées. Les refus attendus de l’ancienne
version ne sont pas des échecs non résolus du code livré. Une campagne globale
instrumentée trop coûteuse est remplacée par la suite globale et les deux mesures
ciblées, en conservant leurs périmètres et seuils.

## Reproduire

Depuis la racine, dans un Linux de recette isolé avec les dépendances test :

```bash
export ANSIBLE_CONFIG="$PWD/ansible.cfg"
export BIND_MANAGEMENT_CHECKCONF=/usr/sbin/named-checkconf
python3 -m pytest -q -rs tests
python3 tests/integration_preflight.py
```

Les étapes de couverture exactes sont dans
[.github/workflows/ci.yml](../.github/workflows/ci.yml). Le banc Python 3.6 utilise
pytest 6.2.5 et coverage 6.2. La CI dédiée impose `BIND_TEST_REQUIRE_MOUNT_NS=1` ;
UID root seul ne démontre pas la disponibilité d’`unshare --mount`.

Les commandes suivantes nécessitent un **conteneur jetable root, sans réseau
externe ni montage hôte/volume sur les chemins BIND**. Elles peuvent réinitialiser
`/etc/named.conf`, `/var/named` et les données de test : jamais sur un serveur
opérationnel. Copier avant les essais les fichiers officiels EL8 dans
`/tmp/native` : `named.localhost`, `named.loopback`, `named.empty`,
`named.rfc1912.zones`, `named.root.key`, `bind.config`. Préparer `/tmp/bindbin`
avec les outils natifs `named-checkconf`, `named-checkzone`, `rndc`,
`dnssec-keygen`, `dnssec-signzone`, `dig`.

```bash
export BIND_MANAGEMENT_ISOLATED=1
python3 tests/integration_hibernate.py --bin-dir /tmp/bindbin \
  --native-dir /tmp/native --named /usr/sbin/named
python3 tests/integration_backup_container.py
for mode in no auto; do
  BIND_MANAGEMENT_DNSSEC="$mode" BIND_MANAGEMENT_TEST_UPSTREAM=127.0.0.1 \
    python3 tests/integration_management.py --bin-dir /tmp/bindbin \
      --native-dir /tmp/native --named /usr/sbin/named
done
```

La recette d’hibernation exige l’absence initiale de sceau et le conserve après
contrôle. Pour la rejouer : retrait opérateur dans le conteneur jetable ou nouveau
conteneur. Ne pas retirer un sceau de serveur réel pour faire passer un test.

## Compatibilité, exploitation et rollback

Aucune collection ni bibliothèque Python de production ajoutée. `timeout`,
`sha256sum`, `sync`, `mkdir`, `rmdir` proviennent de GNU coreutils EL8. Les commandes
natives `backup --force` et RNDC `-force` restent distinctes du paramètre retiré.
Retirer les anciens noms, choisir le parcours et définir éventuellement `yes`.
Pour protéger un état déjà maintenu sans reconstruction, utiliser `hibernate`.
Une modification seule de l’inventaire ne produit aucune écriture distante.

Le rollback conserve l’archive exacte et la restauration runtime. Une reprise
incomplète conserve le verrou commun. Suspendre les automatisations, confirmer
l’absence de propriétaire actif, inspecter DNS et le journal FRONT avant reprise
selon [FRONT_ROUTES.md](FRONT_ROUTES.md). Le retrait du sceau est distinct.
Revenir au ZIP source impose de requalifier son contrat `force` et les données
réelles ; un retour de code ne restaure ni DNS, routes, secrets ou état runtime.
Ne pas effacer un sceau ou un journal pour simuler un rollback.

Les checksums et contrôles natifs ajoutent des lectures I/O. Aucune mesure de
débit, latence ou durée sur la volumétrie utilisateur n’est déduite de ces tests.

## Qualification production — NON TERMINÉE

| Statut | Contrôle, raison et prérequis | Procédure, attendu et risque restant |
|---|---|---|
| **NON EXÉCUTÉ** | Paire RHEL 8 utilisateur : aucun accès. Clone représentatif, inventaire privé, sauvegardes, ADM et clients de test requis. | Déployer avec `yes`, recetter DNS/SOA/AXFR/accès, refuser un second déploiement avec `yes` et `no`, tester tag isolé et retrait opérateur. Attendu : données stables et paire convergée ; DNF et effets système restent à qualifier. |
| **NON EXÉCUTÉ** | SELinux enforcing, systemd/named et firewalld réels : banc non représentatif. | En recette : `named-checkconf -z`, `ls -lZ`, état des services/journaux, DNS UDP/TCP des deux segments, reboot. Attendu : droits, labels, flux et persistance corrects ; les doubles ne le prouvent pas. |
| **NON EXÉCUTÉ** | NetworkManager/routage réel : workflows à doubles dans cette passe. Clone avec ADM de secours requis. | Activer les routes, comparer profil/noyau, idempotence, conflit contrôlé, rollback et reboot. Attendu : seuls les ajouts possédés sont retirés, ADM/BACK intacts ; version et réseau cible restent à qualifier. |
| **NON EXÉCUTÉ** | Crash, pannes physiques, stockage distant, blocs/inodes épuisés : aucun banc de panne autorisé. | Sur clone jetable, interrompre des écritures, provoquer des fautes réelles, restaurer et vérifier journaux. Attendu : refus ou reprise sans écrasement manuel ; les injections ne démontrent pas le matériel ou les I/O noyau bloquées. |
| **NON EXÉCUTÉ** | Charge, disponibilité, RPO/RTO, volumétrie : non fournis. | Mesurer backup/checksum/syntaxe/reload et requêtes sous charge, fixer seuils/alertes. Attendu : objectifs métier respectés ; aucune durée ni absence totale d’interruption certifiée. |
| **NON EXÉCUTÉ** | Cycle DNSSEC long, upstream IPv6 et FIPS réels : hors recette native courante. | Qualifier ancres/chaînes, NTA au-delà de sept jours, IPv6 sortant et absence d’écoute DNS IPv6. Attendu : politiques locales respectées ; preuves historiques insuffisantes pour cette recette. |
| **NON EXÉCUTÉ** | CI distante/matrice complète : aucun lancement distant. | Exécuter les jobs livrés et seuils 98 %. Attendu : combinaisons requises réussies ; présence du YAML seule insuffisante. |
| **NON EXÉCUTÉ** | Chemin de test en namespaces de montage imbriqués : capacité absente du conteneur standard. | Sur Linux jetable avec capacités adéquates : `sudo env BIND_TEST_REQUIRE_MOUNT_NS=1 python3 -m pytest -q tests/test_configtool_workflows.py`. Attendu : 5 cas exécutés sans skip. Les mêmes corps fonctionnels ont réussi sous isolation conteneur ; ce mode d’isolation précis reste à contrôler. |

La limitation du diagnostic court des clés YAML dupliquées d’Ansible 2.9.27
reste non corrigée dans la dépendance : le refus avant les tâches est testé,
pas une amélioration de ce message. Voir [VALIDATION_FIABILITE_IO.md](VALIDATION_FIABILITE_IO.md).

Une configuration déjà amputée mais valide avant lecture ne peut pas être
reconstituée sans référence indépendante. Remplacements atomiques par fichier,
pas par ensemble ni par paire. Les verrous protègent les acteurs coopérants ;
root peut les ignorer ou intervenir entre contrôles. La livraison réduit les
risques identifiés sans garantir l’absence totale d’incident.
