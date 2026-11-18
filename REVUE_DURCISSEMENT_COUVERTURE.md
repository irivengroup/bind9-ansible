# 📦 CONTINUOUS EXECUTION REPORT — Bind9-Ansible

Version : 2026.11.04-coverage-hardening.2 (révision du livrable selon la convention existante).

Statut final : 🔴 NON TERMINÉ — NE PAS DÉPLOYER.

État de l’audit : revue corrective des routes FRONT et des trois Bash, correction
des ACL et instrumentation Ansible partielle. Le chantier des 41 composants
Bash/Ansible reste ouvert. Même périmètre de 56 fichiers : quinze composants
Python/Jinja mesurés au seuil applicable, deux YAML littéraux sans code,
39 composants exécutables sans leurs cinq mesures complètes.

Référence immuable : remediation-2026.11.04-coverage-hardening.1.zip, SHA-256
b8ef36e938957c03f73540219d29137bf643fddf21d04fe018059bb302cb6bff.
Ses correctifs HARD-07 à HARD-13 et QA-01/QA-02 restent intégrés ; son rapport
et ses preuves complets sont conservés dans cette archive, sans les compter
comme de nouveaux correctifs ni reprendre ses mesures comme actuelles.

## 📊 Qualité

### Vulnérabilités et défauts

Identifiants locaux ; aucune obligation réglementaire inventée. Confiance
élevée pour les défauts reproduits. Les préconditions ci-dessous portent sur
des entrées administratives ou observations locales ; aucune exploitation
distante supposée. Extraits sans secret.

| ID / sévérité | Cause, précondition et impact | Correction / preuve / état |
| --- | --- | --- |
| HARD-14 — majeure, routage | Propriétés de passerelle NM absentes/inexactes malgré un prochain saut valide sur FRONT. Échec du rôle ; route get d’une IP locale ne fournit pas ce prochain saut. | Interface portant l’IP vérifiée par routes candidates et LPM numérique, UUID actif, passerelle unique lue uniquement dans les routes main de ce device. Replis profil/global retirés. Sept échecs initiaux dans before-front-kernel-gateway.xml ; quatre dans before-front-lpm.xml ; suite complète et laboratoire natif réussis après. Corrigé. |
| HARD-15 — majeure, archives | Catalogues TAR arrêtés aux premiers blocs zéro ; entrées cachées et fichier creux de plus d’un Gio admis malgré une archive minuscule avec somme valide. Risques de chemins/types/doublons cachés et épuisement de ressources. | --ignore-zeros sur catalogues/extraction, tailles/nombre/temps bornés. before-bash-boundaries.xml et before-archive-resource-bounds.xml reproduisent les défauts ; refus après correction. Répertoires et fichiers vides restent admis. Corrigé. |
| HARD-16 — modérée, santé | aa trouvé hors champ flags ou NOERRORISH interprété comme réponse saine ; faux positif sur sortie dig inattendue. | aa ancré au champ flags, statut NOERROR délimité, locale C. Trois négatifs et un positif, reproduction avant/après conservée. Corrigé. |
| HARD-17 — modérée, ACL | Tests d’extrémités is_link_local/is_multicast insuffisants pour un CIDR contenant une plage réservée ; broadcast limité admis. Autorisation trop large. | Refus du chevauchement avec 0.0.0.0/32, 169.254/16, 224/4, 255.255.255.255/32. Huit échecs/huit positifs avant, seize réussites après. Loopback reste autorisé en ACL, distinct du contrat des routes. Corrigé. |
| HARD-18 — majeure, reprise | Après retrait du dernier via possédé, un ancien journal légitime pouvait refuser sa clôture en exigeant encore la passerelle inférée. | Clôture après restauration exacte des deux magasins/adresses/règles/UUID, sans exiger le prochain saut qui vient de disparaître. Contrôles avant suppression inchangés. Un échec initial, 48 tests réussis après ; vrai ancien journal repris dans NM natif. Corrigé. |
| QA-03 — majeure, mesure Ansible | Les prédicats ne prouvent pas le dispatch, rescue, boucles et données templatisées. | Inventaire natif des 38 YAML, empreintes/positions vérifiées, observation Conditional native. Observations partielles séparées ; aucun hit admis comme couverture YAML complète. Ouvert. |
| QA-04 — majeure, mesure Bash | Prototype C GNU Bash : 80 comparaisons égales, mais trace sous ulimit -f 0 provoquant SIGXFSZ et changeant le programme. | Refus conservé dans native-filelimit-refusal.json, prototype exclu des sources livrées et des mesures. Transport et ensemble de la grammaire restent à établir. Ouvert. |

