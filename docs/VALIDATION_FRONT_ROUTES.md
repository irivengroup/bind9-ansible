# Vérification des routes FRONT — 14 octobre 2026

Périmètre : ajout des seules routes IPv4 manquantes vers les clients FRONT, passerelle découverte dans le profil NetworkManager actif, préservation du réseau existant, rollback des ajouts de l'exécution en échec. Les essais ont été rejoués sur la copie nettoyée décrite dans [VALIDATION_NETTOYAGE.md](VALIDATION_NETTOYAGE.md). Les sources et les tests figurent dans cette livraison ; aucun accès à vos serveurs n'a été utilisé.

| Contrôle | Statut | Preuve observée |
|---|---|---|
| Planificateur natif | **EXÉCUTÉ** | Tests des formats ip/nmcli, des conflits et métriques, du chemin SSH, des tables/règles, des journaux et du rollback sélectif ; couverture du filtre 99 % |
| Workflows Ansible 2.9.27 | **EXÉCUTÉ** | Huit scénarios, CLI contractuels isolés : stdout de mutation vide, second passage, check mode, échecs d'application/vérification, interruption/reprise, succès DNS différé et rollback après échec santé ou handler |
| Rollback couplé au DNS | **EXÉCUTÉ** | Vrai bloc/rescue du rôle avec `--tags deploy` ; seules les opérations DNS sont substituées. Nouvelles routes actives/persistantes supprimées après panne santé ou handler de rechargement, route préexistante préservée. Le restore BIND lui-même n'est pas testé par ces scénarios. |
| Vrai NetworkManager | **EXÉCUTÉ** | NM 1.46.0, quatre workflows Ansible : ajout, seconde exécution sans changement, reprise sélective, nouvel ajout |
| Persistance | **EXÉCUTÉ** | Rechargement et down/up d'un profil dans le laboratoire jetable ; route encore présente. Ces commandes ne sont pas utilisées par le rôle. |
| Préservation réseau | **EXÉCUTÉ** | Routes initiales, défauts, profils BACK/ADM intacts ; une modification DNS pending du profil FRONT reste non appliquée |
| Compatibilité Python 3.6 | **EXÉCUTÉ** | Grammaire et test d'absence de l'API ipaddress introduite en 3.7 ; pas d'exécution sous Python 3.6.8 |

Le défaut de rollback Ansible a été reproduit : un import statique réévaluait `not rollback_attempted` après la première tâche et sautait les suppressions. Une inclusion dynamique évalue cette condition une fois et applique les tags `deploy` aux tâches incluses. Le scénario d'échec DNS reproduit cette cause et réussit avec la correction.

## Reproduction

Sur un contrôleur Linux de test avec root, Ansible 2.9, Python/PyYAML/pytest :

```bash
python -m pytest -q tests/test_native_front_routes.py tests/test_front_routes_ansible.py
sudo bash tests/run_integration_front_routes.sh
```

Le second test nécessite NetworkManager, nmcli, ip, dbus-daemon, unshare et les capacités de montage/réseau ; il isole les espaces réseau, processus et montages avant de démarrer son NM privé. Il ne touche pas aux profils système. Les tests à doubles CLI ne prétendent pas qualifier toutes les versions NetworkManager.

## Recette cible manquante

**NON EXÉCUTÉ** : RHEL 8, Python 3.6.8, NM réellement installé, persistance après redémarrage serveur et session SSH réelle. Raison : aucune cible disponible. Prérequis : recette isolée du trafic de production, SSH/sudo, profil FRONT préconfiguré et sauvegarde/relevé des routes et profils.

```bash
ansible-playbook -i inventory/dev/hosts.ini playbooks/bind9-deploy.yml --limit site1-dns01 --tags routes --check
ansible-playbook -i inventory/dev/hosts.ini playbooks/bind9-deploy.yml --limit site1-dns01 --tags routes
```

Relever avant/après `ip -j -4 route show table main`, `ip -j -4 rule show`, le UUID et `ipv4.gateway`/`ipv4.routes` du profil FRONT. Vérifier `ip route get` avec source FRONT pour une adresse réelle de chaque réseau client, puis une requête UDP/TCP depuis ce client. Répéter la commande et organiser un redémarrage de recette.

Attendu : seuls les ajouts nécessaires apparaissent, réponse via FRONT, deuxième passage sans mutation de route, défauts/profils/connexion SSH conservés et persistance après redémarrage. Risques : différences de NM/iproute2, règles de routage non prises en charge, filtrage réseau et changements concurrents par un autre administrateur. Aucun pont, VRF ou routage par règles complexe n'est pris en charge silencieusement.

**NON EXÉCUTÉ** : rollback opérationnel complet DNS + routes sur RHEL. Procédure : sur la recette et après sauvegarde vérifiée, provoquer un échec DNS contrôlé après création de nouvelles routes dans `--tags deploy`. Attendu : les nouveaux ajouts persistants et actifs sont supprimés, les routes préexistantes restent, les fichiers BIND sont restaurés. Examiner service, journaux, profil et table ; appliquer `routes-recover` uniquement si un journal interrompu subsiste et qu'aucun autre déploiement n'est en cours. Risque restant : commandes ou accès réseau indisponibles pendant l'échec ; le journal doit alors être conservé et la reprise opérée explicitement.

Un commit réussi supprime le journal de cette opération. La restauration manuelle BIND après ce commit ne supprime pas ses routes. La qualification de production reste **NON TERMINÉE**. Voir [FRONT_ROUTES.md](FRONT_ROUTES.md) pour le contrat et [VALIDATION_NAMED.md](VALIDATION_NAMED.md) pour la recette DNS.
