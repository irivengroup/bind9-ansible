# Durcissement du moteur FRONT actuel

Révision : `2026.10.26-front-hardening.1`.

Le périmètre couvre le moteur de routes IPv4 FRONT, son journal courant et le reçu transmis au contrôleur Ansible. La source est `bind9-ansible-v4-actuel.zip`, SHA-256 `0f66737f83de2ea965e2385afc1557b8d1cdfa100a08281f49ddb14177ee3cbf`, conservée intacte. Le code d'exécution Python de gestion DNS ne change pas.

## Constats reproduits

Sur le code source précédent, 22 cas de refus attendus échouaient : une progression ou des métadonnées pouvaient contenir des données supplémentaires ignorées ; une opération pouvait être reprise sans recoupement avec les clients enregistrés ; des champs natifs dupliqués étaient réduits à leur dernière valeur ; un reçu dupliqué ou malformé pouvait être accepté.

Les reproductions utilisent exclusivement des transactions synthétiques, en laboratoire root isolé. Pour tester les contrôles sémantiques séparément du checksum, certaines modifications de journal recalculent volontairement les empreintes. Cela ne démontre pas un accès possible à ces fichiers par un utilisateur non privilégié : les permissions root `0700`/`0600` restent la frontière de confiance existante. SHA-256 assure l'intégrité, sans authentifier un attaquant capable de réécrire tout le journal et ses empreintes.

## Corrections et menaces couvertes

| Entrée ou situation | Risque observé | Contrôle actuel |
|---|---|---|
| Fichiers de journal tronqués, NUL, lignes vides supplémentaires | Octets perdus lors des lectures Bash ou enregistrements ignorés | Lecture bornée et sans NUL via le mécanisme existant ; taille originale égale au texte chargé plus un seul LF final. |
| Métadonnées et progression | Seule la première ligne pouvait être interprétée | Un seul enregistrement, nombre exact de champs, UUID structurés, métrique globale positive, progression entièrement reconnue et bornée. |
| Clients et opérations enregistrés | Reprise d'opérations sans destination autorisée dans le journal | 1 à 512 clients, 1 à 1024 opérations, normalisation et reconstruction de l'union exacte des clients ; chaque opération appartient à cette union et possède une identité unique par magasin/destination. |
| Observation native avec plusieurs `via`, `dev`, `metric`, `proto`, `scope` ou `src` | Première valeur perdue dans la représentation canonique | Refus des champs d'identité dupliqués avant tout plan d'écriture. Les configurations multipath restent hors du périmètre supporté. |
| Reçu de transaction absent, malformé ou dupliqué | Sélection du premier jeton sans preuve d'unicité | Exactement une ligne de reçu et exactement un UUID canonique ; expressions YAML explicitement textuelles et tests positifs LF/CRLF. |
| Reçu invalide après une application différée | Routes modifiées dont l'état pending pourrait être oublié | État pending enregistré avant de valider le reçu ; jeton précédent effacé, aucune identité non vérifiée réutilisée. L'erreur est explicite et le journal reste disponible pour reprise. |

Les fichiers, leurs modes, le verrou `operations.flock`, les snapshots et le nombre de neuf fichiers du journal sont conservés. Le format valide produit par le rôle reste inchangé. Les métadonnées malformées et observations ambiguës sont volontairement refusées ; elles ne sont pas « réparées » ou converties automatiquement.

Les IP seules, sous-réseaux et unions agrégées restent supportés. Une IP devient une route `/32`. Une métrique active héritée à zéro reste autorisée, alors que la métrique globale d'ajout doit être positive. La suppression de l'ancien adaptateur est conservée. Aucun ping, rechargement d'interface, repli de passerelle globale ou nouveau paquet d'exécution n'est introduit.

## Intégration et compatibilité

Les deux fichiers d'exécution modifiés sont `roles/bind9/files/bind-front-routes.sh` et `roles/bind9/tasks/tasks.d/deploy/front_return_routes.yml`. Les actions publiques et codes de sortie restent les mêmes. Les protections de chemin, liens, propriétaires, taille, timeout, SSH et préservation des routes préexistantes restent actives.

Le nouveau fichier `tests/test_front_route_recovery_hardening.py` est ajouté au contrôle root FRONT de la CI existante ; aucun seuil qualité n'est diminué. Les autres mécanismes de la CI restent dans leur périmètre actuel. Le code Python de production étant inchangé, la précédente mesure de couverture management de 98,01 % reste historique et n'est pas présentée comme une nouvelle mesure du Bash durci.

En cas de reçu non fiable, le rôle conserve le signal de transaction en attente pour que le rollback et la gestion de bail du déploiement ne déclarent pas l'état réseau résolu. Aucune suppression ne s'autorise à partir d'un jeton ambigu. Après inspection de l'état réel, `--tags routes-recover` reste la procédure de reprise du seul format actuel.

Le durcissement ajoute des lectures bornées des neuf fichiers et réutilise l'agrégation existante, limitée à 512 clients. Aucun gain de performance n'est revendiqué. Les timeouts existants par commande et la limite de 1 Mio par observation/fichier restent en place ; aucune durée globale de déploiement n'est promise.

## Contrôles reproductibles

