# Demandes de configuration des interfaces et des ressources BIND

Source : demandes explicites de l'utilisateur dans cette conversation. Transcription de périmètre établie le 23 novembre 2026 ; aucun numéro de version d'un CDC formel n'a été fourni.

## Exigences traitées

| ID | Demande et critère observable |
|---|---|
| IF-01 | Mettre à jour tous les fichiers concernés par les déclarations d'interfaces uniques ou multiples dans les `host_vars`, pour des paires ISO. Conserver les valeurs actives des inventaires et fournir des paires d'exemples cohérentes. |
| MG-01 | Renommer la variable de requête `bind_manage` en `bind_resources_manage` dans les points d'entrée, exemples, documentation et tests. Les variables auxiliaires de checksum et d'affichage ne sont pas la requête et conservent leur nom. |
| DP-01 | Remplacer `bind_forward_zones` et `bind_authoritative_zones` par une seule déclaration `bind_resources_deploy`. Les anciens noms sont refusés explicitement, sans alias silencieux. |
| DP-02 | Reprendre la structure des requêtes `bind_resources_manage` pour les ressources prises en charge : options, ACL, journalisation, zones forward/autorité et enregistrements. Le déploiement construit un état neuf précis ; la gestion conserve ses phases d'approbation sur l'état existant. |
| DP-03 | Accepter le lot fourni par l'utilisateur : enveloppe `resource: batch`, action de déploiement `create`, opérations de zones puis de RRsets, valeurs A multiples, TXT avec guillemets, TTL, serial et forwarding `only`. Conserver également les objets de déclaration de zone imbriqués déjà pris en charge par la gestion. |
| DP-04 | Maintenir l'idempotence de la compilation et refuser les ressources ou RRsets en doublon, y compris les alias numériques DNS. Une seconde compilation et une modification du résultat retourné ne doivent pas modifier la requête détenue. |
| DP-05 | Préserver les contrôles des options/ACL/zones protégées, les signatures, la validation native, les transferts authentifiés et le rollback du déploiement complet en cas d'échec. Lire les trois liens natifs RPM déjà acceptés par le rôle, avec contrôles de propriétaire/permissions/inodes et refus des écritures sur ces fichiers. Aucun élargissement de permissions des serveurs ni relâchement d'AppArmor. |
| DP-06 | Migrer les six configurations de site sans perte de suffixe, forwarding, serial, TTL, enregistrement ou adresse amont. Vérifier que le point d'entrée public consomme réellement la nouvelle variable. |
| QA-01 | Conserver le seuil existant de 99,99 % avant arrondi sur les instructions, branches, fonctions, lignes et conditions booléennes, globalement et pour chaque composant et profil obligatoire. Toute preuve manquante ou échouée reste bloquante. |
| QA-02 | Inventorier le nouveau module privé comme une action Ansible exécutable ; le rendu de ses paramètres ne doit pas couvrir son dispatch. Conserver les observations distinctes de ses cinq métriques Python. |
| PB-01 | Publier les sources et les preuves à la racine de `D:\PROJECTS\LABS\dnszibo`, avec contrôle de l'archive exacte et sauvegarde permettant de revenir aux fichiers précédents. Aucun nouveau push GitHub n'est autorisé par cette demande locale. |

## Invariants conservés

Les clients internes suivent les routes existantes ; les routes FRONT restent optionnelles et limitées aux clients cloud. Une interface unique n'exige aucune route supplémentaire. Le rôle conserve son garde RHEL de version majeure supérieure ou égale à 7 et ses prérequis de runtime. Les hôtes utilisateur sont fictifs : les laboratoires Docker/VM gardent leur identité réelle, sans certification de serveurs officiels ou futurs non exécutés.

Les limites de lot communes restent de 64 opérations et 256 Kio. L'état désiré vide se déclare explicitement par un lot de déploiement vide. Les lectures et listings relèvent de la gestion ; les écritures de gestion conservent les checksums d'état et de plan. L'évolution ne migre aucune production et ne réécrit pas automatiquement un inventaire après une opération de gestion.

## Preuves et interprétation

Le rapport externe de livraison doit relier chaque exigence aux sources, tests et preuves réellement exécutés. Les références du 20 novembre et des livraisons antérieures sont historiques. Elles ne remplacent pas les mesures de cette révision. Les contrôles de paquet et l'identité de l'archive complètent les observations de code ; ils ne sont pas une mesure de couverture.