Outils/règles : analyse des entrées et journaux, non-régressions sur sources
canoniques, DataLoader/Conditional natifs, Pyflakes 3.2.0, Bandit 1.7.10,
ShellCheck épinglé. Bandit : zéro alerte ; exclusions antérieures visibles
dans le reçu. Aucun nouveau contournement de test, seuil ou analyseur.

### Architecture et conception

Un seul moteur FRONT : bind-front-routes.sh orchestré par front_return_routes.yml.
Aucun legacy, deuxième moteur, IP modèle distante ou dépendance de routage
Python ajouté. IP nue et /32 sont normalisés et dédoublonnés ; CIDR agrégés
sans élargir leur union ; répétition sans changement ; conflits refusés.

Le nom du profil peut différer de l’interface : écritures par UUID actif.
L’interface portant FRONT est confirmée par ip route show match, plus long
préfixe numérique puis métrique ; deux candidats de même rang sont refusés.
Le prochain saut provient seulement des routes main de ce device, y compris
routes spécifiques/DHCP ; ni ipv4.gateway, ni IP4.GATEWAY, ni défaut BACK/ADM.
Plusieurs prochains sauts distincts sont refusés avant écriture. Un client
directement connecté n’exige pas de passerelle ; sans aucune route via sur
FRONT, deux IP ne permettent pas d’inventer un prochain saut.

