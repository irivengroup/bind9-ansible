# 📦 CONTINUOUS EXECUTION REPORT — Bind9-Ansible

Version : 2026.11.03-project-review.1 ; source observée : 2026.10.26-front-hardening.1 dans DELIVERY.json.

Statut final : 🔴 NON TERMINÉ — NE PAS DÉPLOYER.

État de l’audit : vérification de l’ensemble des sources accessibles et correction
de quatre constats confirmés. La recette des serveurs RHEL de l’utilisateur et
les cinq couvertures indépendantes à 98 % ne sont pas démontrées. Ce rapport
décrit les preuves disponibles avant construction ; le reçu externe du rejeu
est lié au SHA-256 exact de l’archive et complète ces résultats.

Source de départ : `bind9-ansible-v4-durci.zip`, SHA-256
`060e361fcf459802f71a7d799f8d04fefd8943507e823eccb9ebc2111b6de75d`.
Les 214 fichiers initiaux ont été comparés octet par octet au dossier de travail.
Le mécanisme de révision datée du projet est conservé ; aucune version SemVer
antérieure n’a été déduite du nom du dossier.

## 📊 Qualité

### Vulnérabilités et défauts critiques

Les identifiants REV-01 à REV-04 sont proposés pour cette passe. Aucun secret
réel n’est reproduit dans les extraits.

| Constat | Sévérité, cause et préconditions | Impact | Correction et preuve |
| --- | --- | --- | --- |
| REV-01 — échec partiel masqué | Majeure ; le dernier `fail` du rescue avait `tags: [deploy]`. Avec `configure`, `zones`, `records`, `services` ou `validate`, seul le bookkeeping du rescue pouvait réussir. Confiance élevée : comportement reproduit avec Ansible réel. | Code de sortie nul après un échec ; une automatisation pouvait poursuivre alors que les changements partiels demeuraient. | `roles/bind9/tasks/tasks.d/deploy/main.yml` : échec final marqué `always`, diagnostic distinct pour un parcours sans restauration sélectionnée. Cinq tests exécutent le vrai rescue. Le rollback des tags partiels n’est pas inventé ni élargi. |
| REV-02 — mauvais propriétaire de réponse loopback | Modérée ; `named-healthcheck.sh` recherchait le type et les données sans vérifier le nom demandé. Une réponse A/PTR portant un autre nom était acceptée. Confiance élevée : reproduction par le vrai script. | Faux état sain, notamment sans zone passée au moniteur ; un contrôle SOA ultérieur ne peut pas servir de garantie implicite. | Comparaison exacte du propriétaire, de la classe et du type avant comparaison des données. Deux refus A/PTR et un cas nominal sans zone. |
| REV-03 — réponse DNS de management incomplète | Modérée ; `DnsReply` acceptait `len(flags) <= 1` et ne vérifiait ANSWER que si le compteur existait. Des lignes ignorées ou plusieurs réponses pouvaient être acceptées. Précondition : sortie native incomplète ou incohérente. Confiance élevée. | Faux succès d’un contrôle après rechargement ; absence de déclenchement de la reprise attendue. Aucune exploitation distante de dig n’est déduite de ce constat. | `roles/bind9/module_utils/bind9_management.py` : texte borné sans NUL, en-tête unique, compteur obligatoire, une réponse et aucune ligne de données ignorée. Les réponses de forwarders sans AA restent acceptées ; les SOA locales exigent toujours AA et le serial attendu. |
| REV-04 — lien vers adaptateur supprimé | Mineure ; un rapport historique annonçait encore une compatibilité par `front_routes_legacy.yml`, fichier absent. Confiance élevée : résolution du lien échouée. | Documentation contradictoire avec le nettoyage autorisé. | Mention du retrait et lien vers le rapport de nettoyage dans `docs/DURCISSEMENT_SECURITE.md`. Aucune réintroduction de l’ancien code. |

Les modifications de fixtures de management ajoutent le véritable en-tête
`;; flags: qr aa; QUERY: 1, ANSWER: 1` aux réponses nominales. Elles ne changent
aucune assertion ni aucun cas de refus. Les sorties réelles de BIND 9.11 ont
ensuite exercé le parseur corrigé : douze transactions ont réussi, y compris
rechargement, lots, refus d’un forwarder indisponible et reprise conjointe.

