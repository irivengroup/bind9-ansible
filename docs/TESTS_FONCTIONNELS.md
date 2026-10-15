# Commandes de recette fonctionnelle

Exécuter depuis la racine du projet sur le contrôleur Linux, puis sur les serveurs et clients indiqués. Les adresses ci-dessous sont celles de l'exemple **prod/site1** ; les remplacer par les valeurs réelles. Une commande `dig` terminée avec code 0 peut contenir REFUSED ou SERVFAIL : lire statut, AA, ANSWER et données.

## Contrôleur et serveur

```bash
ansible --version
ansible-inventory -i inventory/prod/hosts.ini --graph
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --syntax-check
ansible -i inventory/prod/hosts.ini dns_deployment_order -m ping
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --limit site1 --tags validate,health
```

Les tags `validate,health` supposent un déploiement déjà réalisé et des utilitaires BIND installés. Sur chaque serveur :

```bash
python3 --version
rpm -q bind bind-utils NetworkManager iproute
sudo named-checkconf -z /etc/named.conf
sudo systemctl is-active named
sudo ss -lnutp 'sport = :53'
sudo journalctl -u named --since '-10 minutes' --no-pager
```

Attendu : configuration et zones sans erreur, service actif, UDP/TCP sur 127.0.0.1 et ses FRONT/BACK locales, aucune écoute DNS IPv6. Le service impose `-4`. Ne pas publier une configuration complète contenant les secrets TSIG/RNDC. DNSSEC est désactivé par défaut. RNDC reste administratif dans tous les modes ; les commandes NTA/timer suivantes s’appliquent uniquement à `true`/`auto` :

```bash
sudo rndc -s 127.0.0.1 -k /etc/rndc.key status
sudo rndc -s 127.0.0.1 -k /etc/rndc.key nta -dump
sudo systemctl status named-private-dnssec.timer --no-pager
sudo journalctl -u named-private-dnssec.service --since '-1 day' --no-pager
sudo systemctl cat named.service
```

Avec `true`/`auto`, attendu : seulement les suffixes privés explicitement déclarés dans les NTA appartenant au rôle, pas de NTA racine globale ; timer actif et aucune erreur. Les NTA manuelles éventuelles ne sont pas revendiquées/supprimées par le rôle. Pour une zone stricte de recette dont la chaîne est connue et accessible, contrôler une donnée signée valide (NOERROR/AD), puis une signature corrompue maîtrisée (SERVFAIL) ; ne pas altérer une zone de production. Un suffixe privé exempté n'a pas de garantie DNSSEC et ne doit pas être annoncé comme validé. Les données unsigned peuvent être légitimement insecure ; unsigned ne signifie pas systématiquement bogus. Procédure et contraintes de racine privée : [DNSSEC.md](DNSSEC.md).

## Loopback et zones natives

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

## Forwarding et racine privée

Sur chaque serveur :

```bash
dig -4 -b 127.0.0.1 @127.0.0.1 corp.site1.prod.example.test SOA +recurse
dig -4 -b 127.0.0.1 @127.0.0.1 corp.site1.prod.example.test SOA +tcp +recurse
dig -4 -b 127.0.0.1 @127.0.0.1 hors-perimetre.invalid A +recurse
```

Attendu : SOA du forwarder déclaré en UDP/TCP ; NXDOMAIN pour le nom hors périmètre. Adapter le suffixe forwardé à une zone réellement servie. Un forwarder indisponible pour une requête non mise en cache doit produire SERVFAIL, sans résolution Internet de secours ; ne pas couper un upstream de production pour ce test.

## Contrôles d'accès

Depuis une source absente de la liste et des ACL de pairs : une requête de zone métier doit être REFUSED. Depuis un client FRONT autorisé, vérifier les transferts non signés et signés avec l'outil, sans mettre le secret dans les arguments :

```bash
bind-configtool check-front-axfr 192.0.2.11 \
  site1.prod.example.test 198.51.100.10 /chemin/protege/tsig.key
```

Cet outil doit être installé sur le client de recette avec dig. L'adresse source doit lui appartenir. Code 0 : refus explicite des deux essais ; 1 : transfert inattendu ; 2 : contrôle incomplet/ambigu, notamment si l'ancien dig ne rend pas le RCODE AXFR. Un échec réseau ou `Transfer failed` seul ne suffit pas. Le secondaire BACK authentifié est autorisé, y compris vers FRONT ; ne pas utiliser ce pair pour tester le refus d'un client externe. Détail : [ACL_SERVEURS.md](ACL_SERVEURS.md).

## Routes, reprise et décision de mise en production

Les routes sont désactivées par défaut. Leur activation, simulation, contrôle du chemin `ip -4 route get CLIENT from FRONT_IP`, rollback et persistance sont décrits dans [FRONT_ROUTES.md](FRONT_ROUTES.md). La recette doit préserver la session SSH, les routes par défaut et les profils existants. Le second déploiement doit conserver la donnée et ne pas ajouter de route inutile ; une sauvegarde forcée est attendue à chaque deploy.

Arrêter la mise en production en cas de syntaxe invalide, service inactif, mauvais statut/réponse, SOA divergents, transfert client réussi, résultat de sécurité inconclusif ou mutation réseau inattendue. Restaurer la sauvegarde préalable selon [OPERATIONS.md](OPERATIONS.md). Les critères et validations manquantes sont dans [VALIDATION_RHEL8.md](VALIDATION_RHEL8.md).


## Deux listes clientes et absence de routes internes

Depuis une IP déclarée dans `bind_cloud_clients`, interroger une zone forwardée
sur FRONT en UDP puis TCP. Depuis une IP de `bind_internal_clients`, faire le
même contrôle sur BACK. Attendu : la donnée du forwarder sur les deux pairs.
Les IP sources doivent réellement appartenir aux machines de recette.

Tester successivement cloud seul, internes seuls, puis les deux. Une liste vide
est admise, les deux vides doivent faire échouer Ansible avant modification.
Avec `--tags routes`, seuls les préfixes cloud manquants peuvent être ajoutés.
Un déploiement avec internes seuls ne doit pas consulter/modifier NetworkManager.
Relever les routes/profils avant et après ; appliquer la procédure de
[FRONT_ROUTES.md](FRONT_ROUTES.md). Sans vues, la fusion des autorisations
ne fournit pas d’isolation stricte entre catégories et interfaces ; vérifier
également le filtrage réseau existant si cette isolation est requise.


Pour `false`, attendu : `dnssec-validation no`, aucun hook privé dans `systemctl cat named`, aucun helper ni unité de renouvellement gérés déployés. En sortie d’un ancien mode actif, le timer doit être arrêté/désactivé. Une signature invalide n’est plus rejetée par DNSSEC. Le contrôle des ACL et TSIG reste applicable. Voir [DNSSEC.md](DNSSEC.md).