La pièce jointe est une référence fonctionnelle, corrigée pour éviter le tri
lexical, l’assimilation device/profil et trois recherches de sous-chaînes
indépendantes. La validation porte sur le tuple exact. La cible reste RHEL/NM ;
aucune branche Netplan inerte. L’ajout persistant NM et ip route add ciblé
préservent les paramètres en attente. device reapply pourrait appliquer aussi
les autres modifications du profil ([manuel NM](https://networkmanager.dev/docs/api/latest/nmcli.html)).
Le laboratoire prouve la préservation du DNS en attente ; la solution n’est
donc pas une copie littérale du playbook fourni. Aucun ping ajouté.

Flock, journal durable et possession exacte des ajouts restent nécessaires au
rollback. Une dérive étrangère conserve le journal avec erreur, sans retrait
hasardeux. Le vrai journal de l’ancien helper est récupéré en laboratoire,
avec restauration exacte du noyau et du profil.

| Domaine d’impact | Invariant / évolution |
| --- | --- |
| Contrats | Variables, actions CLI, rc 0/2/1 et journal huit champs conservés ; source de passerelle explicitement remplacée. |
| Sécurité | ADM/BACK/SSH/règles protégés ; ambiguïtés et observations incohérentes refusées. |
| Données et reprise | Ancien journal éprouvé ; pas d’atomicité garantie contre un outil root concurrent non coopératif. |
| Exploitation | Un prochain saut déjà présent sur FRONT est requis pour une destination distante ; aucun réglage implicite global. |
| Archives | Budget logique 1 Gio, 65 536 membres, chemin 4 096 octets, catalogues 16 Mio ; flux physique/compressé 1 140 860 928 octets ; lecture 60 s + arrêt forcé 5 s. Dépassement explicitement refusé. |
| ACL/santé | Chevauchements réservés et faux champs dig refusés ; cas positifs conservés. |
| Compatibilité | Python 3.6.8/Ansible 2.9.27/NM 1.40.16 en EL8 isolé ; QA sur Python 3.11/Core 2.16.14/Jinja 3.1.6, sans paquet cible ajouté. |
| CI/CD | Nouvelles suites incluses par le workflow existant ; cinq seuils bloquants conservés ; aucune publication. |
| Documentation | README/rapport courants actualisés ; fiches docs/ de phases précédentes conservées comme historiques, pas de reconstruction du Wiki. |

### Conformité, fiabilité et angles morts

Couverture, réussite des tests et aptitude à la production restent distinctes.
Les booléens court-circuités ne sont jamais crédités par déduction des entrées.
Les 41 composants initiaux restent inclus : trois Bash, 36 YAML exécutables,
deux YAML littéraux defaults/meta. Ces derniers ont zéro unité exécutable
vérifiée par parseur, NON APPLICABLE sans leur attribuer 100 %.

L’observateur Ansible inventorie 244 unités task/block/play/role et 309
expressions conditionnelles non constantes. Mesure partielle : 22 prédicats,
82 issues élémentaires sur 858 inventoriées ; ni dispatch, rescue, boucles ni
données templatisées mesurés intégralement. Reçus inconnus/altérés ou prétendus
complets refusés. Un résultat partiel n’entre pas dans les cinq métriques YAML.

La première paire limitée a été refusée pour un Python non observé. La paire
complète garde les sélecteurs précédents, dédoublonne leurs fichiers et ajoute
les régressions : 1 468 tests par phase, sans skip/échec. Refus initiaux conservés.

| Contrôle | Résultat | Statut |
| --- | --- | --- |
| Suite complète | 2 044 identités distinctes sur quatre lots disjoints : 2 035 réussites, zéro échec/erreur, neuf skips de montage. | EXÉCUTÉ |
| Paire native/instrumentée | 1 468/1 468 par phase ; sources et outils vérifiés, 56 empreintes production. | EXÉCUTÉ |
| Gate cinq métriques | Retour 2, accepted=false ; 39 composants exécutables incomplets. | ÉCHOUÉ |
| Compatibilité EL8 | 452 tests ; deux syntaxes Ansible ; préflight trois inventaires/12 hôtes, faits cible simulés. | EXÉCUTÉ |
| NM et noyau natifs | 14 parcours, LPM /24 contre BACK /8, via FRONT sans défaut, DNS en attente intact, ancien journal repris. | EXÉCUTÉ |
| BIND EL8 natif | 109 configurations, 345 vérifications DNS, fichiers natifs inchangés, IPv6 et refus AXFR. | EXÉCUTÉ |
| Sauvegarde alternative | Neuf corps/assertions historiques réussis en racine jetable sans montages imbriqués ; systemd/SELinux doublés. | EXÉCUTÉ |
| Pyflakes / Bandit / ShellCheck | Zéro erreur Python/QA, zéro alerte Bandit, sept scripts sans ajout de suppression. | EXÉCUTÉ |
| Charge externe | Aucun nouveau protocole/service ou benchmark externe demandé. | NON APPLICABLE |

### Cinq couvertures par support

Mesures Linux courantes ; aucun moteur Bash/Ansible Windows exécuté. Le reçu
externe atteste l’identité de l’extraction et son SHA. Fractions exactes et
décimales dans COUVERTURE.json ; seuil avant arrondi. Un 0/0 n’est pas 100 %.

| Métrique | Windows — archive extraite | Linux — sources identiques : Python | Linux — sources identiques : Jinja | Global 56 composants |
| --- | --- | --- | --- | --- |
| statements | NON EXÉCUTÉ | 2160/2167 (99.67697277341947392708814028610982925704 %) | 233/233 | NON EXÉCUTÉ |
| branches | NON EXÉCUTÉ | 1247/1257 (99.20445505171042163882259347653142402546 %) | 58/58 | NON EXÉCUTÉ |
| functions | NON EXÉCUTÉ | 148/148 (100 %) | NON APPLICABLE (0/0) | NON EXÉCUTÉ |
| lines | NON EXÉCUTÉ | 2240/2247 (99.68847352024922118380062305295950155763 %) | 264/264 | NON EXÉCUTÉ |
| conditions | NON EXÉCUTÉ | 1784/1812 (98.45474613686534216335540838852097130243 %) | 36/36 | NON EXÉCUTÉ |

### Détail des 56 composants

NE = NON EXÉCUTÉ ; NA = NON APPLICABLE, dénominateur nul. Chemins depuis la racine.

| Composant | Windows — archive extraite | Linux — sources identiques S | B | F | L | C |
| --- | --- | --- | --- | --- | --- | --- |
| `roles/bind9/defaults/main.yml` | NE | NA (0/0) | NA (0/0) | NA (0/0) | NA (0/0) | NA (0/0) |
| `roles/bind9/files/bind-configtool.sh` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/files/bind-front-routes.sh` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/files/bind-private-dnssec.py` | NE | 148/149 | 55/56 | 12/12 | 154/155 | 89/90 |
| `roles/bind9/files/named-healthcheck.sh` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/files/reverse-serial.py` | NE | 175/175 | 98/98 | 15/15 | 181/181 | 126/126 |
| `roles/bind9/files/tsig-secret.py` | NE | 110/110 | 32/32 | 8/8 | 118/118 | 51/52 |
| `roles/bind9/filter_plugins/client_acl.py` | NE | 34/34 | 15/15 | 4/4 | 37/37 | 30/30 |
| `roles/bind9/filter_plugins/dns_topology.py` | NE | 96/97 | 81/82 | 10/10 | 106/107 | 109/110 |
| `roles/bind9/filter_plugins/reverse_dns.py` | NE | 52/52 | 35/35 | 3/3 | 52/52 | 42/42 |
| `roles/bind9/filter_plugins/soa_health.py` | NE | 27/27 | 16/16 | 4/4 | 30/30 | 32/32 |
| `roles/bind9/handlers/main.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/library/bind9_manage.py` | NE | 27/27 | 12/12 | 1/1 | 30/30 | 6/6 |
| `roles/bind9/meta/main.yml` | NE | NA (0/0) | NA (0/0) | NA (0/0) | NA (0/0) | NA (0/0) |
| `roles/bind9/module_utils/bind9_management.py` | NE | 1491/1496 | 903/911 | 91/91 | 1532/1537 | 1299/1324 |
| `roles/bind9/tasks/main.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/manage.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/clients.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/deploy/configuration.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/deploy/directories.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/deploy/firewall.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/deploy/front_return_routes.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/deploy/guard.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/deploy/health.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/deploy/hibernate.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/deploy/lease_release.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/deploy/main.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/deploy/migration.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/deploy/monitoring.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/deploy/packages.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/deploy/primary_readiness.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/deploy/seal.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/deploy/security.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/deploy/service.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/deploy/tsig.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/deploy/validate.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/deploy/verify.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/deploy/zones.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/dnssec_activate.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/dnssec_prepare.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/dnssec_settings.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/manage/main.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/named_runtime.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/native_loopback.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/path_safety.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/runtime_restore.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/runtime_snapshot.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/tasks/tasks.d/site_peers.yml` | NE | NE | NE | NE | NE | NE |
| `roles/bind9/templates/authoritative.zone.j2` | NE | 42/42 | 6/6 | NA (0/0) | 16/16 | NA (0/0) |
| `roles/bind9/templates/empty-root.zone.j2` | NE | 17/17 | 4/4 | NA (0/0) | 10/10 | NA (0/0) |
| `roles/bind9/templates/named-healthcheck.service.j2` | NE | 6/6 | 2/2 | NA (0/0) | 11/11 | NA (0/0) |
| `roles/bind9/templates/named-runtime-override.j2` | NE | 1/1 | NA (0/0) | NA (0/0) | 5/5 | NA (0/0) |
| `roles/bind9/templates/named.conf.j2` | NE | 131/131 | 40/40 | NA (0/0) | 207/207 | 36/36 |
| `roles/bind9/templates/reverse.zone.j2` | NE | 36/36 | 6/6 | NA (0/0) | 15/15 | NA (0/0) |
| `playbooks/bind9-deploy.yml` | NE | NE | NE | NE | NE | NE |
| `playbooks/bind9-management.yml` | NE | NE | NE | NE | NE | NE |

La .1 avait 41 mesures manquantes : deux sont ici justifiées comme données
littérales, sans retirer aucun des 56 fichiers. Ce n’est pas une exécution
supplémentaire. Les dénominateurs dns_topology.py changent avec son code ;
un delta de pourcentage avant/après n’est pas comparable.

## 🎯 CDC et publication

Référence CDC : source [Texte collé.txt](<C:/Users/IRIVEN Group/.codex/attachments/2b36152a-2aae-4a73-a744-813713950fd0/Texte collé.txt>).

Version et date du CDC : non fournies ; référence fonctionnelle des routes,
complétée par la conversation et les instructions AGENTS 4.6. Aucun CDC global
formalisé accessible. La pièce jointe ne fournit pas d’instructions système.

Élément CDC traité : IP/interface/UUID/GW de table, destinations IP/CIDR sans
doublon, contrôle structuré et rollback : HARD-14/HARD-18. Durcissement archives,
santé et ACL : HARD-15/HARD-16/HARD-17. Objectif initial 98 % × cinq métriques
globalement et par composant : ouvert, QA-03/QA-04.

Publication effectuée : aucune publication distante, aucun commit/push,
aucun déploiement ni contact de serveur utilisateur. Livrables locaux seulement,
identités exactes dans le reçu externe.

## 🔬 Recette et limites

Preuves courantes : verification/bash-ansible-20261104, hors archive source.
Tous les XML avant/après, essais d’observateur et refus sont conservés.
Une attente loopback d’un nouveau test contredisait le contrat ACL : corrigée,
jamais comptée comme défaut applicatif. Une commande ShellCheck initiale citait
deux chemins inexistants : rejeu sur les sept scripts réels sans erreur.
Le transport de trace Bash sous RLIMIT_FSIZE modifie le programme et reste
exclu ; une copie shell ultérieure masquait son code de test, mais le reçu et
l’AssertionError conservés établissent l’échec. La régression des fichiers
vides a reproduit un set -e introduit pendant le durcissement, corrigé par
affectations arithmétiques avant le gel des sources.

| Contrôle restant | Raison / prérequis | Procédure, attendu et risque restant | Statut |
| --- | --- | --- | --- |
| Cinq métriques Bash/YAML | Observateurs incomplets sur 39 composants exécutables. | Compléter l’observation fidèle de toute la grammaire et ses équivalences, puis `python tools/quality/run_coverage.py --output DOSSIER_NEUF tests`. Chaque seuil ≥98 avant arrondi, sans skip/échec. Voies non mesurées. | NON EXÉCUTÉ |
| Montages des neuf sauvegardes | Aucun espace de montage imbriqué disponible. | Linux jetable avec mount : `BIND_TEST_REQUIRE_MOUNT_NS=1 python -m pytest -q tests/test_configtool_workflows.py`. Neuf réussites attendues ; le rejeu alternatif ne prouve pas les montages. | NON EXÉCUTÉ |
| Matrice CI distante | Dépôt distant/exécution non fournis. | Exécuter .github/workflows/ci.yml, toutes combinaisons sans contournement, conserver les résultats. Risque de versions non éprouvées. | NON EXÉCUTÉ |
| Paire RHEL, SELinux, reboot | Aucun accès aux hôtes utilisateur. | Paire isolée représentative : procédures validation/reprise du README, deploy, DNS/ACL, perte de contrôleur, rollback, reboot. Attendu : données/services/routes et ADM/BACK intacts ; risques système et de panne. | NON EXÉCUTÉ |
| Reconstruction Wiki | Aucun nouveau cycle documentaire demandé pour ce correctif ciblé. | Fiches des phases précédentes conservées ; README/présent rapport décrivent FRONT actuel. Aucune migration/publication annoncée. | NON APPLICABLE |

EL8 isolé n’est pas une installation système RHEL. Persistance éprouvée par
rechargement réel de profils, sans reboot serveur. Aucun RPO/RTO ni gain de
performance nouveau garanti.

## 💻 Code source et livrables

Sources canoniques : bind9-ansible-v4. Quatre composants de production modifiés :
bind-front-routes.sh, bind-configtool.sh, named-healthcheck.sh, dns_topology.py.
Tests, chargeur natif et outils QA intégrés ; aucun paquet d’exécution cible ajouté.
DELIVERY.json contient la liste exacte des fichiers modifiés et leurs sources ;
SOURCE-MANIFEST.sha256 couvre tous les fichiers sauf lui-même.

Livrables locaux : remediation-2026.11.04-coverage-hardening.2.zip, patch depuis
la .1, empreinte et reçu externe. Ce dernier rattache reconstruction déterministe,
extraction isolée et contrôles rejoués au SHA-256 exact. Le ZIP exclut preuves,
prototype GNU modifié, caches, journaux et environnements installés. Les sources
complètes sont incluses ; aucune copie legacy ou moteur de routage parallèle.
L’ancienne archive et les preuves initiales restent immuables.
