# Impacts et décisions — management ciblé

Rapport historique : le paramètre de déploiement courant est désormais `hibernate_deployment: no`. Voir [HIBERNATE_DEPLOYMENT.md](HIBERNATE_DEPLOYMENT.md) et [AUDIT_HIBERNATE_DEPLOYMENT.md](AUDIT_HIBERNATE_DEPLOYMENT.md) pour le contrat et les preuves actuels. Les résultats et anciens paramètres décrits ci-dessous restent propres à la livraison historique.

Historique de la livraison source. Les changements de la révision durcie sont détaillés dans [DURCISSEMENT_MANAGEMENT.md](DURCISSEMENT_MANAGEMENT.md).

Source immuable : `Bind9-Ansible-zones-vides-natives.zip`, SHA-256 `9c146b0c91755e5b416ea063b46e235365f099e17f04b789307024beb44c8f79`. Projet : **Bind9-Ansible**. Cette évolution concerne le cycle d’exploitation, la concurrence et la reprise ; elle ne remplace pas le modèle DNS déployé.

| Domaine | Modification et invariant protégé |
|---|---|
| Points d’entrée | `playbooks/bind9-deploy.yml` conservé ; ajout de `playbooks/bind9-management.yml`, séquentiel, une action par exécution. |
| Déploiement | `roles/bind9/tasks/main.yml` ajoute le garde/lease et appelle l’orchestration existante déplacée dans `tasks.d/deploy/main.yml`. Ordre et bloc de rollback existants conservés. |
| Sceau | `tasks.d/deploy/guard.yml`, `seal.yml`, `defaults/main.yml` et marqueur final dans `deploy/health.yml`. `force: false` ; reconstruction explicitement autorisée par `force=yes`. Aucun sceau après un échec ou un parcours partiel. |
| Réorganisation | 27 fichiers contextuels de `tasks.d/` déplacés dans `tasks.d/deploy/`, ainsi que l’orchestration précédente. Les imports et leurs consommateurs/tests/documents sont réalignés. Les 11 fichiers partagés restent à la racine. La liste exhaustive des déplacements figure dans `DELIVERY.json`. |
| Management | `tasks/manage.yml` et `tasks.d/manage/main.yml` appellent le module `library/bind9_manage.py`, avec la logique dans `module_utils/bind9_management.py`. Aucun appel au template global ou aux tâches de routage. |
| Données | Edition de clauses/RRsets ciblés ; SOA et délégations modifiés seulement lorsqu’ils sont nécessaires à l’action. Fichiers conservés par défaut au delete zone, purge explicite et sauvegardée. Pas de PTR implicite. |
| Contrats publics | Ajout de `force`, `bind_manage`, `bind_manage_checksums`, `bind_manage_show_values`. Les variables DNS/réseau existantes restent inchangées. Les chemins internes des tâches ont changé ; adapter les éventuels consommateurs externes `tasks_from`. |
| Configuration DNS | `templates/named.conf.j2`, templates de zones, exemples existants, scripts/filtres DNS et inventaires conservés octet pour octet. Défaut DNSSEC `auto`, écoute DNS IPv4 et capacités sortantes IPv4/IPv6 conservés. |
| Réseau | Algorithmes FRONT, passerelle, profils NetworkManager et rollback réseau conservés ; seuls les chemins d’import changent. Aucun changement réseau par management. |
| Sauvegarde/reprise | Outil `bind-configtool` existant réutilisé pour l’archive complète ; journal ciblé privé supplémentaire, restauration des seules cibles de l’opération et refus d’écraser un conflit manuel. |
| Exploitation | Documentation CRUD, migration depuis une livraison sans sceau, rétention des sauvegardes et reprise des leases interrompues dans `MANAGEMENT.md`. Le management ne met pas à jour l’inventaire. |
| Tests et CI | Tests adaptés aux déplacements sans retrait d’assertions métier ; nouveaux scénarios de checksum, CRUD, fichiers dangereux, champs incompatibles, signatures statiques, rollback et cache. Contrôle des deux playbooks et seuil de couverture 98 % du moteur ajouté à la CI. |
| Dépendances | Aucun paquet, collection, service ou bibliothèque Python de production ajouté. Le module utilise la bibliothèque standard Python 3.6+. Les outils BIND/RHEL déjà prérequis restent nécessaires. |

## Menaces et limites de protection

| Risque | Protection et limite |
|---|---|
| Injection de configuration/commande | Une requête typée, refus des champs étrangers à l’action, parsing de clauses, noms bornés, RDATA contrôlé et validation BIND. Arguments natifs séparés, sans shell ; les entrées ne choisissent pas les exécutables. |
| Ecrasement d’une intervention manuelle | SHA-256 du graphe statique, comparaison avant et juste avant publication, édition des seuls spans ciblés, conflit sur les délégations dérivées et comparaison après les contrôles de santé. |
| Ecritures concurrentes du projet | Lease commune par hôte. Aucun verrou n’est volé automatiquement. Un administrateur root externe doit respecter la fenêtre exclusive : le protocole ne rend pas les écritures root non coopératives impossibles. |
| Lien, FIFO, permissions ou parent dangereux | Lecture bornée et sans suivi des liens, fichiers root ordinaires à un seul lien, parents protégés ; seule exception contrôlée pour le lien crypto-policy natif. |
| Signature DNSSEC invalidée | Refus des modifications de données primaires déjà signées et des types d’enregistrement appartenant au signataire ; validation globale et runtime protégés du CRUD isolé. |
| Fausse santé par cache | Expiration ciblée du suffixe après modification de zone et lors de sa restauration ; vérification de réponses et SOA, au-delà du simple code de sortie `dig`. |
| Echec après renommage/rechargement | Suivi des cibles avant publication, sauvegardes privées, rollback conditionné aux empreintes, rechargement et réconciliation privée ; lease conservée si la reprise reste incertaine. |
| Crash multi-fichiers ou paire partiellement modifiée | Atomicité par fichier et par hôte seulement, journal de reprise, arrêt séquentiel au premier échec. Reprise manuelle nécessaire après interruption ; pas de transaction distribuée ni de bascule automatique entre pairs. |
| Fuite de secrets | Transport masqué, valeurs non affichées par défaut, sauvegardes root privées. Les checksums ne sont pas des signatures ; les sauvegardes contenant la configuration doivent rester protégées. |

Les limites de syntaxe, les options protégées, les commandes et les prérequis sont contractuels dans [MANAGEMENT.md](MANAGEMENT.md). Les résultats réellement exécutés et les contrôles non exécutés sont détaillés dans [VALIDATION_MANAGEMENT.md](VALIDATION_MANAGEMENT.md). La présence de tests ou d’un workflow ne constitue pas une qualification du système RHEL de production.
