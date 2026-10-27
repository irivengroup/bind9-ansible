# Passerelle FRONT sans route par défaut FRONT

Révision : `2026.10.25-front-gateway.1`.

Rapport historique de cette correction. La révision suivante retire la compatibilité des anciens journaux FRONT ; le périmètre et les preuves actuels sont dans [NETTOYAGE_FRONT_ACTUEL.md](NETTOYAGE_FRONT_ACTUEL.md). Les mentions de compatibilité et de fichiers modifiés ci-dessous décrivent la révision du 25 octobre.

## Cause et périmètre

Le message signalé est `No IPv4 gateway on the active FRONT profile/device; no global gateway fallback`, dans la tâche `Run the single native FRONT transaction with bounded observations`.

L'absence d'une route par défaut sur FRONT était déjà supportée lorsque `ipv4.gateway`, `IP4.GATEWAY` ou une route par défaut du profil FRONT fournissait le prochain saut. La correction concerne le cas supplémentaire où ces trois sources sont vides, mais où une route spécifique du profil ou de l'interface FRONT fournit déjà une passerelle.

**Information fournie :** FRONT possède une passerelle et la route par défaut système est portée par une autre interface. **Hypothèse reproduite :** la passerelle est visible dans les routes spécifiques, alors que les propriétés de passerelle sont vides. Les sorties NetworkManager du serveur concerné n'ont pas été fournies ; cette hypothèse ne constitue donc pas une observation de ce serveur. Si `ipv4.gateway` y est effectivement renseigné, le code d'origine le lisait déjà : vérifier l'interface et l'UUID actifs sur la cible avant de conclure à la même cause.

## Comportement livré

Le seul fichier d'exécution modifié est `roles/bind9/files/bind-front-routes.sh`. Après les trois sources préexistantes, le moteur recherche un prochain saut unique parmi les routes simples du profil FRONT actif et les routes de la table principale associées à l'interface FRONT. Plusieurs routes utilisant le même prochain saut ne sont pas ambiguës. Deux prochains sauts différents provoquent un refus avant écriture.

Les routes sans passerelle et celles d'autres interfaces ne servent jamais à l'inférence. Les attributs de routage supplémentaires sont refusés pour cette découverte ; `proto static` est accepté dans l'état actif. La passerelle découverte doit toujours être dans le sous-réseau FRONT, utilisable et distincte des adresses locales. Une destination directement connectée n'impose aucune inférence de passerelle. En l'absence de source utilisable, l'erreur inclut maintenant l'interface, l'UUID actif et la destination.

La priorité des propriétés explicites, les métriques existantes, les codes de sortie, les arguments, les protections SSH/ADM/BACK, le verrou de processus, les contrôles de dérive et le format des journaux restent inchangés. Aucun nouvel inventaire, paramètre, paquet ou filtre n'est requis. Aucun ping n'est ajouté.

La liste de destinations du rôle accepte des IP seules, des sous-réseaux IPv4 canoniques, ou un mélange des deux. Par exemple :

```yaml
bind_front_routes_enabled: true
bind_cloud_clients:
  - 203.0.113.25
  - 172.20.0.0/16
```

L'IP seule devient `203.0.113.25/32` ; le sous-réseau garde son masque. La normalisation et l'agrégation existantes conservent exactement l'union des destinations, sans élargir les accès. Ces valeurs sont des exemples de laboratoire, à remplacer par les destinations autorisées.

## Adaptation du playbook demandé

| Disposition | Intégration dans le rôle |
|---|---|
| Repérer l'interface par son IP | Observation native existante, qui couvre aussi les adresses secondaires. Pas de dépendance à une adresse primaire dans les faits Ansible. |
| Lire la passerelle du profil NetworkManager | `nmcli -g ipv4.gateway connection show uuid ...` reste la première source. L'UUID actif évite les noms de connexions dupliqués. |
| Lire et comparer les routes | Conservation du parseur structuré et de la comparaison exacte destination/passerelle/métrique. Une expression régulière sur une chaîne ne remplace pas ces contrôles. |
| Ajouter seulement ce qui manque | Conservation de `nmcli +ipv4.routes` pour le profil et de `ip route add` pour le noyau. |
| Activer les nouvelles routes | Ajout actif existant, sans `nmcli connection up`, qui pourrait activer des adresses, DNS ou autres routes en attente. |
| Diagnostic | Le résumé existant indique interface, UUID, passerelle retenue et ajouts. Le refus pour passerelle absente identifie maintenant la destination. |
| Retirer la validation demandée | Aucun ping, aucune cible ICMP et aucune pause fixe. Les contrôles internes de cohérence et de reprise sont conservés. |

