# Durcissement DNSSEC et compatibilité du contrôleur

## Configuration publique

```yaml
bind_dnssec_active: auto
```

Valeurs : booléens YAML `false` et `true`, chaînes CLI exactes `false`/`true`,
ou `auto`. Défaut actuel : `auto`. Remplacer le nom public `bind_dnssec_enabled`
de la livraison précédente dans vos inventaires et paramètres `-e` ; aucun
alias n’est ajouté. En mode désactivé, aucun helper, hook, service ou timer
DNSSEC n’est installé ; les anciens composants gérés sont retirés.

## Diagnostic et décision

L’ancien message sur les faits dérivés était une assertion de cohérence,
pas une preuve suffisante d’une surcharge utilisateur. Un booléen YAML
`false` est valide. Le laboratoire reproduit exactement ce message lorsqu’un
ancien mode est forcé par extra-vars ; cette condition ne démontre pas la
cause exacte sur le serveur de l’utilisateur.

L’utilisateur a également signalé un test `boolean` inconnu. Les tests Jinja
`boolean` et `integer` sont apparus en 2.11. L’archive précédente échoue avec
Jinja 2.10.3 sur ces deux tests ; les contrôles corrigés réussissent sur ce
même moteur. Il s’agit du Jinja du contrôleur, indépendamment du Python cible.

La correction supprime les faits DNSSEC intermédiaires et leur assertion.
Le rôle valide strictement l’entrée publique avec `type_debug`, puis utilise
directement `bind_dnssec_active == 'auto' or (bind_dnssec_active | bool)`.
Le template calcule sa directive localement depuis la même entrée.
Un ancien mode à priorité supérieure ne peut plus choisir une autre politique.
Les valeurs invalides restent refusées : le filtre `bool` n’est pas utilisé
comme validation permissive. Les contrôles de types du routage sont également
remplacés par `type_debug` ; le drapeau route reste un booléen strict et sa
métrique un entier, jamais un booléen.

## Parties du code impactées

| Fichier | Bloc et effet |
|---|---|
| `roles/bind9/defaults/main.yml` | Entrée publique `bind_dnssec_active: auto` |
| `roles/bind9/tasks/tasks.d/dnssec_settings.yml` | Validation stricte avant modification ; suppression des deux faits et du contrôle de surcharge ; erreur précisant le type reçu |
| `roles/bind9/tasks/main.yml` | Validation initiale, décisions d’installation/activation/retrait DNSSEC, contrôle compatible du booléen de routage |
| `roles/bind9/tasks/tasks.d/deploy/directories.yml` | Répertoire de clés gérées uniquement si validation active |
| `roles/bind9/tasks/tasks.d/native_loopback.yml` | Vérification de l’ancre native seulement dans les modes actifs |
| `roles/bind9/tasks/tasks.d/deploy/front_routes_plan.yml` | Type entier de la métrique via `type_debug` ; refus des booléens conservé |
| `roles/bind9/templates/named.conf.j2` | Directive `no`/`yes`/`auto` depuis la seule entrée publique ; écoute IPv6 toujours fermée |
| `roles/bind9/templates/named-runtime-override.j2`, `roles/bind9/tasks/tasks.d/named_runtime.yml` et `roles/bind9/handlers/main.yml` | Retrait de la surcharge `named -4`, héritage du lancement natif, rechargement des unités et restart lors de la migration |
| `roles/bind9/filter_plugins/dns_topology.py` et `roles/bind9/tasks/tasks.d/deploy/validate.yml` | Forwarders IPv4/IPv6, garde des endpoints, glue A/AAAA et refus des boucles locales |
| `roles/bind9/filter_plugins/client_acl.py` | Clarification du périmètre IPv4 des adresses clientes/service ; données DNS IPv4/IPv6 inchangées |
| `tests/test_optional_dnssec.py` | Valeurs, conditions réelles, rendu, absence de faits intermédiaires et de tests Jinja incompatibles |
| `tests/test_dnssec_settings_ansible.py` | Nouveau runner réel, deux modes de typage Jinja, collisions extra-vars et refus des valeurs invalides avant écriture cible |
| `tests/test_dnssec_workflows.py` | Entrée renommée dans les installations, désactivations, idempotence et restaurations |
| `tests/integration_loopback.py`, `tests/test_private_dnssec.py`, `tests/test_dns_topology.py`, `tests/test_global_review.py` | Politique publique, lancement natif, IPv6 sortant UDP/TCP, absence d’écoute IPv6 et restart de migration |
| `.github/workflows/ci.yml` | Matrice Ansible 2.9 avec Jinja 2.10.3 et 3.0.3 ; MarkupSafe compatible épinglé pour ces environnements QA |
| `examples/deux-zones-forwardees/variables.yml` et documentation associée | Nom public actualisé ; rendus alignés sur le défaut actuel `auto`, exceptions limitées aux suffixes privés |

La structure DNS sans vues, les ACL, les zones natives, le routage additif et
le rollback sont conservés. Aucun Python de routage, collection ou dépendance
de production n’est ajouté. Les paquets Jinja de recette restent hors ZIP.

## Contrôles et limites

**EXÉCUTÉ** : suite avec Jinja 2.10.3 et 3.0.3, trois modes DNSSEC et chaînes CLI,
typages natif/classique, entrées invalides, mode ancien forcé par extra-vars,
préflight des 12 hôtes simulés, syntaxe de trois inventaires et contrôles qualité.
Les workflows réels Ansible de routes et de fichiers utilisent des doubles
CLI/systemd ; les laboratoires BIND et NetworkManager exécutent leurs vrais
binaires dans un réseau isolé. Résultats exacts : `DELIVERY.json`.

**NON EXÉCUTÉ** : recette système sur vos serveurs RHEL 8. Avant production,
utiliser une paire de recette, sauvegarder BIND et runtime, appliquer les trois
modes puis `false`, vérifier services, SELinux, réponses DNS, deuxième passage,
échec contrôlé et restauration. Attendu : politique sélectionnée respectée,
aucun composant DNSSEC déployé en `false`, aucune altération des routes initiales,
retour à l’état sauvegardé après échec. Risques restants : différences de versions
et de confinement, ancres/chaînes réelles et reprise après perte du contrôleur.
Qualification de production **NON TERMINÉE**. Procédures détaillées :
[VALIDATION_RHEL8.md](VALIDATION_RHEL8.md), [DNSSEC.md](DNSSEC.md) et
[OPERATIONS.md](OPERATIONS.md).


Le transport est également réaligné : aucun `ExecStart` n’est imposé par le rôle ; les échanges sortants IPv4/IPv6 sont possibles et seule l’écoute DNS IPv6 est fermée. Le handler recharge les définitions systemd avant application, y compris via les tags partiels. Les routes retour restent IPv4. Détails, commandes et limites : [TRANSPORT_DNS.md](TRANSPORT_DNS.md).
