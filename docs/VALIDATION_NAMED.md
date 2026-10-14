# Vérification de named.conf.j2 — livraison du 14 octobre 2026

## Conclusion et périmètre

Le template canonique et les fichiers de zones associés ont été inspectés puis testés avec de vrais processus BIND 9.18.24 dans un espace réseau Linux isolé. **EXÉCUTÉ** : 44 configurations chargées et 107 contrôles DNS réussis, rejoués sur la copie nettoyée décrite dans [VALIDATION_NETTOYAGE.md](VALIDATION_NETTOYAGE.md). Cela couvre les branches rendues et les scénarios décrits ci-dessous ; cela ne constitue pas une preuve universelle pour tout inventaire, toute version BIND ou tout incident.

La qualification de production est **NON TERMINÉE** tant que la recette RHEL 8 / Python 3.6.8 / BIND et NetworkManager réellement installés n'est pas exécutée. Les fichiers EL8 natifs employés proviennent du [paquet AlmaLinux c8s](https://git.almalinux.org/rpms/bind/src/branch/c8s), pas du serveur utilisateur. Leur empreinte est inchangée après les essais. Aucun service système ni réseau utilisateur n'a été reconfiguré.

## Défauts constatés et corrections intégrées

| Défaut observable | Correction et non-régression |
|---|---|
| Une zone forward enfant de l'autorité locale renvoyait NXDOMAIN depuis cette autorité ; le forwarder direct répondait pourtant NOERROR | Délégation NS/glue générée dans le parent local le plus proche ; pour un suffixe indépendant, délégation dans la racine privée. Le garde rejette les collisions, les données cachées et les topologies ambiguës plutôt que tous les enfants forwardés. Essais UDP/TCP sur deux topologies, deux serveurs et trois vues. |
| Le contrôle d'inventaire rejetait les six exemples fournis à cause de cet emboîtement | Le garde de topologie reconnaît les délégations réellement générées ; les vraies assertions Ansible passent sur les 12 hôtes. Les serials des exemples sont avancés à 2026101401 pour publier les changements. |
| Les zones privées non signées nécessitent une politique DNSSEC explicite | `dnssec-validation auto` reste actif ; `dnssec_validate: false` produit uniquement les exceptions des suffixes forwardés déclarés. Les six suffixes privés `.test` confirmés les utilisent. Défaut par zone : true ; les chaînes signées ne sont pas qualifiées par ce test. |
| Les permissions de zone sont conservées par `in-view`, même lorsqu'une vue cliente interdit les transferts | La règle de zone exige secondaire BACK ET TSIG ; la sélection FRONT exclut les IP BACK des pairs et leur clé, y compris avec une ACL FRONT large. Tests AXFR signé/non signé, mauvaise source et mauvaise destination. |
| Une sortie vide, « Transfer failed » ou une panne réseau était acceptée par l'outil AXFR comme preuve de refus | L'outil demande un RCODE DNS explicite, refuse les erreurs TSIG et considère ces autres résultats comme inconclusifs. Une SOA transférée est une défaillance de sécurité. Régressions automatisées fournies. |
| Le template inverse porte deux marqueurs, mais son réconciliateur attendait le marqueur inverse dès la première ligne | Le réconciliateur accepte le vrai en-tête et l'ancien, conserve le refus des fichiers non gérés, avance le serial en cas de changement et refuse l'overflow avant réécriture. Tests avec le vrai template. |
| Le contrôle SOA utilisait une sous-chaîne du serial d'inventaire, même après augmentation du serial inverse | Extraction du serial réellement vérifié dans le fichier primaire, comparaison de la SOA exacte avec NOERROR et AA ; régression pour serial trouvé uniquement dans un timer SOA. |
| Sur un secondaire neuf, le contrôle primaire utilisait dig avant son installation | Installation des dépendances avant ce contrôle. La recette d'installation réelle RHEL reste à réaliser. |

Le correctif forwarding conserve la racine privée : les noms non déclarés restent NXDOMAIN, et une requête absente du cache provoque SERVFAIL lorsque son forwarder est arrêté, sans fallback Internet. Les réponses encore en cache conservent leur comportement DNS normal. Les délégations sont des données internes de ce résolveur privé, pas une procédure de publication DNS publique. Le port de forwarding reste celui de l'inventaire.

## Vérifications du code et impact

| Contrôle | Statut | Résultat |
|---|---|---|
| Suite complète `python -m pytest -q tests` | **EXÉCUTÉ** | 207 tests réussis sur la copie nettoyée, Python 3.8.20 |
| Couverture des trois filtres contrôleur modifiés/ajoutés | **EXÉCUTÉ** | 126 tests ciblés ; 392 instructions, deux non couvertes, 99 %, seuil 98 % respecté. Ce chiffre ne représente pas la couverture globale des scripts hérités/YAML. |
| Bandit 1.7.10 | **EXÉCUTÉ** | Quatre fichiers Python de production modifiés analysés, aucun résultat signalé, aucune règle désactivée |
| Syntaxe / compilation | **EXÉCUTÉ** | Ansible 2.9.27 sur dev/preprod/prod, Bash des deux outils et des deux lanceurs, compilation Python ; grammaire 3.6 du script cible |

