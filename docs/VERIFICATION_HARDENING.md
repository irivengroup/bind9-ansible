# Passe de vérification et de durcissement — Bind9-Ansible V4

## Modifications

- Suppression idempotente de `/etc/named/20-acl.conf` lorsque les deux listes de clients FRONT et BACK sont vides. Cette suppression est incluse dans la transaction de déploiement et ne supprime aucune autre configuration.
- Validation préalable des entrées ACL (chaînes CIDR non vides, refus des métacaractères de configuration BIND), avant leur injection dans les templates.
- Ajout de tests de non-régression pour la suppression conditionnelle de l'ACL et la validation des entrées.

## Contrôles et limites

- Tests Python du dépôt, parsing YAML, syntaxe Bash, compilation Python et intégrité ZIP vérifiés lors de la livraison.
- L'expression régulière ACL constitue un contrôle de forme, **pas une validation sémantique complète des adresses et préfixes CIDR**. À compléter avant production.
- Aucun test d'intégration réel Ansible / named-checkconf / named-checkzone / AXFR / restauration SELinux sur RHEL 8 n'a été exécuté dans cet environnement.
- **Point à arbitrer avant production :** la vue FRONT actuelle autorise la récursion pour ses clients. Une politique antérieure demandait l'absence de récursion sur FRONT ; les zones de forwarding de cette vue doivent être repensées avant de désactiver la récursion sans casser la résolution.
- Les tags `configure`, `zones` et `records` ne doivent pas être utilisés seuls pour un déploiement transactionnel. Préférer `--tags deploy`.

