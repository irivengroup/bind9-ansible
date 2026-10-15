# Correction des contrôles DNS et vérification de la configuration

## Diagnostic reproduit

La livraison précédente utilisait `search('flags:.*\baa\b')` dans deux
conditions Ansible. Jinja interprète `\b` comme le caractère de retour arrière
`0x08`, avant l’évaluation de l’expression régulière. Sous Ansible 2.9.27,
Jinja 2.10.3 et 3.0.3, une réponse correcte avec `flags: qr aa;` était donc
refusée. Les quatre combinaisons A/PTR et UDP/TCP reproduisent ce défaut.
Le contrôle du SOA primaire avant le déploiement du secondaire était affecté
par la même cause.

Le motif devient `(?m)^;; flags: (?:[a-z]+ )*aa(?: |;)`. Il exige le jeton
`aa` dans les drapeaux de l’en-tête de `dig`, sans utiliser cet échappement.
Les critères restent : code de commande nul, statut `NOERROR`, réponse
autoritative, propriétaire/type/donnée attendus. Les tentatives et délais
restent inchangés. Une panne DNS réelle continue de provoquer un échec.

Le contrôle des transferts ouverts avait une autre limite : sa recherche
littérale ne reconnaissait pas le format canonique multiligne de
`named-checkconf -p`, qui écrit aussi `"any"` entre guillemets. Le motif
corrigé reconnaît ces formes d’ACL directe, tout en acceptant l’ACL imbriquée
qui exige à la fois la source BACK du secondaire et la clé TSIG du site.
Ce contrôle ciblé ne constitue pas un analyseur général de toutes les ACL
possibles dans une configuration BIND personnalisée.

Ces défauts ont été reproduits sur la livraison précédente. La sortie de
l’incident réel n’étant pas disponible, sa cause exacte sur le serveur
n’est pas confirmée.

## Parties du code impactées

| Fichier | Bloc et modification |
|---|---|
| `roles/bind9/tasks/tasks.d/health.yml` | `Verify native localhost forward and reverse answers over UDP and TCP` : correction des expressions AA dans `until` et `failed_when` ; commentaire de compatibilité Jinja |
| `roles/bind9/tasks/tasks.d/primary_readiness.yml` | `Verify primary SOA from secondary over back network` : même correction AA dans `failed_when` |
| `roles/bind9/tasks/tasks.d/verify.yml` | `Ensure no wildcard zone transfers are enabled` : reconnaissance des ACL directes `any` multiligne et entre guillemets |
| `tests/test_loopback_health_conditions.py` | 108 scénarios : réponses exactes, refus, sorties vides, erreurs de commande, faux jetons AA, SOA/serial, format canonique des ACL et analyse des constantes Jinja de toutes les conditions du rôle |
| `tests/integration_loopback.py` | Exécution des vraies tâches de santé, disponibilité primaire et vérification des fichiers avec BIND ; test de refus d’une configuration ouverte réellement formatée par BIND ; deux modes de typage Jinja |
| `README.md`, `docs/LOOPBACK.md`, `docs/VALIDATION_RHEL8.md` | Résultats actualisés, prérequis et lien vers ce rapport |
| `DELIVERY.json`, `SOURCE-MANIFEST.sha256` | Provenance, périmètre du diff, preuves et empreintes régénérés |

Les tests de régression sont découverts par le job pytest existant. Le
workflow CI n’a pas été modifié ni exécuté à distance. Le template
`named.conf.j2`, les contrats de variables, les routes et les scripts de
production ne sont pas modifiés par cette correction. Aucune dépendance
de production n’est ajoutée.

## Configuration vérifiée

Le parcours réel déploie `/etc/named.conf`, puis lance `named-checkconf -z`
sur ce fichier et `named-checkzone` sur les zones primaires avant
l’activation/recharge du service et les requêtes de santé. La sortie
canonique de `named-checkconf -p` et son assertion restent protégées par
`no_log`, car cette sortie contient la clé TSIG.

**EXÉCUTÉ** : les inventaires livrés et la matrice du template sont chargés
par les binaires BIND ; le laboratoire exécute aussi les tâches de
vérification sur les fichiers générés. Seuls les chemins, les adresses de
laboratoire et le port de requête sont adaptés. Le wrapper `dig` transmet
la requête au vrai serveur BIND ; aucune réponse DNS n’est simulée.
Les fichiers natifs `named.rfc1912.zones`, `named.localhost`,
`named.loopback` et `named.empty` restent inchangés.

