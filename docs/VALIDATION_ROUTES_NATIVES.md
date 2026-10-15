# Routes externes en tâches Ansible natives

> Historique du routage natif : les nombres ci-dessous décrivent la livraison source. Le contrat client courant et ses contrôles figurent dans [CLIENTS_UNIQUES.md](CLIENTS_UNIQUES.md).

Cette adaptation de **Bind9-Ansible** reprend le principe du playbook fourni : sélectionner l’interface portant l’adresse connue, lire la passerelle du seul profil NetworkManager actif, persister les routes avec `nmcli`, puis les ajouter à chaud avec `ip route`. Le point d’entrée reste `playbooks/bind9-deploy.yml`. Aucun nouveau playbook autonome ni variable `target_secondary_ip`/`route_destination` n’est ajouté : les entrées existantes sont `bind_front_ip` et `bind_cloud_clients`.

## Corrections et intégration

| Point identifié | Comportement intégré |
|---|---|
| Fermetures Jinja incomplètes, portée de boucle | Expressions complètes et namespaces ; tests avec une adresse FRONT primaire puis secondaire |
| Adresse observée et profil | Lecture native des adresses réellement affectées, sélection unique d’une interface UP distincte de BACK/ADM ; UUID du profil actif pour éviter noms ambigus et espaces |
| Passerelle | Propriété persistante `ipv4.gateway` de ce profil ; rejet si absente, invalide, hors lien, égale au serveur ou non utilisable |
| Comparaison de routes | Préfixe, passerelle et métrique comparés exactement ; `198.51.100.1` ne correspond pas à `.11`, un préfixe n’est pas recherché par sous-chaîne |
| Route obsolète préexistante | Refus explicite d’un conflit avant modification. Le rôle n’efface pas une route d’origine inconnue pour la remplacer ; cela préserve le périmètre additif demandé |
| Persistance et état actif | Ajouts manquants dans le profil avec `nmcli connection modify ... +ipv4.routes` et dans le noyau avec `ip -4 route add ... table main` ; les deux états sont vérifiés séparément |
| Application à chaud | Aucune activation/up/down/reapply de connexion ; aucun changement d’IP, passerelle, DNS ou profil BACK/ADM |
| Erreurs | Codes de sortie non nuls fatals, pas de `failed_when: false` pour les mutations ; stdout vide d’une commande réussie accepté, puis contrôle de l’état réel |
| Rollback et reprise | Journal privé avant mutation ; retrait exact des seuls ajouts encore possédés, conservation des routes préexistantes, refus si identité modifiée ; schéma du journal antérieur accepté |
| Python et dépendances | Suppression de `roles/bind9/filter_plugins/front_routes.py`. Calculs en tâches/Jinja standard, sans netaddr, collection réseau, nouveau script shell ou Python de routage. Les fichiers Python sous tests servent seulement à la recette ; les helpers DNS préexistants sont conservés |
| Contrat | Variables, tags `routes`/`routes-recover`/`deploy`, garde SSH, refus des réseaux protégés, table principale et gestion désactivée par défaut conservés. Déduplication et agrégation des préfixes adjacents/inclus conservées |

Les tâches ajoutées restent dans `roles/bind9/tasks/tasks.d/` : calcul des intervalles IPv4, lecture des routes du profil, calcul des ajouts, contrôle du journal et comparaison des états. Elles sont importées par les parcours existants ; aucune dépendance n’est à installer sur le contrôleur ou sur les cibles pour cette évolution.

## Commandes

Depuis la racine du projet sur le contrôleur Linux :

```bash
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --syntax-check
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --tags routes --check -v \
  -e '{"bind_front_routes_enabled": true}'
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --tags routes \
  -e '{"bind_front_routes_enabled": true}'
```

La simulation ne modifie aucune route et ne crée pas de journal. Ajouter `--limit` pour un serveur réel de l’inventaire. Avant une reprise `--tags routes-recover`, inspecter le journal et s’assurer qu’aucun autre déploiement n’est actif. Les procédures, prérequis réseau et effets du rollback sont détaillés dans [FRONT_ROUTES.md](FRONT_ROUTES.md).

## Preuves et limites

**EXÉCUTÉ** : 260 tests distincts réussis : suite de 256 tests, puis quatre régressions complémentaires de comparaison exacte et d’agrégation CIDR. Les 76 cas d’expressions ont aussi été rejoués ensemble sur les fichiers finaux. Onze scénarios exécutent le vrai runner Ansible avec des CLI isolés. Quatre workflows NetworkManager réels vérifient ajout, second passage, reprise sélective et persistance après réactivation du seul profil de laboratoire.

**EXÉCUTÉ** : syntaxe Ansible 2.9.27 sur trois inventaires, préflight des 12 hôtes avec faits simulés, absence effective de netaddr, Pyflakes et Bandit, couverture des quatre modules Python restants 99 % (277 instructions, une non couverte, seuil 98 %). BIND EL8 9.11.36 et 9.18.24 : chacun 44 configurations et 131 contrôles DNS réels ; compilation des Python restants et réconciliation DNSSEC isolée sous Python 3.6.8.

Les résultats exacts de cette livraison sont consignés dans `DELIVERY.json`. Les tests d’expressions utilisent le moteur réel d’Ansible 2.9.27 ; les workflows exécutent Ansible avec des doubles CLI sans réseau opérationnel. Un autre laboratoire exécute réellement NetworkManager et les commandes du noyau dans des espaces isolés, puis réactive seulement ses profils jetables pour vérifier la persistance.

**NON EXÉCUTÉ** : déploiement sur vos serveurs RHEL 8, leurs versions NM/iproute2, accès SSH réel, reboot cible, panne de connexion pendant mutation et restauration DNS système. Raison : aucune cible accessible. Prérequis : serveur de recette RHEL 8/Python 3.6.8, SSH/sudo, profil FRONT existant avec passerelle, sauvegarde BIND et relevés avant opération. Procédure : simulation ci-dessus, application sur un serveur, relevés `ip -j -4 route show table main` et `nmcli -g ipv4.routes connection show uuid UUID`, vérification `ip -4 route get CLIENT from FRONT_IP`, deuxième passage, reboot de recette puis échec DNS contrôlé et reprise. Attendu : seuls les ajouts requis, routes initiales et SSH conservés, idempotence et suppression sélective lors du rollback. Risques restants : différences de versions et configuration cible, concurrence d’un administrateur, perte SSH et politique de routage refusée.

**NON EXÉCUTÉ** : qualification système complète DNS/SELinux/systemd/DNF, crypto-policy/FIPS, renouvellement DNSSEC sur sept jours, RFC5011, capacité et CI distante. Procédures et risques : [VALIDATION_RHEL8.md](VALIDATION_RHEL8.md), [DNSSEC.md](DNSSEC.md), [OPERATIONS.md](OPERATIONS.md). La qualification de production reste **NON TERMINÉE**. Aucun changement n’a été appliqué à vos serveurs.

Sources de syntaxe : [Ansible 2.9, filtres standard](https://docs.ansible.com/ansible/2.9/user_guide/playbooks_filters.html), [nmcli, manuel officiel](https://networkmanager.dev/docs/api/latest/nmcli.html). L’approche n’utilise pas le filtre ipaddr dépendant de netaddr.
