# Exemple : deux zones forwardées et liste cliente unique

Rendu du template livré pour le primaire FRONT `192.0.2.11`, BACK `10.20.1.11` ; pair secondaire FRONT `192.0.2.12`, BACK `10.20.1.12`. La liste `bind_cloud_clients` contient `198.51.100.0/24` et `bind_internal_clients` vaut `[]`. La fusion calculée `bind_allowed_clients` alimente l’ACL cliente commune `allowed_clients`. La récursion BACK n’est donc pas activée dans cet exemple cloud seul.

- [variables.yml](variables.yml) déclare deux zones privées non signées, avec `dnssec_validate: false` explicite. Les deux listes d’entrée acceptent IP IPv4 et CIDR ; l’une peut être vide, jamais les deux. La fusion n’est pas une variable à déclarer.
- [named.conf](named.conf) est le rendu primaire ; [named-secondary.conf](named-secondary.conf) est le rendu secondaire. Chacun utilise ses propres ACL FRONT/BACK pour l'écoute et `allow-query-on`. Ils sont sans vues, DNSSEC désactivé et IPv4, avec les fichiers natifs inclus. La clé TSIG affichée est **publique, fictive et interdite en production** ; le rôle génère sa véritable clé.
- [empty-root.zone](empty-root.zone) conserve NXDOMAIN hors périmètre et les délégations des deux suffixes forwardés. Il n'y a aucune zone autoritative métier à transférer.

Ce sont des valeurs d'illustration, pas une configuration à copier telle quelle sur un serveur. Le parcours `deploy` fournit le canal RNDC administratif et le drop-in runtime IPv4 commun. Avec `bind_dnssec_enabled: false`, aucun helper, hook, stockage de clés géré par le rôle ni unité privée DNSSEC n’est installé. Les champs `dnssec_validate: false` des forwards restent déclaratifs et sont ignorés dans ce mode. Pour activer les exceptions, choisir `true` ou `auto` et utiliser le parcours complet ; copier seulement named.conf ne suffit pas.

Le rendu a été contrôlé avec `named-checkconf -z` EL8 dans un répertoire temporaire, avec chemins adaptés ; aucun déploiement cible de ces adresses n'est revendiqué. Voir [DNSSEC et limites](../../docs/DNSSEC.md), [README du projet](../../README.md) et [recette](../../docs/TESTS_FONCTIONNELS.md).
