# Recette fonctionnelle DNS, réseau et accès

[Documentation](../../index.md) › [Qualification et contrôles](index.md) › Recette fonctionnelle DNS, réseau et accès

[Retour au Parent](index.md)

## Sommaire

- [Tests de fonctionnement](#tests-de-fonctionnement)
- [Loopback et zones natives](#loopback-et-zones-natives)
- [Autorité, inverse et convergence](#autorité-inverse-et-convergence)
- [Deux listes clientes et absence de routes internes](#deux-listes-clientes-et-absence-de-routes-internes)
- [Contrôler les modes DNSSEC](#contrôler-les-modes-dnssec)

## Tests de fonctionnement

Les commandes utilisent l’exemple `site1` et doivent suivre votre inventaire. Examiner **statut DNS, drapeaux et données** ; timeout et sortie vide ne prouvent jamais un refus de sécurité.

### Sur chaque serveur

```bash
sudo named-checkconf -z /etc/named.conf
sudo systemctl is-active named
sudo rndc status
sudo ss -lnutp

dig -b 127.0.0.1 @127.0.0.1 localhost A +norecurse +notcp +time=3 +tries=1
dig -b 127.0.0.1 @127.0.0.1 localhost A +norecurse +tcp +time=3 +tries=1
dig -b 127.0.0.1 @127.0.0.1 -x 127.0.0.1 +norecurse +notcp +time=3 +tries=1
dig -b 127.0.0.1 @127.0.0.1 -x 127.0.0.1 +norecurse +tcp +time=3 +tries=1

dig @127.0.0.1 corp.site1.prod.example.test SOA +notcp +time=3 +tries=1
dig @127.0.0.1 corp.site1.prod.example.test SOA +tcp +time=3 +tries=1
dig @127.0.0.1 apps.site1.prod.example.test SOA +notcp +time=3 +tries=1
dig @127.0.0.1 apps.site1.prod.example.test SOA +tcp +time=3 +tries=1
dig @127.0.0.1 nom-hors-perimetre.invalid A +time=3 +tries=1
```

Attendus : configuration acceptée, zones chargées, service `active`, RNDC opérationnel. Écoute DNS IPv4 loopback/FRONT/BACK seulement, RNDC sur loopback seulement. Localhost : `NOERROR`, drapeau `aa`, A `127.0.0.1`, PTR `localhost.`. Chaque forward doit retourner le SOA de son suffixe avec `NOERROR` ; `aa` n’est pas exigé pour ces réponses récursives. Le nom hors périmètre doit retourner `NXDOMAIN`. Une résolution Internet générale n’est pas un critère de succès de cette architecture.

Sur le primaire de l’exemple :

```bash
sudo named-checkzone site1.prod.example.test \
  /var/named/zones/site1.prod.example.test.zone
```

Le parcours `deploy` contrôle aussi les inverses et compare les SOA aux serials réellement réconciliés du primaire.

### Depuis les clients

Exemple FRONT, puis comparaison BACK depuis un poste autorisé à joindre ces adresses :

```bash
dig @192.0.2.11 app.site1.prod.example.test A +notcp +time=3 +tries=1
dig @192.0.2.12 app.site1.prod.example.test A +tcp +time=3 +tries=1
dig @10.20.1.11 site1.prod.example.test SOA +norecurse +time=3 +tries=1
dig @10.20.1.12 site1.prod.example.test SOA +norecurse +time=3 +tries=1
```

Attendus : `NOERROR`, `aa` pour les données autoritatives, A `192.0.2.101` et même serial attendu entre pairs. Tester aussi les inverses, chaque type utilisé et chaque forward depuis les segments clients. La récursion sur BACK exige une liste interne déclarée ; un SOA autoritatif ne prouve pas l’accès au cache récursif.

Depuis une source non autorisée pouvant atteindre le service, obtenir un refus DNS explicite ou le blocage prévu par la politique réseau ; distinguer ces niveaux de preuve. Tester UDP et TCP, et l’absence d’écoute DNS sur ADM.

### Réplication et refus AXFR

Comparer les SOA BACK, lire `xfer.log`, puis réaliser le transfert autorisé avec source BACK secondaire et fichier de clé BIND protégé. Ne jamais copier le secret dans la ligne de commande.

L’outil ci-dessous teste depuis un **client FRONT** un AXFR non signé puis signé. Il exige `dig`, une IP source réellement portée par ce client et une clé au format BIND :

```text
bind-configtool check-front-axfr FRONT_DNS_IP ZONE CLIENT_SOURCE_IP ROOT_ONLY_TSIG_KEYFILE
```

Les arguments représentent vos valeurs réelles ; le dernier fichier contient un bloc de clé BIND, pas le fichier Base64 brut du contrôleur. L’exécutable installé sur le serveur est réservé à root ; préparer outil et clé sur un client de recette contrôlé et retirer les copies privées selon votre politique.

| Code | Interprétation |
|---|---|
| `0` | Refus DNS explicite `REFUSED` ou `NOTAUTH` dans les deux essais. |
| `1` | Zone transférée : anomalie bloquante pour ce client externe. |
| `2` | Usage incorrect ou contrôle incomplet/ambigu, erreur réseau ou TSIG ; preuve complémentaire requise. |

`Transfer failed` seul, `BADKEY`/`BADSIG`/`BADTIME`, une signature absente ou un timeout ne prouvent pas le refus attendu. Toutes les versions utilisent le format texte natif `+noall +comments +answer` : le format YAML de dig récent masque l'avertissement d'une réponse non signée à une question signée. Un avertissement natif d'échec de vérification rend le contrôle inconclusif. Tout diagnostic du client sur stderr le rend également inconclusif : `dig` peut ignorer une clé illisible ou invalide, envoyer une question non signée et retourner le code 0. Chaque essai exige un seul en-tête de réponse complet, non tronqué, avec le statut de refus dans son champ natif ; une mention dans une note ou une donnée ne suffit pas. La zone est transmise par `-q`, sans interprétation comme option ou serveur. Chaque requête a un délai de 15 secondes et une limite de sortie de 1 Mio ; dépassement, octet nul, erreur du producteur ou du nettoyage rendent le contrôle inconclusif. Les contenus bruts des réponses et diagnostics DNS ne sont pas repris dans les erreurs. Le secondaire BACK authentifié est autorisé même vers FRONT : ne pas le prendre comme client externe pour ce test.

### Firewalld et routes

```bash
sudo firewall-cmd --state
sudo firewall-cmd --get-active-zones
sudo firewall-cmd --permanent --list-all-zones
ip -4 route show table main
```

Pour chaque zone FRONT/BACK relevée, passer son nom réel à `--zone` et interroger `--query-service=dns` en runtime puis avec `--permanent` : attendu `yes`, retour `0`. Si les routes sont activées, vérifier leur profil et chemin effectif. Exemple sur le primaire :

```bash
ip -4 route get 198.51.100.10 from 192.0.2.11
```

Attendu : sortie FRONT et passerelle prévue, sans changement des chemins ADM/BACK ou de la route par défaut. Vérifier la permanence après redémarrage en recette. Procédures complémentaires : [TESTS_FONCTIONNELS.md](TESTS_FONCTIONNELS.md), [VALIDATION_RHEL8.md](VALIDATION_RHEL8.md).



## Loopback et zones natives

Les tests qui simulent un fichier de politique BIND absent utilisent une image de mesure jetable, sans démon BIND. Ils réservent uniquement `/etc/crypto-policies/back-ends/bind.config`. Un fichier ou un lien déjà présent à cet emplacement fait échouer la préparation. Les autres politiques cryptographiques et les répertoires fournis par Rocky sont conservés. Les images des contrôles avec le véritable analyseur BIND et celles de la recette des services utilisent le lien fourni par la distribution.

```bash
dig -4 -b 127.0.0.1 @127.0.0.1 localhost A +norecurse
dig -4 -b 127.0.0.1 @127.0.0.1 localhost A +tcp +norecurse
dig -4 -b 127.0.0.1 @127.0.0.1 1.0.0.127.in-addr.arpa PTR +norecurse
dig -4 -b 127.0.0.1 @127.0.0.1 1.0.0.127.in-addr.arpa PTR +tcp +norecurse
dig -4 -b 127.0.0.1 @127.0.0.1 localhost AAAA +norecurse
dig -4 -b 127.0.0.1 @127.0.0.1 localhost.localdomain SOA +norecurse
dig -4 -b 127.0.0.1 @127.0.0.1 0.in-addr.arpa SOA +norecurse
```

Attendu : NOERROR/AA ; A 127.0.0.1, PTR localhost., AAAA ::1. Les données IPv6 natives ne signifient pas une écoute IPv6.



## Autorité, inverse et convergence

Depuis un client FRONT autorisé de l'exemple (198.51.100.0/24) :

```bash
dig -4 @192.0.2.11 app.site1.prod.example.test A +norecurse
dig -4 @192.0.2.12 app.site1.prod.example.test A +tcp +norecurse
dig -4 @192.0.2.11 -x 192.0.2.101 +norecurse
dig -4 @192.0.2.12 -x 192.0.2.101 +tcp +norecurse
dig -4 @192.0.2.11 site1.prod.example.test SOA +norecurse
dig -4 @192.0.2.12 site1.prod.example.test SOA +tcp +norecurse
```

Attendu : NOERROR/AA, A 192.0.2.101 et PTR app.site1.prod.example.test. ; SOA identiques au serial attendu dans l'inventaire après convergence. Pour vérifier BACK, depuis le primaire avec sa source BACK réelle :

```bash
dig -4 -b 10.20.1.11 @10.20.1.11 site1.prod.example.test SOA +norecurse
dig -4 -b 10.20.1.11 @10.20.1.12 site1.prod.example.test SOA +tcp +norecurse
```

Après modification d'un record et incrément du serial, déployer la paire, vérifier la donnée sur les deux serveurs et consulter les journaux NOTIFY/transfert. Attendu : secondaire convergé ; aucun transfert non signé. Ces essais ne prouvent pas séparément l'usage d'IXFR plutôt qu'AXFR.



## Deux listes clientes et absence de routes internes

Depuis une IP déclarée dans `bind_trusted_networks.cloud_clients`, interroger une zone forwardée
sur FRONT en UDP puis TCP. Depuis une IP de `bind_trusted_networks.internal_clients`, faire le
même contrôle sur BACK. Attendu : la donnée du forwarder sur les deux pairs.
Les IP sources doivent réellement appartenir aux machines de recette.

Tester successivement cloud seul, internes seuls, puis les deux. Une liste vide
est admise, les deux vides doivent faire échouer Ansible avant modification.
Avec `--tags routes`, seuls les préfixes cloud manquants peuvent être ajoutés.
Un déploiement avec internes seuls ne doit pas consulter/modifier NetworkManager.
Relever les routes/profils avant et après ; appliquer la procédure de
[FRONT_ROUTES.md](../configuration/reseau/FRONT_ROUTES.md). Sans vues, la fusion des autorisations
ne fournit pas d’isolation stricte entre catégories et interfaces ; vérifier
également le filtrage réseau existant si cette isolation est requise.


Pour `false`, attendu : `dnssec-validation no`, aucun hook privé dans `systemctl cat named`, aucun helper ni unité de renouvellement gérés déployés. En sortie d’un ancien mode actif, le timer doit être arrêté/désactivé. Une signature invalide n’est plus rejetée par DNSSEC. Le contrôle des ACL et TSIG reste applicable. Voir [DNSSEC.md](../configuration/bind/DNSSEC.md).


## Contrôler les modes DNSSEC

DNSSEC est activé en mode `auto` par défaut. RNDC reste administratif dans tous les modes ; les commandes NTA/timer suivantes s’appliquent uniquement à `true`/`auto` :

```bash
sudo rndc -s 127.0.0.1 -k /etc/rndc.key status
sudo rndc -s 127.0.0.1 -k /etc/rndc.key nta -dump
sudo systemctl status named-private-dnssec.timer --no-pager
sudo journalctl -u named-private-dnssec.service --since '-1 day' --no-pager
sudo systemctl cat named.service
```

Avec `true`/`auto`, attendu : seulement les suffixes privés explicitement déclarés dans les NTA appartenant au rôle, pas de NTA racine globale ; timer actif et aucune erreur. Les NTA manuelles éventuelles ne sont pas revendiquées/supprimées par le rôle. Pour une zone stricte de recette dont la chaîne est connue et accessible, contrôler une donnée signée valide (NOERROR/AD), puis une signature corrompue maîtrisée (SERVFAIL) ; ne pas altérer une zone de production. Un suffixe privé exempté n'a pas de garantie DNSSEC et ne doit pas être annoncé comme validé. Les données unsigned peuvent être légitimement insecure ; unsigned ne signifie pas systématiquement bogus. Procédure et contraintes de racine privée : [DNSSEC.md](../configuration/bind/DNSSEC.md).

---

Documentation DNSZIBO · contenu harmonisé le 25 novembre 2026.
