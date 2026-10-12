# Vérification et corrections — 2026-10-12

- Suite de tests Python : 101 réussis.
- YAML : chargement de tous les fichiers `.yml` réussi.
- Script de migration : `bash -n` réussi.
- Scripts Python : compilation réussie.
- Générateur PTR : validation stricte des masques et des structures d'entrée.
- Générateur PTR : refus explicite des zones inverses automatiques imbriquées sans délégation.
- Les masques IPv4 non alignés restent refusés (RFC 2317 non implémentée).

## Limites

`ansible-playbook`, `named-checkconf`, `named-checkzone` et un environnement RHEL 8
ne sont pas disponibles ici. Aucun test de déploiement, transfert TSIG, restauration
réelle ou intégration BIND n'est revendiqué. Les zones inverses ne doivent être
publiées que sur des espaces DNS dont l'autorité est effectivement déléguée.
