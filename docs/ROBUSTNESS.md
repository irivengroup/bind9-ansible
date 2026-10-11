# Robustesse du rôle Bind9Project

- Déploiement incrémental : aucun nettoyage global de `/etc/named` ou `/var/named`.
- Contrôle précoce des chemins gérés (liens symboliques refusés).
- Conservation des permissions RPM du répertoire parent `/var/named`.
- Sauvegarde de référence vérifiée à chaque exécution et sauvegardes de fichiers modifiés par `template: backup: true`.
- Vérification syntaxique globale `named-checkconf -z` et contrôle individuel des zones primaires `named-checkzone`.
- Contrôle du SOA autoritaire du primaire avant tout déploiement secondaire.

## Limites et précautions

- Les mises à jour de plusieurs fichiers ne sont pas atomiques. En cas d'échec, restaurer les sauvegardes de fichiers et relancer la validation avant redémarrage.
- Les fichiers historiques non gérés restent en place. Les inclusions et la configuration effective doivent être contrôlées.
- Le rôle n'est pas une garantie de zéro interruption; prévoir une recette RHEL 8 avec BIND, SELinux, firewalld et AXFR/IXFR.
- Les clés TSIG doivent être stockées dans Ansible Vault; les fichiers secrets sont protégés en 0640.
