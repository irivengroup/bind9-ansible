# Vérification du management — 17 octobre 2026

Rapport historique : le paramètre de déploiement courant est désormais `hibernate_deployment: no`. Voir [HIBERNATE_DEPLOYMENT.md](HIBERNATE_DEPLOYMENT.md) et [AUDIT_HIBERNATE_DEPLOYMENT.md](AUDIT_HIBERNATE_DEPLOYMENT.md) pour le contrat et les preuves actuels. Les résultats et anciens paramètres décrits ci-dessous restent propres à la livraison historique.

Rapport historique de la livraison `2026.10.17-management`. Pour les changements et contrôles rejoués sur la révision durcie, voir [DURCISSEMENT_MANAGEMENT.md](DURCISSEMENT_MANAGEMENT.md). Les nombres ci-dessous ne constituent pas de nouveaux résultats sur cette révision.

Projet **Bind9-Ansible**, cible RHEL 8, Ansible 2.9, Python serveur 3.6.8. Source : `Bind9-Ansible-zones-vides-natives.zip`, SHA-256 `9c146b0c91755e5b416ea063b46e235365f099e17f04b789307024beb44c8f79`. Les anciennes livraisons sont conservées ; le dossier `sources/` n’a pas été modifié. Les empreintes du contenu courant figurent dans `SOURCE-MANIFEST.sha256`.

## Résultats observés

| Statut | Contrôle et résultat | Périmètre réel |
|---|---|---|
| **EXÉCUTÉ** | 704 tests avec Jinja 3.0.3 et 704 avec Jinja 2.10.3, Ansible 2.9.27 / Python contrôleur 3.8.20 | Non-régression hors six fichiers de workflows privilégiés exécutés séparément. Inclut le transport du module, le sceau, le refus du second déploiement, force et lease concurrente. |
| **EXÉCUTÉ** | 175 tests ciblés finaux : 155 CRUD/refus/reprise, 15 contrats force/santé et 5 transports Ansible | Etat final, incluant protection des signatures statiques, lecture SOA et ancre native dans le checksum. Recouvrement avec les suites précédentes : ne pas additionner les nombres comme des cas uniques. |
| **EXÉCUTÉ** | 155 scénarios ciblés avec le véritable `named-checkconf` 9.11.36 EL8 pour les candidats | Fichiers temporaires ; les autres appels natifs de ces tests sont des doubles de contrat. |
| **EXÉCUTÉ** | Couverture du moteur Python : 98.24 %, seuil 98 % respecté | Lignes de `roles/bind9/module_utils/bind9_management.py` uniquement ; ne mesure pas YAML, le transport distant, le réseau ou le système complet. |
| **EXÉCUTÉ** | Deux campagnes natives de management, DNSSEC `no` et `auto` : chacune dix transactions réussies et un rollback d’upstream indisponible après réponse cachée | Vrai BIND 9.11 EL8, RNDC, contrôles UDP/TCP, vrais tar/gzip/SHA-256 et restaurations de fichiers. Réseau et `/etc`, `/var`, `/run`, `/usr/local/sbin` privés ; `restorecon` est un double, pas une politique SELinux enforcing. |
| **EXÉCUTÉ** | BIND EL8 et BIND 9.18.24 : chacun 109 configurations chargées et 284 contrôles DNS | Template existant, fichiers natifs, DNSSEC, IPv6 sortant, absence d’écoute DNS IPv6, forwarding, transferts et contrôles Ansible de santé. Les binaires EL8 sont issus de RPM AlmaLinux extraits ; ce n’est pas un déploiement système RHEL. |
| **EXÉCUTÉ** | 18 workflows Ansible FRONT et 9 workflows DNSSEC | Ajouts, idempotence, reprises et rollback ; CLI réseau/systemd substituées par des doubles. Le code de routage reste natif YAML/Jinja, sans nouveau Python de routage. |
| **EXÉCUTÉ** | 25 workflows : logs/SELinux (12), firewalld (8), sauvegarde/restauration (5) | Vrais modules Ansible/fichiers/archives avec outils système doublés ou espaces de montage privés. Ne qualifie ni SELinux enforcing, ni firewalld système réel. |
| **EXÉCUTÉ** | Six contrôles de syntaxe : les deux playbooks × trois inventaires ; préflight de 12 hôtes par moteur Jinja | Vraies assertions et facts RHEL simulés ; aucun accès à vos serveurs. |
| **EXÉCUTÉ** | Python 3.6.8 EL8 : compilation de tous les Python de production, lecture/checksum/écriture protégée/lease et préservation du texte | Interpréteur EL8 extrait ; commandes natives du scénario Python doublées. Le module Ansible distant sur votre RHEL reste à recetter. |
| **EXÉCUTÉ** | Pyflakes, Bandit ciblé, compilation, syntaxe shell, contrôle des imports, liens Markdown, périmètre et archives | Deux alertes informatives subprocess examinées et annotées localement : exécutables internes, arguments séparés, aucun shell ni commande fournie par la requête. Aucun contrôle global de sécurité désactivé. |

Les dépréciations du runtime historique restent visibles. Les échecs intermédiaires de chemins de tests et de configuration du laboratoire ont été corrigés puis les contrôles concernés rejoués ; ils ne sont pas présentés comme des échecs non résolus. La présence de la CI n’est pas sa preuve d’exécution distante.

## Parties impactées et invariants

