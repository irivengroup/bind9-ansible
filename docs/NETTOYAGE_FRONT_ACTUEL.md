# Moteur FRONT actuel uniquement

Révision : `2026.10.26-front-current-only.1`.

Rapport historique du nettoyage. La révision de durcissement suivante conserve ce retrait et possède ses propres preuves dans [DURCISSEMENT_FRONT_ACTUEL.md](DURCISSEMENT_FRONT_ACTUEL.md).

La suppression de la compatibilité des anciens journaux FRONT a été explicitement demandée après confirmation qu'elle n'était plus nécessaire. La source est l'archive corrigée `bind9-ansible-v4-front-corrige.zip`, conservée intacte. Cette révision conserve la correction de passerelle FRONT et la prise en charge des IP seules, sous-réseaux et listes mixtes.

## Suppressions intégrées

- Suppression de `roles/bind9/tasks/tasks.d/deploy/front_routes_legacy.yml`.
- Suppression de sa découverte et de son inclusion conditionnelle dans `front_return_routes.yml`. Toutes les actions passent directement par le moteur actuel.
- Suppression des actions internes `legacy-rollback`/`legacy-cleanup`, des options `--legacy-*`, des tableaux, empreintes et identités associés dans `bind-front-routes.sh`.
- Suppression de l'ancien chemin de journal dans le garde de publication DNS ; seul le journal FRONT actuel bloque une nouvelle écriture.
- Suppression des alias de tests qui reconstruisaient les anciens auxiliaires YAML et de la fabrication de journaux historiques dans les fixtures.

Les tests de sécurité applicables ont été portés sur de vraies transactions du moteur courant : vérification des magasins, dérive de métrique/attributs, corruption de journal, lien physique, preuve opérateur, exclusion concurrente et reprise explicite. Les cas propres aux anciennes conversions de listes YAML/JSON ont été retirés avec le code qu'ils contrôlaient. Les tests qui restent n'utilisent plus l'ancien adaptateur comme source de validation.

## Contrat courant et impact

| Domaine | Comportement conservé ou suppression demandée |
|---|---|
| Actions FRONT | `plan`, `apply`, `verify`, `commit`, `rollback`, `recover` uniquement. Les anciennes actions sont refusées avant création de l'état privé. |
| Journal | `/var/lib/Bind9-Ansible/front-routes.pending/`, format courant inchangé. `routes-recover` reprend uniquement ce format. |
| Compatibilité retirée | L'ancien journal n'est plus détecté ni repris, sans conversion ou suppression automatique de données sur les serveurs. |
| Verrou | `operations.flock` conserve son exclusion de processus entre routage et management. |
| Réseau | Découverte FRONT par IP et UUID, prochain saut limité à FRONT, ajouts persistants/actifs, métriques et routes préexistantes conservés. |
| Destinations | IP seule convertie en `/32`, CIDR IPv4 canonique ou mélange des deux, avec union exacte des destinations. |
| Exploitation | Pas de ping ni de réactivation de connexion ajoutés. Les paramètres réseau de profil en attente restent préservés. |
| Management DNS | Un journal FRONT courant en attente bloque toujours la publication. Les mécanismes de transaction propres au management ne sont pas modifiés. |
| CI et qualité | Fichiers de tests existants conservés, scénarios applicables portés sur le moteur courant ; seuil de couverture management de 98 % conservé. Aucun nouveau paquet d'exécution. |

Le retrait de l'ancien format est volontairement incompatible avec les versions qui le produisaient. Le prérequis fourni par l'utilisateur est qu'aucune récupération de ce format n'est encore nécessaire. Le rôle courant ne propose aucun mécanisme implicite pour le migrer ou l'effacer. Les autres composants du rôle, notamment la compatibilité Ansible 2.9 et les transactions propres au management DNS, restent dans leur périmètre existant.

## Procédures de contrôle

Les résultats courants et leurs statuts sont consignés dans `DELIVERY.json`. Les rapports de la révision du 25 octobre décrivent uniquement leur code et leur recette historiques.

**EXÉCUTÉ — FRONT et intégration des sources :** 178 tests réussis, aucun échec ni test ignoré, en 270,18 secondes. Les cas applicables de sécurité/reprise utilisent les journaux réellement produits par le moteur actuel. La suppression de l'adaptateur, le refus des anciennes actions et l'absence des anciens alias de tests sont contrôlés.