## Impact et risques

| Domaine | Impact et protection |
|---|---|
| Routage | Nouvelle source de passerelle limitée à FRONT ; aucune route globale, ADM ou BACK n'est utilisée comme repli. |
| Sécurité | Réutilisation des contrôles d'adresse et refus des ambiguïtés/attributs non supportés. Aucune évaluation de commande ni interpolation d'entrée dans un shell supplémentaire. |
| Compatibilité | Sources explicites prioritaires, CLI et journal conservés. Les clients directement connectés conservent leur fonctionnement sans passerelle. |
| Données et reprise | Aucun changement de schéma. Les ajouts restent journalisés et le rollback retire seulement les opérations appartenant à la transaction. |
| Exploitation | Pas de réactivation d'interface ; pas d'activation des paramètres de profil en attente. Le fonctionnement reste additif. |
| Performance | Parcours supplémentaire des observations déjà capturées seulement si nécessaire. Les limites existantes de 1 Mio par observation et 512 destinations restent en place. Aucun gain de performance n'est revendiqué. |
| CI | Les fichiers de tests existants restent sélectionnés par le workflow actuel ; aucune modification du workflow ni de ses seuils. |

L'inférence suppose que le prochain saut unique des routes simples de FRONT est approprié pour les destinations cloud demandées. Elle ne découvre pas une passerelle absente de l'état observé et ne remplace pas une décision réseau lorsque plusieurs routeurs FRONT existent.

## Preuves et reproduction

**EXÉCUTÉ — reproduction avant correction :** le nouveau test `TestNativeProcessTransactions::test_gateway_from_front_specific_routes_without_a_front_default[profile]` échoue sur le script fourni avec exactement le message signalé et un code de sortie 1. Le même scénario attend un plan de deux ajouts, sans modification pendant la simulation.

**EXÉCUTÉ — NetworkManager natif :** 14 workflows Ansible sur NetworkManager `1.40.16`, Bash `4.4.20`, Python `3.6.8`, Ansible `2.9.27` et Jinja2 `2.10.3`. L'image EL8 de base est `sha256:215db17f4fd2f7632d2d7025ccd8cf0d14e28a2040807ec83c931f98a0441f01`. Le laboratoire final utilise un conteneur sans réseau extérieur et uniquement `NET_ADMIN` dans son propre espace réseau, sans `SYS_ADMIN`, sans désactivation de seccomp et sans accès au réseau hôte. Les sources sont montées en lecture seule.

Le scénario natif met FRONT en `ipv4.never-default: yes`, réactive le profil uniquement dans le laboratoire, vérifie que `ipv4.gateway` et `IP4.GATEWAY` sont vides et qu'aucune route par défaut FRONT n'existe. Il exécute plan, ajout et répétition sans changement pour une nouvelle destination. Le prochain saut FRONT est retrouvé ; les routes préexistantes, les profils BACK/ADM et la route par défaut BACK sont conservés. Les autres scénarios couvrent reprise, commit, métrique automatique et préservation des DNS de profil en attente pendant l'application.

**EXÉCUTÉ — suite ciblée finale :** 171 tests réussis, aucun échec ni test ignoré, en 233,40 secondes. Les 58 avertissements de dépréciation Python/Jinja/MarkupSafe de l'environnement historique restent visibles. Les tests couvrent notamment les trois sources de routes spécifiques, l'ambiguïté des prochains sauts, les passerelles inutilisables, l'exclusion des autres interfaces, les clients directement connectés, la simulation, l'idempotence et les transactions différées.

**EXÉCUTÉ — complément IP et sous-réseau :** 2 tests supplémentaires réussis, aucun échec ni test ignoré, en 10,50 secondes, après la précision utilisateur. Ils exercent le même code d'exécution final, sans modification supplémentaire de ce code : une IP seule et un sous-réseau ensemble, simulation sans écriture, ajout exact en `/32` et `/16`, conservation des routes initiales et répétition sans changement. L'un utilise le moteur Bash et l'autre les tâches Ansible réelles. Total : 173 cas distincts exécutés.

**EXÉCUTÉ — syntaxe et analyse :** `bash -n`, Pyflakes sur tous les modules Python du rôle et tous les tests, syntaxe du playbook public avec Ansible `2.9.27`, ShellCheck sur tous les scripts du projet, épinglé à `sha256:61862eba1fcf09a484ebcc6feea46f1782532571a34ed51fedf90dd25f925a8d`. Aucun diagnostic non résolu.

