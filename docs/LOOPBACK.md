# Loopback et zones natives EL8

Le template canonique écoute sur `local_loopback` (`127.0.0.1`) et les adresses FRONT/BACK locales. `/etc/named.rfc1912.zones` est inclus au niveau global, sans vues. Le rôle vérifie ce fichier ainsi que `/var/named/named.localhost`, `named.loopback`, `named.empty`, sans les réécrire ni accepter des liens symboliques pour ces quatre fichiers.

Le fichier natif EL8 inspecté déclare `localhost.localdomain`, `localhost`, la zone inverse IPv6 de `::1`, `1.0.0.127.in-addr.arpa` et `0.in-addr.arpa`. `empty-zones-enable yes` conserve aussi les zones vides automatiques BIND. L'écoute IPv6 est désactivée ; les données natives AAAA `::1` et PTR IPv6 restent consultables via IPv4. Le rôle ne modifie pas `/etc/resolv.conf`.

La récursion locale permet les suffixes forwardés ; les autres noms suivent la racine privée NXDOMAIN, sauf les zones natives et autoritatives plus spécifiques. Les zones natives sont visibles à toutes les sources admises par `allow-query`, selon le modèle global sans vues. Les fichiers de données officiels ne sont pas personnalisés.

```bash
sudo named-checkconf -z /etc/named.conf
dig -4 -b 127.0.0.1 @127.0.0.1 localhost A +norecurse
dig -4 -b 127.0.0.1 @127.0.0.1 localhost A +tcp +norecurse
dig -4 -b 127.0.0.1 @127.0.0.1 1.0.0.127.in-addr.arpa PTR +norecurse
dig -4 -b 127.0.0.1 @127.0.0.1 1.0.0.127.in-addr.arpa PTR +tcp +norecurse
dig -4 -b 127.0.0.1 @127.0.0.1 localhost AAAA +norecurse
```

Attendu : NOERROR, AA, A `127.0.0.1`, PTR `localhost.` et AAAA `::1`. Le rôle contrôle automatiquement les quatre premières réponses locales, les SOA exacts des zones métier et les SOA forwardés en UDP/TCP. Le moniteur facultatif exécute ces contrôles via son timer systemd ; demander `bind_monitoring_enabled: true` et le tag `monitor`.

**EXÉCUTÉ** : laboratoire BIND EL8 9.11.36 et BIND 9.18.24, chacun 109 configurations et 270 contrôles DNS, avec fichiers natifs inchangés. **NON EXÉCUTÉ** : recette sur votre système RHEL réel ; raisons, procédure et risques dans [VALIDATION_RHEL8.md](VALIDATION_RHEL8.md). Les modes DNSSEC `false`/`true`/`auto`, le canal RNDC uniquement IPv4 et les exceptions privées optionnelles sont décrits dans [DNSSEC.md](DNSSEC.md).