Les essais couvrent les rôles primaire/secondaire, les listes cloud et
internes, DNSSEC `false`/`true`/`auto`, les zones autoritatives, la racine
privée NXDOMAIN, le forwarding, les refus de transfert, la réplication et
le transport IPv6 sortant avec absence d’écoute DNS IPv6. Les combinaisons
vides du template ne rendent pas valide un inventaire refusé par le
préflight : au moins une des deux listes de clients reste obligatoire.

## Résultats et limites

| Statut | Contrôle et résultat |
|---|---|
| **EXÉCUTÉ** | Ansible 2.9.27 / Jinja 2.10.3 : 447 tests contrôleur, 14 workflows de routes et 8 workflows DNSSEC, soit 469 tests distincts ; opérations de fichiers réelles, doubles CLI/systemd pour les workflows |
| **EXÉCUTÉ** | Ansible 2.9.27 / Jinja 3.0.3 : 447 tests contrôleur |
| **EXÉCUTÉ** | Trois régressions avant/après : quatre réponses loopback correctes et un SOA primaire précédemment refusés, ACL directe ouverte précédemment acceptée |
| **EXÉCUTÉ** | BIND natif EL8 9.11.36-16.el8_10.16 et BIND 9.18.24 : chacun 109 configurations et 282 requêtes DNS ; dont 8 requêtes loopback et 4 SOA par le vrai runner Ansible, dans les deux modes de typage Jinja |
| **EXÉCUTÉ** | Dans chaque laboratoire BIND : vraies tâches de vérification des fichiers et refus d’une ACL directe `any` produite par `named-checkconf -p` |
| **EXÉCUTÉ** | Syntaxe des trois inventaires et préflight sur 12 hôtes avec faits cibles simulés, sous les deux Jinja ; Pyflakes, syntaxe shell et inspection des références de sources |
| **NON EXÉCUTÉ** | Inspection de `/etc/named.conf` et du processus sur votre serveur : aucune connexion ni copie de ce fichier n’est disponible |
| **NON EXÉCUTÉ** | Déploiement et restauration système RHEL, SELinux, pare-feu, systemd réel, redémarrage et upstreams cibles ; procédures et risques dans [VALIDATION_RHEL8.md](VALIDATION_RHEL8.md) |

La qualification opérationnelle reste **NON TERMINÉE** tant que la recette
sur la paire cible n’a pas été effectuée. La couverture Python publiée
dans le rapport précédent porte sur des modules de production inchangés ;
elle n’est pas une mesure de couverture des tâches YAML de cette correction.

## Vérification du serveur effectivement déployé

**NON EXÉCUTÉ** ici. Prérequis : accès administrateur à chaque serveur de
recette RHEL, paquets BIND installés, clé RNDC native disponible, réseau de
recette et sauvegardes avant tout redéploiement. Ces commandes de lecture
ne publient pas la configuration contenant la clé TSIG :

```bash
sudo /usr/sbin/named-checkconf -z /etc/named.conf
sudo /usr/sbin/named-checkzone localhost /var/named/named.localhost
sudo /usr/sbin/named-checkzone 1.0.0.127.in-addr.arpa /var/named/named.loopback
sudo /usr/sbin/rndc -s 127.0.0.1 -k /etc/rndc.key status
systemctl is-active named
for transport in +notcp +tcp; do
  dig -4 -b 127.0.0.1 @127.0.0.1 "$transport" +time=3 +tries=1 +norecurse +noall +comments +answer localhost A
  dig -4 -b 127.0.0.1 @127.0.0.1 "$transport" +time=3 +tries=1 +norecurse +noall +comments +answer 1.0.0.127.in-addr.arpa PTR
done
sudo journalctl -u named --since '15 minutes ago' --no-pager
```

Attendu : chargement des zones sans erreur, service actif et canal RNDC
opérationnel, quatre réponses `NOERROR` avec drapeau `aa`, A `127.0.0.1`
et PTR `localhost.`. Effectuer ensuite les essais FRONT/BACK, forwarders,
transferts et SOA décrits dans [TESTS_FONCTIONNELS.md](TESTS_FONCTIONNELS.md).
Risques restants : dérive locale, service chargeant un autre fichier,
permissions/SELinux, réseau et données réelles des forwarders.

Pour reproduire les conditions sur le contrôleur Linux équipé d’Ansible :

```bash
python -m pytest -q tests/test_loopback_health_conditions.py
```

Pour une recette complète, suivre [VALIDATION_RHEL8.md](VALIDATION_RHEL8.md).
L’intégration loopback nécessite aussi `ansible-playbook` dans le PATH.
En cas d’échec réel après correction, conserver l’item de boucle, `rc`,
`stdout`, `stderr` et les journaux associés ; ne pas désactiver les contrôles.
