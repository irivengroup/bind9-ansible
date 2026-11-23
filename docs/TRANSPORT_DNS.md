# Transport natif BIND et écoute DNS

| Fonction | IPv4 | IPv6 |
|---|---|---|
| Requêtes DNS reçues sur les adresses FRONT/BACK/loopback déclarées | Oui | Non : `listen-on-v6 { none; };` |
| Échanges vers les forwarders | Oui | Oui, si le réseau IPv6 existant permet de les joindre |
| Données DNS A/AAAA et zones inverses | Oui | Oui, y compris réponses AAAA/PTR IPv6 envoyées aux clients IPv4 |
| Réplication entre les pairs BACK déclarés | Oui | Pas de pair IPv6 ajouté par ce rôle |
| Administration RNDC | 127.0.0.1:953 | Pas d’écoute RNDC IPv6 |
| Routes retour ajoutées pour les clients cloud | IPv4 uniquement | Aucune route ajoutée |

Le rôle conserve le `ExecStart` natif de `named.service`. Le drop-in commun
ne surcharge plus le lancement avec `-4` ; il configure seulement le reload
RNDC local. Le contrôle de l’écoute IPv6 appartient au template `named.conf`.
Le retrait de l’ancienne surcharge modifie le drop-in et déclenche un restart,
afin que le processus existant prenne réellement la nouvelle définition.

Les options externes dans `/etc/sysconfig/named` et les autres drop-ins restent
sous votre administration. Retirer toute option `-4` ou `-6` contradictoire
avant recette. Vérifier le service effectif :

```bash
systemctl cat named
systemctl show named -p ExecStart
ss -lnutp 'sport = :53'
dig -4 @127.0.0.1 localhost AAAA +short
dig -6 @::1 localhost AAAA +time=2 +tries=1
```

Attendu : écoute DNS IPv4 de named, aucune écoute DNS IPv6, réponse `::1`
au test AAAA via IPv4 ; le dernier test IPv6 échoue faute d’écoute. Vérifier
les lignes appartenant au processus named si un autre résolveur est présent.
La présence d’un enregistrement AAAA ne signifie pas qu’un socket IPv6 écoute.

## Forwarders

`bind_forward_zones[].forwarders[].address` accepte une adresse littérale
unicast IPv4 ou IPv6. Les noms d’hôte, adresses non spécifiées, multicast,
link-local/scopées et IPv4 mappées dans IPv6 sont refusés. Les alias ambigus
ne doivent pas contourner les contrôles de boucles locales. Le port conserve
son contrat : entier ou chaîne décimale de 1 à 65535, jamais un booléen.
Les délégations privées utilisent A pour IPv4 et AAAA pour IPv6.

```yaml
bind_dnssec_active: auto
bind_forward_zones:
  - domain: corp.example.test
    policy: only
    dnssec_validate: false
    forwarders:
      - address: 192.0.2.53
        port: 53
      - address: 2001:db8::53
        port: 53
```

Adresses de documentation uniquement : remplacer par des endpoints réellement
servis. Cet exemple ne promet pas un ordre strict entre les forwarders ; BIND
gère sa sélection. Le réseau, les routes IPv6 sortantes et le pare-feu existent
déjà et restent sous votre gestion. Aucun réglage IPv6 système n’est modifié.
Les listes clientes et adresses FRONT/BACK restent IPv4 puisque l’écoute DNS
IPv6 est fermée. Le mode DNSSEC n’affecte pas ce choix de transport.

## Preuves et limites

**EXÉCUTÉ** : BIND EL8 9.11.36 et BIND 9.18.24, sans option de famille forcée.
Un client IPv4 reçoit une réponse A puis AAAA via un upstream uniquement IPv6.
Une réponse TXT supérieure à la limite UDP force le repli TCP IPv6, confirmé
par le journal du vrai upstream. Les requêtes entrantes IPv6 UDP et TCP vers
le résolveur échouent. Les fichiers natifs restent inchangés.
Les nombres exacts figurent dans `DELIVERY.json`.

**NON EXÉCUTÉ** : service natif et réseau de vos cibles RHEL 8. Prérequis :
paire de recette sauvegardée, IPv6 sortant opérationnel si utilisé, forwarder
de recette IPv6 avec enregistrements connus et options systemd cohérentes.
Appliquer les commandes ci-dessus, interroger en IPv4 une donnée servie par
le forwarder IPv6, puis contrôler un RRset forçant le repli TCP et les journaux.
Attendu : réponses exactes, trafic sortant IPv6 autorisé, aucun listener DNS
IPv6. Risques : réseau/pare-feu cible, option externe de famille, confinement
SELinux et ancres réelles. Qualification de production **NON TERMINÉE**.