| Domaine | Impact / invariants |
|---|---|
| Configuration | Un seul template named.conf, include natif conservé, DNSSEC actif avec exceptions limitées ; gardes de collision et cuts privés générés |
| Données / compatibilité | NS/glue ajoutés aux parents, serials d'exemple avancés ; vos zones existantes demandent aussi un serial supérieur. Deux formats d'en-tête inverse acceptés. |
| Sécurité | Refus FRONT des pairs/clé, adresse ET clé pour AXFR, refus par défaut et données dynamiques interdites ; absence de secret généré dans la livraison |
| Réseau | Uniquement ajouts/suppressions des routes owned ; aucun profil, adresse, défaut, DNS système ou service NetworkManager reconfiguré |
| Exploitation / reprise | Sauvegarde BIND avant changements, journal des routes jusqu'au commit, rollback local à l'hôte ; qualification réelle de restauration encore requise |
| Livraison | Sources, README, tests, rapports et manifestes ; ZIP contrôlé et paramètres fixes. Les runtimes et logs de laboratoire sont exclus. |

Menaces traitées : interpolation BIND avec entrées malformées, accès/AXFR hors ACL, collision de zones natives, boucle vers les pairs, modification de routes non détenues, journal corrompu et fuite de secrets dans logs/archives publiques. Les garde-fous sont l'entrée typée, les validations BIND, les ACL/TSIG, les snapshots comparés, le journal privé validé et les exclusions de livraison. Les droits root de la cible et les forwarders privés restent des éléments de confiance ; aucune résistance à un administrateur root malveillant n'est annoncée.

## Matrice exécutée

| Contrôle | Statut | Résultat observé |
|---|---|---|
| Rendus dev/preprod/prod, deux sites, primaire/secondaire | **EXÉCUTÉ** | Les 12 configurations réellement livrées se chargent avec `named-checkconf -z` |
| Matrice des branches | **EXÉCUTÉ** | 32 rendus supplémentaires : deux rôles × quatre combinaisons ACL × quatre combinaisons autorité/forwarding |
| Assertions Ansible d'inventaire | **EXÉCUTÉ** | Trois inventaires, 12 hôtes ; assertions réelles, connexion locale, faits RHEL simulés, aucune mutation |
| Loopback natif | **EXÉCUTÉ** | localhost A/AAAA, localhost.localdomain A, PTR 127.0.0.1 en UDP/TCP ; mauvaise source refusée |
| Autorité locale / FRONT / BACK | **EXÉCUTÉ** | Réponses exactes, sources/destinations non autorisées refusées |
| AXFR | **EXÉCUTÉ** | Secondaire BACK signé autorisé ; non signé BACK et signé FRONT/local refusés ; source BACK secondaire sur destination FRONT refusée |
| Paire et NOTIFY | **EXÉCUTÉ** | Transfert initial signé, données secondaires attendues, serial et record convergents après modification et NOTIFY |
| Forwarding enfant et indépendant | **EXÉCUTÉ** | Réponses exactes primaire/secondaire dans les vues locale, FRONT, BACK, en UDP/TCP |
| Hors suffixes configurés / panne upstream | **EXÉCUTÉ** | NXDOMAIN autoritaire pour un nom non déclaré ; SERVFAIL lorsque le forwarder est arrêté |
| Isolation des fichiers natifs | **EXÉCUTÉ** | Empreintes EL8 inchangées ; clés aléatoires supprimées avec le répertoire de test |

La combinaison ACL vide/vide est testée pour le rendu, mais reste refusée par le préflight du rôle : au moins une liste de clients est exigée. La matrice n'assimile pas ce cas à un déploiement autorisé. Les tests de charge et de capacité ne sont pas couverts par ces requêtes fonctionnelles.

## Reproduire les essais

Contrôleur de test : Linux, Python 3.8 avec Jinja2/PyYAML/pytest, Ansible 2.9.27. Laboratoire DNS : root ou capacités nécessaires à `unshare`, `ip`, BIND 9.18, `dig +yaml`, fixtures natives EL8 lisibles. Depuis la racine du projet :

```bash
python -m pytest -q tests
python tests/integration_preflight.py
ansible-playbook -i inventory/dev/hosts.ini playbooks/bind9-deploy.yml --syntax-check
sudo bash tests/run_integration_loopback.sh \
  --bin-dir /usr/bin --named /usr/sbin/named --native-dir /opt/bind-el8-native
```

Le lanceur crée un espace réseau isolé avant d'ajouter les IP de test. Il démarre des démons dans des dossiers temporaires et utilise des ports réservés ; les politiques de vues et les fichiers de zones sont ceux des vrais templates. La rebascule des adresses, ports et chemins est limitée au laboratoire. Les changements de records/serial testés sont temporaires.

## Sécurité et limites précises