**EXÉCUTÉ — management et protections partagées :** 507 tests réussis, aucun échec ni test ignoré, en 387,20 secondes. Couverture réelle du module `bind9_management.py` : 1478 lignes couvertes sur 1508, soit 98,010610 %, aucune ligne exclue ; seuil de 98 % respecté. Les deux suites incluent quelques contrôles communs et leurs nombres ne représentent pas une somme de tests distincts.

**EXÉCUTÉ — NetworkManager réel :** 14 workflows Ansible sur NetworkManager 1.40.16, Python 3.6.8 et Ansible 2.9.27, dans un conteneur jetable sans réseau extérieur. Le laboratoire reçoit seulement `NET_ADMIN` dans son propre espace réseau, sans `SYS_ADMIN` ni désactivation de seccomp ; les sources sont montées en lecture seule. Plan, application, reprise, commit, répétition sans changement, passerelle non par défaut, métriques et préservation des paramètres DNS en attente sont contrôlés.

**EXÉCUTÉ — analyse et syntaxe :** Pyflakes sur tous les modules Python du rôle et les tests, ShellCheck épinglé par la CI sur tous les scripts, `bash -n` sur le script FRONT et syntaxe des deux playbooks publics. Aucun diagnostic non résolu. Les avertissements de dépréciation de l'environnement Python 3.6 historique restent visibles.

**EXÉCUTÉ — livraison :** manifeste SHA-256, archive déterministe sans caches/journaux temporaires, et application du patch sur une copie de la livraison précédente avec comparaison exacte des sources. Pour appliquer ce patch sous Windows, utiliser `git -c core.autocrlf=false apply --check` puis `git -c core.autocrlf=false apply` avec le chemin du patch, afin de conserver les fins de ligne des scripts.

Depuis la racine du projet dans un laboratoire Linux root avec les dépendances QA du projet :

```bash
python3 -m pytest -p no:cacheprovider -q \
  tests/test_native_front_routes.py tests/test_front_route_hardening.py \
  tests/test_front_route_hardening_ansible.py tests/test_front_routes_ansible.py \
  tests/test_routes_deployed_entry_ansible.py tests/test_source_reachability.py \
  tests/test_language_separation.py
python3 -m pyflakes roles/bind9/filter_plugins roles/bind9/files \
  roles/bind9/module_utils roles/bind9/library tests
bash -n roles/bind9/files/bind-front-routes.sh
```

La suite management et le seuil `--fail-under=98` sont ceux de `.github/workflows/ci.yml`, étape « Targeted management, deployment seal and transport on legacy Ansible ». Les deux playbooks publics se contrôlent avec `--syntax-check` et les chemins `ANSIBLE_CONFIG`/`ANSIBLE_ROLES_PATH` explicitement définis si le montage local est accessible en écriture à tous. ShellCheck utilise l'image épinglée par la CI. La recette native reste `bash tests/run_integration_front_routes.sh` dans un laboratoire Linux autorisant les espaces réseau/montage privés ; elle ne constitue pas une procédure de déploiement.

## Recette restante et retour arrière

**NON EXÉCUTÉ — serveurs de l'utilisateur :** aucun accès aux cibles réelles. Prérequis : confirmer le format de journal actuellement utilisé, l'IP FRONT, son UUID, les versions installées et une fenêtre exclusive. Procédure : adapter l'inventaire et la limite d'hôte documentés dans `FRONT_ROUTES.md`, lancer `--tags routes --check -v`, examiner le plan puis appliquer et répéter. Résultat attendu : ajouts spécifiques via FRONT, deuxième exécution sans changement, route par défaut et réseaux ADM/BACK préservés. Pour une reprise, utiliser seulement un journal courant préalablement inspecté avec `--tags routes-recover`. Risque restant : état réel non observé, en particulier un ancien journal encore nécessaire contrairement au prérequis fourni.

**NON EXÉCUTÉ — autres combinaisons CI :** procédure de qualification via `.github/workflows/ci.yml` sur un dépôt disposant des runtimes déclarés. Résultat attendu : tous les contrôles applicables sans échec ni baisse de seuil. Risque restant : compatibilité non mesurée sur les autres combinaisons.

Retour arrière du code : restaurer la précédente archive corrigée complète et ses empreintes. Ce retour ne retire pas des routes déjà committées. Le format du journal courant restant identique, sa reprise garde le même mécanisme ; préserver tout journal ambigu pour inspection et ne pas le supprimer pour forcer une relance.

**Qualification opérationnelle : NON TERMINÉE pour les serveurs réels.** Le déploiement et la recette sur ces serveurs restent à effectuer dans le périmètre autorisé.