La suite dédiée compte 20 cas : avant correction, **14 échecs et 6 réussites** ;
après correction, **20 réussites**, sans cas ignoré. Les reçus initiaux sont
conservés séparément et ne sont pas remplacés par ceux de la candidate.

### Architecture et conception

Le rôle conserve ses points d’entrée, groupes d’inventaire, variables publiques
et outils natifs. Les trois fichiers runtime corrigés sont le moniteur shell,
le parseur de santé de la classe `DnsReply` et le rescue YAML. Aucun nouveau
service, paquet cible, moteur de routage ou format de journal n’est introduit.
La logique objet du management et ses protections de publication sont conservées.

Le moteur FRONT est identique à la livraison de départ : IP seule en /32,
sous-réseaux et listes mixtes restent pris en charge. Aucune validation ping ni
réactivation de connexion NetworkManager n’est ajoutée. Les protections
ADM/BACK, la passerelle propre à FRONT, l’idempotence et la reprise ont été
rejouées dans quatorze parcours NetworkManager 1.40.16.

Les verrous sont coopératifs. Ils ne garantissent pas l’atomicité face à un
administrateur root ou à un outil externe modifiant simultanément le réseau.
Une fenêtre exclusive reste nécessaire pour le déploiement complet. Les tags
partiels conservent leurs limites documentées de sauvegarde et de reprise.

### Conformité, outils et angles morts

| Pilier examiné | Éléments et preuve | Statut et limite |
| --- | --- | --- |
| Sécurité et entrées/sorties | Chemins privilégiés, TSIG, limites des commandes, archives, journaux et réponses DNS ; Bandit 1.7.10 sur les neuf fichiers Python de production et ShellCheck épinglé sur les sept scripts. | EXÉCUTÉ ; aucune alerte de ces outils. Ce n’est pas une analyse complète des dépendances ni une certification. |
| Architecture et concurrence | Entrées deploy/manage, parseurs, écritures conditionnelles, verrous, ownership et cas de reprise. | EXÉCUTÉ ; revue et tests ciblés, sans preuve générale d’absence de race avec des acteurs non coopératifs. |
| État et résilience | Reprise DNS/FRONT, sauvegarde/restauration, erreurs natives et propagation des échecs. | EXÉCUTÉ en laboratoire ; arrêt brutal d’un serveur réel et reboot RHEL non exécutés. |
| Conformité et testabilité | YAML strict, syntaxe, liens locaux, compatibilité et tests avant/après. | EXÉCUTÉ pour les contrôles indiqués ; aucun référentiel réglementaire fourni. Les cinq couvertures restent à établir. |

| Contrôle | Résultat à la construction | Statut |
| --- | --- | --- |
| Identité source | 214 fichiers identiques à la livraison de départ, archive d’origine préservée | EXÉCUTÉ |
| Régressions dédiées | 20/20 après correction, 14 refus manquants reproduits avant | EXÉCUTÉ |
| Composants sur Python 3.11.15 / Ansible Core 2.16.14 | 597 réussites, zéro échec, zéro ignoré | EXÉCUTÉ |
| DNS natif EL8 | 109 configurations chargées, 345 contrôles DNS, fichiers natifs inchangés, transfert sauvage refusé, transport sortant IPv6 | EXÉCUTÉ |
| Management natif avec DNSSEC auto | 12 transactions, archives réelles, reprise après forwarder indisponible, lots et catalogues | EXÉCUTÉ |
| Routes natives | 14 parcours NetworkManager, défaut BACK et passerelle FRONT distincts | EXÉCUTÉ |
| Sauvegarde/restauration | 9 scénarios existants exécutés dans une racine de conteneur jetable ; systemd, BIND-checker et SELinux doublés dans ces seuls scénarios | EXÉCUTÉ |
| Journalisation native | 3 fichiers, démon non privilégié, rotation à 10 Mio, 5 versions, 3 contrôles DNS ; modes observés 0644/0640/0640 sous répertoire protégé | EXÉCUTÉ |
| Préflight inventaires | 3 inventaires et 12 hôtes, assertions réelles, faits RHEL simulés | EXÉCUTÉ |
| Python et YAML | 78 sources Python compilées/analysées, 69 YAML sans clé dupliquée | EXÉCUTÉ |
| Shell | 7 syntaxes Bash et 7 sources ShellCheck sans nouvelle exclusion | EXÉCUTÉ |
| Bandit | 0 alerte ; trois suppressions ciblées préexistantes sur les appels de processus contrôlés, non modifiées | EXÉCUTÉ |
| Liens documentaires locaux | Un lien cassé reproduit, corrigé, puis aucun lien local cassé détecté | EXÉCUTÉ |
| Suite complète et couvertures de l’extraction livrée | Résultat à consulter dans le reçu externe lié au SHA-256 du ZIP | NON EXÉCUTÉ au moment de la construction |
| Qualification de vos serveurs et matrice CI entière | Aucun serveur contacté et aucune exécution CI distante | NON EXÉCUTÉ |