La configuration réserve les zones natives, valide les adresses et les éléments interpolés de TSIG, empêche les doublons sans distinction de casse, les ACL /0 et les boucles vers les pairs DNS sur port 53. Les validations BIND finales restent indispensables pour les RDATA, la disponibilité des fichiers et la version installée.

L'ordre `peer`, `local`, `external`, `internal`, `deny` est conservé. Les sources BACK des pairs atteignent la vue peer sans récursion ; les applications locales devant utiliser les forwarders peuvent interroger 127.0.0.1. Le rôle ne modifie pas le choix du résolveur système. Les listes IPv6 de clients ne sont pas admises puisque l'écoute est IPv4.

Le [manuel BIND sur les ACL](https://bind9.readthedocs.io/en/v9.18.0/security.html) décrit les conditions combinées adresse/clé. Le [manuel de référence](https://bind9.readthedocs.io/en/v9.18.21/reference.html#access-control) précise que `allow-notify` ajoute des autorisations à celles des masters : il ne retire pas leur autorisation implicite. La clause imbriquée restreint les autorisations supplémentaires ; elle ne prouve pas que tout NOTIFY non signé du primaire est refusé. Les transferts de données restent authentifiés TSIG.

Les scénarios suivants n'ont pas été exécutés sur votre infrastructure :

| Contrôle | Statut / raison | Procédure, prérequis et résultat attendu | Risque restant |
|---|---|---|---|
| Runtime et installation RHEL 8 / Python 3.6.8 | **NON EXÉCUTÉ** : aucun accès aux cibles | Paire de recette, SSH/sudo, dépôts RHEL. Relever `/usr/bin/python3 --version`, `/usr/sbin/named -V`, `nmcli --version`, puis déployer `--tags deploy` en dev. Attendu : service actif, contrôles sans erreur, python/module DNF disponibles. | Différences du paquet BIND, de Python, SELinux et du système de fichiers |
| Fonctionnement sur les réseaux réels | **NON EXÉCUTÉ** : pas de clients/cibles accessibles | Depuis un client FRONT autorisé, et un BACK si configuré, interroger un A/PTR métier et un nom existant de chaque suffixe forward en UDP puis TCP ; tester une source interdite. Attendu : réponses exactes, refus explicites hors ACL. | Pare-feu, routage, MTU et disponibilité des forwarders |
| AXFR/IXFR et réplication des deux serveurs RHEL | **NON EXÉCUTÉ** sur cibles | Modifier une zone avec serial supérieur, déployer le primaire puis le secondaire ; comparer SOA et record. Contrôler les journaux de transfert/NOTIFY ; tester AXFR FRONT avec clé valide et statut DNS explicite. Attendu : convergence, aucun transfert FRONT. | Version, choix IXFR/AXFR et délai réel de convergence ; l'IXFR spécifique n'est pas prouvé par le seul test de retransfert |
| DNSSEC signé / confiance privée | **NON EXÉCUTÉ** : utilisateur a confirmé des zones privées non signées | Sur une recette distincte disposant d'une chaîne ou ancre de confiance vérifiable, tester une réponse valide et une signature invalide avec validation active. Attendu : réponse validée et SERVFAIL sur donnée invalide. | La racine privée et les ancres nécessitent un design explicite ; aucun support automatique n'est annoncé |
| Idempotence complète et redémarrage | **NON EXÉCUTÉ** sur cibles | Deuxième déploiement identique sur la paire, puis redémarrage planifié ; vérifier zones, serials, routes et service. Attendu : données/serials stables, routes persistantes ; une nouvelle sauvegarde est volontaire. | Comportement du vrai NM, des services et des journaux BIND |
| Rollback et restauration BIND opérationnelle | **NON EXÉCUTÉ** sur cibles | Paire de recette, sauvegarde vérifiée, injecter une panne DNS contrôlée ; vérifier ancienne configuration et routes ajoutées supprimées. Tester aussi restore arrêté/redémarré selon OPERATIONS. Attendu : service précédent opérationnel, routes antérieures intactes. | Sauvegarde à chaud secondaire non atomique, espace disque, privilèges ; panne SSH/arrêt brutal exige reprise explicite |
| SELinux, capacité et charge | **NON EXÉCUTÉ** : pas de RHEL ni charge représentative | Vérifier labels/AVC sur RHEL en enforcing et effectuer une campagne de charge dans les seuils définis par l'exploitant. Attendu : aucune interdiction imprévue et respect des objectifs de service. | Aucun SLO, capacité ou seuil de saturation mesuré ici |
| GitHub Actions | **NON EXÉCUTÉ** : workflow non déclenché à distance | Exécuter le workflow dans le dépôt avec accès GitHub ; attendu : jobs de syntaxe, tests, compilation et couverture réussis. | Les autres couples Ansible/Python de la matrice ne sont pas qualifiés par le seul contrôleur local |

La preuve de syntaxe et de réponses ne remplace pas cette recette opérationnelle. Aucune garantie de « tous les cas possibles » n'est déclarée ; les configurations prises en charge doivent satisfaire les gardes et réussir les contrôles sur la version cible.