Les statuts et chiffres de la révision sont consignés dans `DELIVERY.json`. Les contrôles locaux emploient l'environnement EL8 de référence, Python 3.6.8, Ansible 2.9.27, Jinja2 2.10.3 et NetworkManager 1.40.16. Les avertissements de dépréciation de cet environnement restent visibles.

**EXÉCUTÉ — reproduction avant correction :** 22 refus attendus manquants sur 24 cas, les deux cas positifs étant déjà acceptés. **EXÉCUTÉ — suite finale :** 253 tests réussis, aucun échec ni test ignoré, en 322,70 secondes, dont 33 cas dédiés au durcissement. Les erreurs de développement détectées par l'analyse Shell et les tests Ansible ont été corrigées avant cette suite finale.

**EXÉCUTÉ — recette native :** les 14 workflows Ansible avec NetworkManager réel réussissent sur le code final. **EXÉCUTÉ — compatibilité des journaux valides :** neuf cas emploient le producteur de journal de l'archive source précédente puis le lecteur durci, sans modifier les fichiers du journal entre les deux versions. Les trois actions `verify`, `commit` et `recover` réussissent pour l'agrégation de sous-réseaux adjacents, une liste IP/sous-réseau et une métrique existante à zéro.

**EXÉCUTÉ — contrôles statiques :** tous les scripts passent ShellCheck sans exclusion supplémentaire ; syntaxe Bash, Pyflakes sur le rôle et les tests, et syntaxe des deux playbooks publics sans diagnostic non résolu. **EXÉCUTÉ — livraison :** manifeste vérifié, ZIP déterministe, absence de caches/journaux temporaires et application exacte du patch sur la source précédente. Sous Windows, employer `git -c core.autocrlf=false apply --check` puis `git -c core.autocrlf=false apply` avec le chemin du patch pour préserver les fins de ligne des scripts.

Depuis la racine du projet dans un laboratoire Linux root avec les dépendances QA du projet :

```bash
python3 -m pytest -p no:cacheprovider -q \
  tests/test_front_routes_ansible.py tests/test_front_route_hardening_ansible.py \
  tests/test_native_front_routes.py tests/test_front_route_hardening.py \
  tests/test_front_route_recovery_hardening.py \
  tests/test_routes_deployed_entry_ansible.py tests/test_source_reachability.py \
  tests/test_language_separation.py tests/test_operation_resilience.py
python3 -m pyflakes roles/bind9/filter_plugins roles/bind9/files \
  roles/bind9/module_utils roles/bind9/library tests
bash -n roles/bind9/files/bind-front-routes.sh
```

Les deux playbooks publics sont également contrôlés avec `--syntax-check` et les chemins `ANSIBLE_CONFIG`/`ANSIBLE_ROLES_PATH` explicites si nécessaire. ShellCheck utilise l'image déjà épinglée par la CI. La recette native est `bash tests/run_integration_front_routes.sh` dans un laboratoire autorisant les espaces privés ; le laboratoire de cette livraison utilise une alternative en conteneur sans réseau extérieur, avec uniquement `NET_ADMIN` dans son espace réseau, sans `SYS_ADMIN`, sans désactivation de seccomp et avec sources en lecture seule.

Le nouveau module comporte des contrôles positifs : journaux valides, union de sous-réseaux adjacents, liste IP/sous-réseau, métrique active existante à zéro et reçus LF/CRLF. Les refus vérifient l'absence d'écriture et la conservation des preuves. Les tests de reçu vérifient aussi le message de refus attendu et les faits pending/jeton dans la branche de reprise, afin qu'une erreur de syntaxe ne puisse pas compter comme un refus correct.

## Limites et recette restante

**NON EXÉCUTÉ — serveurs réels :** aucun accès aux cibles de l'utilisateur. Prérequis : relever l'état FRONT, les versions, l'UUID actif, la passerelle et les journaux ; disposer d'une fenêtre exclusive. Procédure : adapter l'inventaire et l'hôte des commandes de `FRONT_ROUTES.md`, lancer `--tags routes --check -v`, examiner le plan, appliquer puis répéter. Résultat attendu : destinations exactes via FRONT, route par défaut inchangée, deuxième exécution sans changement. Tester une reprise uniquement sur un journal courant inspecté. Risque restant : état et versions des serveurs non observés.

**NON EXÉCUTÉ — matrice CI distante complète :** procédure via `.github/workflows/ci.yml` sur un dépôt et les runtimes déclarés. Résultat attendu : contrôles applicables sans échec ni baisse de seuil. Risque restant : compatibilité non mesurée sur les autres combinaisons.

La protection repose sur un contrôleur et un compte root de confiance, des outils système de confiance et le respect du verrou par les autres opérations. Elle ne garantit pas l'atomicité contre une intervention root concurrente qui remplace des fichiers entre deux contrôles. Les erreurs observées conduisent à un refus et à la conservation des données, sans repli silencieux.

Retour arrière du code : restaurer l'archive source actuelle précédente et ses empreintes. Cela ne retire pas des routes déjà committées et rétablit les limites de validation de cette version. Préserver tout journal ambigu pour inspection ; ne pas l'effacer afin de forcer une relance. Une reprise valide utilise le format courant, dont le schéma ne change pas.

**Qualification opérationnelle : NON TERMINÉE pour les serveurs réels.** Les essais locaux couvrent les changements décrits ; ils ne constituent pas une qualification exhaustive de toute l'installation BIND ou de l'infrastructure de production.
