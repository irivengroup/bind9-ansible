# Validation DNSSEC optionnelle

**Rapport de la livraison source.** La consolidation et les plafonds de cache sont conservés ; le test combiné courant est décrit dans [OPERATIONS.md](OPERATIONS.md#déploiement-combiné-dnssec-auto-et-routes-front).

```yaml
bind_dnssec_active: auto
```

| Valeur | Directive BIND | Déploiement spécifique DNSSEC |
|---|---|---|
| `false` | `dnssec-validation no;` | Aucun helper, hook privé, service ou timer installé ; anciens assets gérés retirés |
| `true` | `dnssec-validation yes;` | Ancres natives chargées explicitement, réconciliateur d’exceptions, hook et renouvellement |
| `auto` (défaut) | `dnssec-validation auto;` | Ancres automatiques via `bindkeys-file`, réconciliateur, hook et renouvellement |

Les booléens YAML et chaînes CLI exactes `true`/`false` sont acceptés, ainsi
que la chaîne `auto`. Les autres chaînes, nombres et valeurs nulles sont
refusés avant modification, pour tous les tags. `bind_dnssec_active` est
l’unique entrée de politique : aucun fait de mode ou d’activation n’est généré
ou consommé. Les anciennes variables intermédiaires ne pilotent plus le rôle.
La validation utilise `type_debug`, compatible avec Jinja 2.10 ; les tests
`is boolean` et `is integer`, introduits dans Jinja 2.11, ne sont pas utilisés.

Après cette validation stricte, les tâches s’activent avec :

```yaml
when: bind_dnssec_active == 'auto' or (bind_dnssec_active | bool)
```

Le cas `auto` est évalué explicitement. `| bool` seul ne valide pas une entrée
et pourrait convertir une valeur incorrecte en désactivation silencieuse.
Le template calcule localement la directive depuis cette même entrée publique,
sans fait Ansible surchargeable. Voir la section « Compatibilité et migration » ci-dessous.

Le défaut est désormais `auto` : sans surcharge, le rôle active la validation
et ses composants associés. Une valeur explicite `false` conserve la désactivation.
L’entrée publique reste `bind_dnssec_active`. Avant mise à niveau, vérifier
les ancres natives et les exceptions des zones privées ; voir
la section « Compatibilité et migration » ci-dessous.

## Ancres et exceptions privées

En mode `true`, le template inclut explicitement `/etc/named.root.key`, le
fichier natif RHEL contenant les ancres gérées. En mode `auto`, il utilise ce
fichier via `bindkeys-file`. Le rôle ne génère ni ne remplace ces ancres :
elles doivent être actualisées par les paquets et procédures de la distribution.
Les deux modes actifs vérifient la présence du fichier et préparent le
répertoire de clés gérées. En mode `false`, ces directives et contrôles sont
omis ; les fichiers natifs et états existants ne sont pas effacés.

Cette différence entre ancre explicite et automatique suit la
[référence officielle BIND](https://bind9.readthedocs.io/en/v9.18.24/reference.html#namedconf-statement-dnssec-validation).
Le mode `false` ne rejette plus une signature DNSSEC invalide ; les contrôles
d’accès, TSIG, IPv4 et l’administration RNDC restent indépendants.

`dnssec_validate: false` sur une zone forward décrit une exception privée
uniquement lorsque la validation globale est active. RHEL 8/BIND 9.11 ne
dispose pas de `validate-except` : le helper utilise des NTA natives via
`rndc nta`, d’une semaine, renouvelées chaque jour et au démarrage/reload.
Seuls les suffixes marqués et possédés sont retirés ou renouvelés. Les NTA
manuelles ne sont pas revendiquées. Le helper revalide la politique sous verrou,
protège son journal root et synchronise les écritures atomiques.

Quand le mode global est `false`, les champs par zone sont ignorés pour le
déploiement, aucun marqueur NTA n’est rendu et le helper n’est pas appelé.
Le journal `/var/lib/Bind9-Ansible/dnssec-owned.json` préexistant est conservé
pour une réactivation ; il ne réactive pas la validation. Aucun helper ni
répertoire d’état privé n’est créé sur une cible neuve désactivée.

## Runtime indépendant et bascules

`bind9-runtime.conf` conserve le lancement natif IPv4/IPv6 et le reload RNDC dans tous les modes. L’écoute DNS IPv6 est fermée uniquement dans `named.conf` ; voir [TRANSPORT_DNS.md](TRANSPORT_DNS.md).
Le canal RNDC local et sa clé native servent aussi à l’administration BIND ;
ce ne sont pas un déploiement DNSSEC. Les modes actifs ajoutent seulement le
drop-in lexicalement ultérieur `bind9-z-dnssec.conf`, qui applique les exceptions
après démarrage et après le reload RNDC. L’ancien drop-in combiné
`bind9-dnssec.conf` est retiré pendant la migration.

En sortie vers `false`, les unités privées existantes sont arrêtées, le timer
désactivé, les hooks/unités retirés, les définitions systemd rechargées puis
le helper retiré. Le handler redémarre named lorsqu’un drop-in change ; cela
peut interrompre brièvement le service. La validation et les zones sont
contrôlées avant application et santé.

Avant mutation, les six assets runtime sont capturés avec leurs droits et le
précédent état du timer. Le rescue restaure ces fichiers, retire les nouveaux,
restaure la sauvegarde BIND puis redémarre l’état précédent. La capture est en
mémoire Ansible ; l’arrêt du contrôleur exige une reprise depuis vos sauvegardes
persistantes. Détails : [OPERATIONS.md](OPERATIONS.md).

## Preuves historiques et limites

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
[VALIDATION_RHEL8.md](VALIDATION_RHEL8.md). Qualification de production
**NON TERMINÉE**. La racine privée et NXDOMAIN hors périmètre sont conservés ;
les essais ne démontrent pas une résolution Internet générale.


## Compatibilité et migration

Le contrôleur Ansible 2.9 peut employer Jinja 2.10.3. Les tests Jinja
`boolean` et `integer`, apparus en 2.11, ne doivent donc pas être introduits.
Le Python 3.6.8 du serveur ne détermine pas la version Jinja du contrôleur.
Un booléen YAML `false` est une entrée valide ; les anciennes variables
intermédiaires forcées par `-e` ne doivent pas piloter le mode effectif.
Le message historique sur les faits dérivés ne suffisait pas à prouver une
surcharge dans l’environnement de l’opérateur.

Remplacer les anciennes entrées `bind_dnssec_enabled` et
`bind_dnssec_validation` par `bind_dnssec_active` ; aucun alias n’est ajouté.
Depuis le changement de défaut du 17 octobre 2026, un inventaire sans
surcharge active le mode `auto` au déploiement suivant. Pour rester en mode
désactivé, définir explicitement le booléen `false` et utiliser le parcours
de déploiement complet. Le mode `auto` ne signe pas les zones autoritatives.

Avant une bascule, sauvegarder BIND et les fichiers runtime, contrôler les
surcharges, l’horloge, les ancres de distribution, RNDC et les forwarders.
Déployer d’abord sur une paire de recette, primaire puis secondaire, puis
effectuer un second passage et les tests de restauration. Copier uniquement
`named.conf` n’applique pas le cycle de vie des exceptions privées.

## Consolidation des fichiers — 2026.10.19-dnssec.1

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

### Contrôles reproductibles

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

### Recette système et retour de version

**NON EXÉCUTÉ** : vos serveurs RHEL 8 ne sont pas accessibles. Prérequis :
paire de recette représentative, sauvegardes exportées et vérifiées de BIND
et runtime, accès ADM de secours, ancres à jour et forwarders représentatifs.
Exécuter les modes `false`, `true`, `auto`, un second passage, les tests
[fonctionnels](TESTS_FONCTIONNELS.md), un reload puis un redémarrage contrôlé.
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
utiliser la restauration existante décrite dans [OPERATIONS.md](OPERATIONS.md),
avec sauvegardes persistantes en cas de perte du contrôleur.
