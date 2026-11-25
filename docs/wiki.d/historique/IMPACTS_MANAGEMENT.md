# Impacts et décisions — management ciblé

[Documentation](../../index.md) › [Historique des évolutions](index.md) › Impacts et décisions — management ciblé

[Retour au Parent](index.md)

## Sommaire

- [Menaces et limites de protection](#menaces-et-limites-de-protection)

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

Les limites de syntaxe, les options protégées, les commandes et les prérequis sont contractuels dans [MANAGEMENT.md](../exploitation/MANAGEMENT.md). Les résultats réellement exécutés et les contrôles non exécutés sont détaillés dans [VALIDATION_MANAGEMENT.md](../qualification/VALIDATION_MANAGEMENT.md). La présence de tests ou d’un workflow ne constitue pas une qualification du système RHEL de production.

---

Documentation DNSZIBO · navigation actualisée le 23 novembre 2026.