Les avertissements de dépréciation Python/Jinja/Ansible ont été conservés. Les
versions historiques n’ont pas été mises à niveau implicitement. Bandit ne
couvre pas les playbooks ni les bibliothèques système RHEL ; aucune base de CVE
ni campagne DAST distante n’a été exécutée. Aucun identifiant CWE/CVSS n’est
inventé pour qualifier les défauts fonctionnels constatés.

**Seuil : au moins 98 % séparément sur chaque métrique, globalement et par
composant.** Le nombre de tests et les recettes natives ne constituent pas une
mesure de couverture.

| Couverture | Windows — archive extraite | Linux — sources identiques |
| --- | --- | --- |
| Instructions | NON APPLICABLE à l’exécution Windows : rôle RHEL/Linux | NON EXÉCUTÉ à la construction ; mesure du rejeu dans le reçu externe |
| Branches | NON APPLICABLE à l’exécution Windows : rôle RHEL/Linux | NON EXÉCUTÉ à la construction ; mesure du rejeu dans le reçu externe |
| Fonctions | NON APPLICABLE à l’exécution Windows : rôle RHEL/Linux | NON EXÉCUTÉ : instrumentation absente |
| Lignes | NON APPLICABLE à l’exécution Windows : rôle RHEL/Linux | NON EXÉCUTÉ à la construction ; mesure du rejeu dans le reçu externe |
| Conditions booléennes | NON APPLICABLE à l’exécution Windows : rôle RHEL/Linux | NON EXÉCUTÉ : instrumentation absente |

Le rejeu doit détailler les neuf composants Python, y compris ceux non chargés.
Coverage.py mesure les lignes exécutables/instructions et les branches ; ni le
nombre de fonctions appelées ni les résultats vrai/faux de chaque condition
élémentaire ne sont déduits de ces métriques. Bash et YAML/Jinja ne disposent
pas ici d’instrumentation de couverture : leurs cinq mesures requises restent
NON EXÉCUTÉES. La CI conserve ses seuils de 98 % existants et inclut les nouveaux
cas dans le contrôle ciblé de management ; elle ne démontre pas les cinq seuils.

## 🎯 CDC et publication

Référence CDC : source non fournie sous forme de fichier accessible. Demande
utilisateur : « faire une passe de verification/correction du projet complet ».

Version et date du CDC : non fournies ; consignes AGENTS.md communiquées dans la
conversation, version 4.6, sans fichier CDC supplémentaire identifié.

Élément CDC traité : vérification globale des sources, correction des défauts
REV-01 à REV-04, maintien des routes IP/sous-réseaux, absence de ping et retrait
conservé du code FRONT historique. États et preuves : matrice des constats et
reçus avant/après ; limites de production et de couverture ouvertes.

Publication effectuée : aucune publication effectuée. Préparation de fichiers
locaux et d’une archive source de développement ; aucun déploiement, envoi de
message, commit, push ou changement des serveurs de l’utilisateur.

## 🔬 Recette et limites

Depuis une extraction Linux isolée, avec Python/Ansible/Jinja compatibles et les
outils natifs EL8, exécuter les contrôles du workflow `.github/workflows/ci.yml`.
Pour la suite complète et les mesures natives disponibles :

```bash
export ANSIBLE_CONFIG="$PWD/ansible.cfg"
export ANSIBLE_ROLES_PATH="$PWD/roles"
export PYTHONDONTWRITEBYTECODE=1
python3 -m coverage run --branch \
  --source=roles/bind9/filter_plugins,roles/bind9/module_utils,roles/bind9/files,roles/bind9/library \
  -m pytest -p no:cacheprovider --basetemp=/tmp/bind9-review -q tests
python3 -m coverage report --fail-under=98
python3 -m coverage json -o /tmp/bind9-coverage.json
python3 tests/integration_preflight.py
```