Voir [IMPACTS_MANAGEMENT.md](IMPACTS_MANAGEMENT.md) pour la matrice code/données/sécurité/compatibilité/exploitation et `DELIVERY.json` pour la liste exhaustive. Le template BIND, les templates de zones, inventaires, exemples DNS existants et scripts/filtres DNS sont identiques à la source. Les 27 tâches contextuelles et l’orchestration sont déplacées, avec adaptation des imports. Les changements fonctionnels du déploiement sont limités au garde, à la lease et au sceau demandé après santé réussie.

Le management est borné aux clauses, zones statiques et RRsets décrits dans [MANAGEMENT.md](MANAGEMENT.md). Il refuse les conflits et les formats non pris en charge. Atomicité par fichier et par hôte ; absence de transaction distribuée ou d’exclusion d’un éditeur root non coopératif. Les mises à jour ne réconcilient pas l’inventaire.

## Contrôles restant à réaliser

**Qualification opérationnelle : NON TERMINÉE.** Raison commune : aucune paire de vos serveurs RHEL 8 accessible. Prérequis communs : paire jetable représentative, même inventaire/paquets/politique, accès ADM de secours, sauvegardes vérifiées et clients des deux segments.

| Statut | Procédure | Résultat attendu et risque restant |
|---|---|---|
| **NON EXÉCUTÉ** | Déployer la paire, vérifier le sceau et son SHA-256 ; relancer sans force puis avec `--tags deploy -e force=yes`. | Premier parcours sain, deuxième bloqué avant reconstruction, reconstruction explicite sauvegardée et saine. Restent intégration DNF/systemd/Python cible et état initial réel. |
| **NON EXÉCUTÉ** | Lire puis appliquer chaque CRUD en recette avec les empreintes par hôte ; modifier manuellement une option non ciblée puis tenter une écriture avec l’ancienne empreinte. | Refus de l’empreinte périmée ; après nouvelle lecture, seule l’action et ses conséquences nécessaires changent. Restent particularités des fichiers réellement déployés. |
| **NON EXÉCUTÉ** | Créer une zone autoritative par management sur les deux hôtes, créer/modifier un RRset sur le primaire, comparer SOA et réponses UDP/TCP du secondaire. | Transfert TSIG BACK et convergence du serial ; aucun transfert externe. Le laboratoire prouve la réplication du modèle existant, pas cette séquence complète de management sur votre paire. |
| **NON EXÉCUTÉ** | Tester SELinux enforcing, labels des nouveaux fichiers, unités natives, firewalld et redémarrage de chaque hôte. | Fichiers lisibles par `named_t`, RNDC fonctionnel, règles et DNS persistants. Restent politique locale et comportement du boot. |
| **NON EXÉCUTÉ** | En recette, interrompre une écriture multi-fichiers et la connexion du contrôleur ; suivre la reprise du journal décrite dans MANAGEMENT. | Conflits conservés, restauration contrôlée, contrôle BIND/DNS puis retrait manuel de la lease. Risque de publication partielle après crash ; aucun crash physique n’a été simulé. |
| **NON EXÉCUTÉ** | Tester vos upstreams, ancres, FIPS éventuel et renouvellement NTA au-delà de sept jours. | Validation réelle, exception limitée au suffixe privé et renouvellement stable. Restent politiques cryptographiques et confiance externes. |
| **NON EXÉCUTÉ** | Mesurer charge DNS, latence de rechargement, taille mémoire du parsing, disque/sauvegardes et délais de reprise. | Objectifs de service et capacité respectés. Aucun seuil de performance ni RPO/RTO de production n’est démontré. |
| **NON EXÉCUTÉ** | Exécuter la matrice CI distante du dépôt et conserver ses journaux. | Toutes les combinaisons déclarées réussissent ; les seuls moteurs Ansible contrôlés localement sont indiqués ci-dessus. |

## Reproduction en laboratoire

Depuis la racine, dans un laboratoire Linux root jetable avec les dépendances de la CI :

```bash
export ANSIBLE_CONFIG="$PWD/ansible.cfg"
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-deploy.yml --syntax-check
ansible-playbook -i inventory/prod/hosts.ini playbooks/bind9-management.yml --syntax-check
python -m pytest -q tests/test_management.py tests/test_management_refusals.py tests/test_management_ansible.py tests/test_deployment_contract.py
python -m coverage run --include='*/roles/bind9/module_utils/bind9_management.py' -m pytest -q tests/test_management.py tests/test_management_refusals.py
python -m coverage report --fail-under=98
```

Pour les vrais daemons, fournir les outils/fichiers natifs décrits dans le README, puis :

```bash
read -r -p 'Répertoire absolu des outils BIND : ' test_bin_dir
read -r -p 'Répertoire absolu des fichiers natifs EL8, named.root.key inclus : ' test_native_dir
read -r -p 'Chemin absolu du binaire named : ' test_named
sudo --preserve-env=PATH env "PYTHON=$(command -v python)" BIND_MANAGEMENT_DNSSEC=no   bash tests/run_integration_management.sh --bin-dir "$test_bin_dir" --native-dir "$test_native_dir" --named "$test_named"
sudo --preserve-env=PATH env "PYTHON=$(command -v python)" BIND_MANAGEMENT_DNSSEC=auto   bash tests/run_integration_management.sh --bin-dir "$test_bin_dir" --native-dir "$test_native_dir" --named "$test_named"
```

Ces lanceurs exigent `unshare`, les montages privés, `ip` et root ; ne pas les utiliser sur un serveur opérationnel. Les fichiers et processus du laboratoire ne sont pas intégrés à l’archive source. Le ZIP, son CRC, les empreintes du manifeste et une double construction identique dans le même runtime sont contrôlés lors du packaging. Les sommes de contrôle ne remplacent pas un canal de distribution authentifié.
