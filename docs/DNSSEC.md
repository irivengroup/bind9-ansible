# Validation DNSSEC optionnelle

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
sans fait Ansible surchargeable. Voir [DURCISSEMENT_DNSSEC.md](DURCISSEMENT_DNSSEC.md).

Le défaut est désormais `auto` : sans surcharge, le rôle active la validation
et ses composants associés. Une valeur explicite `false` conserve la désactivation.
L’entrée publique reste `bind_dnssec_active`. Avant mise à niveau, vérifier
les ancres natives et les exceptions des zones privées ; voir
[DEFAUT_DNSSEC_AUTO.md](DEFAUT_DNSSEC_AUTO.md).

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

## Contrôles et limites

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