La suite se répartit en groupes de fichiers indépendants avec un état temporaire
et un conteneur privés par groupe ; les données de couverture se combinent
uniquement lorsque leurs empreintes de sources sont identiques. Les neuf tests
exigeant un espace de montage peuvent être ignorés par pytest faute de capacité
locale ; leur adaptateur existant `tests/integration_backup_container.py` rejoue
les mêmes corps et assertions dans un conteneur jetable, sans supprimer ces tests.

Les lanceurs natifs et leurs prérequis figurent dans README.md. Ne pas exécuter
les tests privilégiés sur un serveur DNS de production. Les laboratoires locaux
ont été coupés du réseau externe ; seuls les tests de réseau en conteneur ont
reçu NET_ADMIN dans leur propre espace réseau, sans SYS_ADMIN ni assouplissement
seccomp. Les sources y sont montées en lecture seule.

| Contrôle restant | Raison et prérequis | Procédure et résultat attendu | Risque restant |
| --- | --- | --- | --- |
| Cinq couvertures, globales et par composant | Instrumentation fonctions/conditions et Bash/YAML absente ; compteurs à établir | Exécuter les rapports natifs, compléter par des moteurs compatibles observant réellement chaque condition, puis refuser toute métrique absente ou brute inférieure à 98 % | Chemins non exercés ; aucune aptitude production démontrée |
| Matrice CI complète | Seules deux combinaisons locales exercées | Exécuter chaque entrée de `.github/workflows/ci.yml` dans le dépôt de recette ; conserver tous les reçus, sans tests ignorés non justifiés | Écarts sur autres contrôleurs |
| Déploiement, reprise et reboot RHEL | Aucun accès aux serveurs de l’utilisateur ; paire de recette, ADM de secours et inventaire réel nécessaires | Suivre les procédures de README.md : déploiement complet, DNSSEC, transferts/refus, firewalld/SELinux enforcing, erreur contrôlée et reboot, un hôte à la fois | État système, droits et persistance réels non qualifiés |
| Dépendances et politiques réelles | Pas de base SCA/CVE ni données de maintenance de l’organisation | Analyser les paquets RHEL et dépendances contrôleur avec les référentiels à jour de l’organisation | Vulnérabilités tierces non évaluées |

Les procédures détaillées existantes, résultats historiques et preuves de refus
restent conservés. L’essai exploratoire de l’ancienne version a été interrompu
après 362 réussites et 9 ignorés pour consacrer les ressources au rejeu complet
de l’artefact ; il n’est pas présenté comme une campagne complète réussie.

Retour arrière local : utiliser l’archive de départ intacte après vérification
de son SHA-256 ci-dessus. Cela restaure le code, pas un état DNS/réseau distant.
Pour un serveur déjà modifié, suivre la procédure de sauvegarde/reprise existante
et préserver tout journal en attente ; aucune restauration de serveur n’a été
exécutée dans cette passe.

Références primaires : [gestion des erreurs Ansible 2.9](https://docs.ansible.com/projects/ansible/2.9/user_guide/playbooks_blocks.html)
et [sélection par tags Ansible 2.9](https://docs.ansible.com/projects/ansible/2.9/user_guide/playbooks_tags.html).
La documentation officielle confirme qu’un rescue réussi rétablit le statut de
succès ; la preuve déterminante de REV-01 reste l’exécution avant/après.

## 💻 Code source et livrables

L’archive contient le projet complet, ses tests et ses manifestes. Les fichiers
modifiés et empreintes sont recensés dans DELIVERY.json et SOURCE-MANIFEST.sha256.
Le reçu externe donne le SHA-256 du ZIP, l’identité extraction/contrôle, les
résultats du rejeu et les mesures par composant. Les caches, journaux bruts de
laboratoire, outils temporaires et dépendances réinstallables sont exclus.

### 🧪 tests/test_project_review_regressions.py

Périmètre : identité A/PTR sans zone, nominal sans zone, sorties DNS complètes et
corrompues, limites de taille et cinq tags partiels. 20 cas exécutés après
correction. Statut de couverture : 🔴 EN COURS — les cinq mesures indépendantes
à 98 % ne sont pas démontrées. La suite complète est livrée, sans assertion
affaiblie ; les quatre fixtures nominales de management portent désormais le
compteur ANSWER réellement émis par dig.
