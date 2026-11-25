# Validation DNSSEC optionnelle

[Documentation](../../../index.md) › [Configuration](../index.md) › [Configuration BIND](index.md) › Validation DNSSEC optionnelle

[Retour au Parent](index.md)

## Sommaire

- [Ancres et exceptions privées](#ancres-et-exceptions-privées)
- [Runtime indépendant et bascules](#runtime-indépendant-et-bascules)
- [Compatibilité et migration](#compatibilité-et-migration)
- [Basculer la validation DNSSEC](#basculer-la-validation-dnssec)
- [Journal et attentes bornées](#journal-et-attentes-bornées)
- [Suivre le renouvellement](#suivre-le-renouvellement)
- [Campagnes et traçabilité](#campagnes-et-traçabilité)

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

`bind9-runtime.conf` conserve le lancement natif IPv4/IPv6 et le reload RNDC dans tous les modes. L’écoute DNS IPv6 est fermée uniquement dans `named.conf` ; voir [TRANSPORT_DNS.md](../reseau/TRANSPORT_DNS.md).
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

La section [Basculer la validation DNSSEC](#basculer-la-validation-dnssec) décrit la capture des six composants et l’ordre des opérations. Le [manuel de reprise](../../exploitation/OPERATIONS.md#reprise) précise le retour à l’état initial du service et les sauvegardes persistantes nécessaires.

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

## Basculer la validation DNSSEC

Sauvegarder avant bascule les fichiers BIND et les six assets gérés :
`bind9-runtime.conf`, ancien `bind9-dnssec.conf`, `bind9-z-dnssec.conf` sous
`/etc/systemd/system/named.service.d`, le helper `/usr/local/sbin/bind-private-dnssec`
et les deux unités `/etc/systemd/system/named-private-dnssec.service` / `.timer`.
Conserver les fichiers existants, permissions et états du timer ; ne pas
inventer de fichiers absents. La transaction capture ces éléments en mémoire
Ansible avant mutation. Elle refuse les chemins non réguliers ou non root.

Avec `false`, arrêter/désactiver le timer et arrêter le service privé avant
de retirer hooks, unités et helper. Recharger les définitions systemd avant
de retirer l’exécutable ; conserver les ancres officielles et le journal
d’appartenance. Le drop-in commun conserve le lancement natif et le reload RNDC ; seule l’écoute DNS IPv6 est désactivée.

Si un échec déclenche le rescue, les unités privées sont arrêtées et leur timer
désactivé, puis named est arrêté. Les assets initiaux sont restaurés avec
leurs droits, les nouveaux fichiers absents à l’origine sont retirés, les
définitions sont rechargées et la sauvegarde BIND est restaurée. L’activité et l’activation initiales de named sont ensuite rétablies : un service auparavant actif redémarre, un service auparavant arrêté reste arrêté.
Le timer reprend uniquement s’il était actif avant déploiement, avec son
enablement initial. Un processus Ansible tué ne déclenche pas ce rescue :
utiliser les sauvegardes persistantes et la version/variables précédentes
pour une reprise opérateur ; inspecter les fichiers et `systemctl cat named`
avant redémarrage. Ne pas mélanger un ancien named.conf avec des hooks manquants.

Qualifier la séquence sur une paire de recette de la version RHEL ciblée (≥ 7), dont SELinux et systemd.
Les tests locaux substituent systemd et ne prouvent pas les redémarrages réels.
Le helper source reste livré dans le ZIP pour les modes activés, mais n’est
pas installé sur une cible neuve en mode `false`.




## Journal et attentes bornées

Le helper vérifie les octets du journal privé avant publication et avant toute mutation RNDC. Les champs JSON dupliqués sont refusés. L’attente de son verrou est limitée à 30 secondes par défaut ; le CLI expose `--lock-timeout`, qui exige un nombre fini positif ou nul. Une attente expirée échoue explicitement et préserve le journal.

<a id="consolidation-des-fichiers--20261019-dnssec1"></a>
## Suivre le renouvellement

Sur le serveur configuré en mode actif :

```bash
sudo systemctl status named-private-dnssec.timer --no-pager
sudo journalctl -u named-private-dnssec.service --since '-24 hours' --no-pager
sudo rndc nta -dump
```

## Campagnes et traçabilité

Les [campagnes DNSSEC](../../historique/CAMPAGNES_CONFIGURATION.md#dnssec-preuves) et la [consolidation de 2026](../../historique/CAMPAGNES_CONFIGURATION.md#dnssec-consolidation) conservent leurs sources et compteurs. Les critères et limites actuels sont dans la [qualification](../../qualification/QUALIFICATION_ACTUELLE.md).

---

Documentation DNSZIBO · contenu harmonisé le 25 novembre 2026.