**EXÉCUTÉ — recherche de code inutile :** Pyflakes ne signale aucun import inutilisé ni nom non défini ; ShellCheck ne signale aucun élément inutile à retirer. Aucun chemin de reprise historique ni test de compatibilité n'a été supprimé sans preuve d'absence d'usage. Aucun code de ping, de pause ou de nouvelle réactivation de connexion n'a été introduit. Il n'y a donc pas de suppression supplémentaire dans cette correction. Ces analyseurs ne constituent pas une preuve générale d'absence de code mort dynamique.

**EXÉCUTÉ — livraison :** application du patch sur l'archive source avec comparaison octet par octet, contrôle du manifeste SHA-256, reconstruction déterministe du ZIP et vérification de l'absence de caches/journaux temporaires dans l'archive. L'archive d'origine reste intacte. Pour appliquer le patch sous Windows en conservant les fins de ligne des scripts, exécuter depuis la racine d'une copie source `git -c core.autocrlf=false apply --check /chemin/vers/bind9-ansible-v4-front-corrige.patch`, puis la même commande sans `--check`. Adapter le chemin du patch. La conversion automatique CRLF d'un Git globalement configuré peut sinon changer les empreintes et rendre un script Bash inexécutable ; l'archive corrigée contient directement les fins de ligne attendues.

Les résultats chiffrés de la suite ciblée finale et les empreintes des fichiers sont dans `DELIVERY.json`. Depuis la racine du projet dans un contrôleur Linux disposant des dépendances QA du projet et des droits root pour les fixtures isolées :

```bash
python3 -m pytest -p no:cacheprovider -q \
  tests/test_native_front_routes.py \
  tests/test_front_routes_ansible.py \
  tests/test_front_route_hardening.py \
  tests/test_front_route_hardening_ansible.py \
  tests/test_routes_deployed_entry_ansible.py \
  tests/test_language_separation.py
bash -n roles/bind9/files/bind-front-routes.sh
python3 -m pyflakes tests/test_native_front_routes.py \
  tests/test_front_routes_ansible.py tests/integration_front_routes.py
ANSIBLE_CONFIG="$PWD/ansible.cfg" ANSIBLE_ROLES_PATH="$PWD/roles" \
  ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --syntax-check
```

La recette native complète est reproductible sur un hôte Linux de laboratoire disposant de NetworkManager, D-Bus, Ansible et des permissions de création d'espaces isolés avec `bash tests/run_integration_front_routes.sh`. Ce script refuse l'exécution sans root et utilise ses propres espaces réseau/montage. Ne pas l'exécuter comme procédure de déploiement sur les serveurs DNS.

## Recette restante et retour arrière

**NON EXÉCUTÉ — serveurs de l'utilisateur :** aucun accès aux cibles ni inventaire réel activé. Prérequis : relever les versions, l'IP FRONT, son interface, son UUID actif, ses passerelles et routes ; disposer d'une session d'administration et d'une fenêtre exclusive. Adapter l'inventaire et la limite d'hôte des commandes documentées dans `FRONT_ROUTES.md`, puis exécuter `--tags routes --check -v`. Résultat attendu : sélection de la passerelle FRONT, ajouts spécifiques uniquement et aucun changement réseau pendant la simulation. Après revue du plan, appliquer le même tag et le répéter ; résultat attendu : routes persistantes/actives cohérentes, deuxième exécution sans changement, route par défaut système inchangée. Risque restant : l'état réel peut ne pas correspondre au scénario reproduit, ou plusieurs passerelles peuvent nécessiter une décision opérateur.

**NON EXÉCUTÉ — autres combinaisons de la CI :** seul l'environnement historique EL8/Ansible `2.9.27` a été exécuté pour cette correction. Procédure : lancer `.github/workflows/ci.yml` sur la révision dans le dépôt connecté, avec les runtimes de sa matrice. Résultat attendu : contrôles concernés sans échec ni baisse de seuil. Risque restant : compatibilité non mesurée sur les autres runtimes déclarés.

Le retour du code consiste à restaurer le script d'origine depuis l'archive source, conservée intacte. Cela ne retire pas des routes déjà committées. En cas de transaction interrompue, inspecter le journal et employer la procédure `routes-recover` existante ; ne pas supprimer un journal pour forcer une relance. Le format de journal n'est pas modifié par cette correction.

**Qualification opérationnelle : NON TERMINÉE.** Restent la confirmation de l'état exact de FRONT et la recette sur les serveurs concernés, ainsi que les autres combinaisons CI si leur qualification est requise pour la livraison du projet.
