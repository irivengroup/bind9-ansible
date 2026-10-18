# Validation et impacts — fiabilité des données et I/O

Rapport historique : le paramètre de déploiement courant est désormais `hibernate_deployment: no`. Voir [HIBERNATE_DEPLOYMENT.md](HIBERNATE_DEPLOYMENT.md) et [AUDIT_HIBERNATE_DEPLOYMENT.md](AUDIT_HIBERNATE_DEPLOYMENT.md) pour le contrat et les preuves actuels. Les résultats et anciens paramètres décrits ci-dessous restent propres à la livraison historique.

Révision **2026.10.17-management-io-safety.1**, à partir de
`Bind9-Ansible-management-integrite.zip`, SHA-256
`4307808fe31a73b170ba76999a1f045b42dccb77739387c08c128d3f245c4f15`.
Les références synchronisées, archives et sources des livraisons précédentes
restent inchangées. Leurs résultats sont historiques ; les résultats ci-dessous
sont propres à cette passe.

## Objectif, menaces et décisions

Protéger les données contre les lectures courtes, les écritures partielles,
la corruption observable avant publication, les sorties de commandes excessives,
les processus descendants survivant à une échéance et les erreurs de parsing
des réponses DNS. La configuration sur disque reste la source de vérité ; aucun
retour silencieux vers l'inventaire, une ancienne copie ou un préfixe tronqué.

Les contrôles de cette passe s'ajoutent au verrou commun, aux empreintes, au
contrôle d'identité des fichiers et au rollback qui préserve une intervention
manuelle. Une intégrité incertaine conduit à un refus ou à une reprise explicite.
Ces mécanismes ne constituent pas une garantie absolue contre les pannes du
stockage, du noyau, les écritures root non coopératives ou un crash multi-fichiers.

| Partie impactée | Risque et comportement final |
|---|---|
| `module_utils/bind9_management.py` — `SafeFiles` | Vérification du nombre exact d'octets par rapport à `fstat`, taille maximale avant lecture, UTF-8 strict et refus NUL. Contrôles d'identité existants conservés. |
| Même module — `VerifiedIO` et intégration | Write-count, flush, fsync, taille et relecture intégrale des temporaires, candidats, copies privées et manifestes. Création exclusive sans lien, synchronisation des répertoires du journal et des copies. Aucun candidat incomplet n'est présenté au validateur comme s'il était complet. |
| Même module — `NativeCommand` | Capture stdout/stderr bornée à 1 Mio, UTF-8 strict, échéance de 60 s ; groupe de processus propre et arrêt en cas d'échec, attente du processus principal. Aucun résultat coupé à la limite n'est retourné comme complet. |
| Même module — `Manager`, `SnapshotFiles`, `Syntax`, `Configuration`, `ZoneDocument` | Copie et taille des requêtes unitaires, limites agrégées pour tout CRUD, taille individuelle des candidats, quotas de tokens/profondeur/records ; dépassement refusé sans supprimer des éléments. |
| Même module — `commit`, `DnsReply`, `check_probes` | Synchronisation de l'archive et de son SHA avant publication ; refus TC/statuts contradictoires/compteur ANSWER incohérent ; `dig -r` ignore les réglages personnels et `+ignore` conserve une vraie sonde UDP, avec TCP séparé. |
| `library/bind9_manage.py` | Diagnostic d'`OSError` avec symbole et numéro d'errno, sans chemin ni message sensible. Les états de reprise existants restent prioritaires. |
| `ansible.cfg` | `duplicate_dict_key = error` : refus avant les tâches de clés YAML pouvant remplacer silencieusement une action ou une liste. Option vérifiée dans le code installé d'Ansible 2.9. |
| `tests/test_management_io_safety.py` | 45 nouveaux cas : écritures courtes, compteur mensonger, octet corrompu, ENOSPC, lectures partielles, encodage, limites, deadlines et descendants, DNS tronqué, YAML dupliqué et diagnostic d'errno via le vrai transport. |
| `tests/test_management_refusals.py`, `test_management_ansible.py` | Contrat des commandes testé avec de vrais processus ; nouvelle commande sync intégrée au double de transport, sans prétendre qu'il qualifie le stockage. |
| `tests/integration_management.py` | HOME de laboratoire avec `.digrc` perturbateur, requêtes de contrôle isolées via `-r`, démons/archives/DNS réels. |
| `.github/workflows/ci.yml`, documentation, livraison | Nouvelle suite incluse dans la mesure, seuil de 98 % conservé ; prérequis et nouveaux refus documentés, manifeste et ZIP actualisés. |

Les playbooks d'entrée, les templates, les variables par défaut du rôle, les
inventaires, le routage FRONT et le helper de sauvegarde déployé ne sont pas
modifiés. GNU coreutils `sync` est utilisé par le manager, sans nouvelle collection
ni bibliothèque Python de production. La configuration Ansible du contrôleur
change volontairement son comportement face aux doublons, y compris certaines
surcharges d'ancres ; ne pas présenter cela comme une compatibilité transparente
de tous les inventaires privés non inspectés.

## Contrôles réellement exécutés

| Statut | Contrôle | Preuve et portée |
|---|---|---|
| **EXÉCUTÉ** | Reproduction avant les correctifs respectifs | Les 19 premiers scénarios d'injection échouaient sur le moteur précédent. Deux cas YAML montraient un code retour nul avec écrasement de valeur. Un `.digrc` perturbateur a reproduit un faux échec natif et son rollback avant ajout de `-r`. |
| **EXÉCUTÉ** | Suite finale consolidée | **367 tests réussis**, incluant les **45 nouveaux cas**, sur Python **3.6.8**, Ansible **2.9.27**, Jinja2 **2.10.3** et `named-checkconf` natif **9.11.36-16.el8_10.16**. CRUD, lots, listings, refus, concurrence, transport, déploiement/sceau et nouvelles I/O. Les frontières DNS/SELinux sont doublées dans les composants ; la recette native ci-dessous est séparée. |
| **EXÉCUTÉ** | Couverture du moteur final | **98,20 %**, soit **1313/1337 lignes**, seuil de 98 % conservé sans nouvelle exclusion. Ce chiffre ne mesure pas l'intégralité du YAML, du transport, des branches ou du système. |
| **EXÉCUTÉ** | Démons BIND EL8 réels, modes `no` et `auto` | **12 transactions committées par mode**, dont 2 lots mixtes ; UDP/TCP, RNDC, vrais fichiers, archives et synchronisation, listings, refus tardif de RDATA invalide, rollback unitaire et commun d'un lot. `.digrc` perturbateur présent pendant les essais finaux. |
| **EXÉCUTÉ** | Protection avant publication | Pour chaque phase candidat/copie/temporaire/manifeste, défaut court/mensonger/corrompu/ENOSPC injecté : configuration live inchangée et aucun reload. Dépassement de requête, de candidat ou de parseur : refus sans publication. |
| **EXÉCUTÉ** | Processus et transport | Sortie au-delà du plafond refusée ; frontière exacte UTF-8 préservée ; processus et descendant arrêtés lors du timeout. ENOSPC/EIO remontent via Ansible sans la chaîne sensible injectée. |
| **EXÉCUTÉ** | Analyse et syntaxe | Bandit **1.7.10** et Pyflakes **3.2.0** ciblés sans alerte, dans un conteneur Python 3.11 ; compilation des fichiers modifiés avec Python 3.6.8. Deux playbooks sur `prod`, `preprod`, `dev` en syntax-check, avec la configuration YAML stricte. Syntaxe des scripts shell concernés contrôlée. |
| **EXÉCUTÉ** | Packaging | Empreintes, périmètre des changements, CRC, correspondance octet par octet ZIP/source, permissions des fichiers existants et double construction identique. Caches, journaux bruts, environnements et secrets actifs exclus. |
| **ÉCHOUÉ** | Diagnostic court convivial des doublons dans Ansible 2.9.27 | Le contrôleur ancien peut afficher `NoneType ... line` après son `ConstructorError` au lieu d'une explication claire. Limite amont observée, bibliothèque non patchée. Le refus avant les tâches et la cause duplicate-key dans la trace synthétique sont, eux, vérifiés. |

Le laboratoire est un conteneur standard Rocky Linux 8.9 avec paquets EL8.10,
sans capacité ajoutée, sans mode privilégié ni seccomp désactivé. Réseau effectif
`none` et aucun port publié pendant les recettes natives ; sources en lecture
seule. Les démons du banc sont root et SELinux est un double explicite. Cela ne
qualifie pas les permissions du processus named/systemd de vos serveurs.
Les journaux bruts restent hors ZIP ; leurs empreintes sont dans
[PREUVES_FIABILITE_IO.txt](PREUVES_FIABILITE_IO.txt).

Les essais intermédiaires ont été utilisés pour corriger les causes, sans réduire
les assertions de préservation ni le seuil de couverture. Le test de doublon
conserve son assertion de code retour non nul et vérifie la cause dans une trace
portant exclusivement sur des données synthétiques ; la limitation de diagnostic
court n'est pas présentée comme réparée. Les outils QA incompatibles avec Python
3.6 n'ont pas été imposés à la cible : l'analyse statique moderne a été exécutée
dans un second conteneur, et non ajoutée aux dépendances du rôle.

## Reproduire en laboratoire jetable

Prérequis : Linux root isolé, Ansible 2.9.27, pytest/coverage compatibles, BIND
EL8 et outils de recette. Ne pas exécuter les tests privilégiés sur un DNS
opérationnel. Depuis la racine :

```bash
export ANSIBLE_CONFIG="$PWD/ansible.cfg"
export BIND_MANAGEMENT_CHECKCONF=/usr/sbin/named-checkconf
python3 -m coverage run --include='*/roles/bind9/module_utils/bind9_management.py' \
  -m pytest -q tests/test_management.py tests/test_management_refusals.py \
  tests/test_management_hardening.py tests/test_management_ansible.py \
  tests/test_management_batch.py tests/test_management_batch_ansible.py \
  tests/test_management_list.py tests/test_management_live_integrity.py \
  tests/test_management_io_safety.py tests/test_deployment_contract.py
python3 -m coverage report -m --precision=2 --fail-under=98
```

La procédure native est celle de
[VALIDATION_INTEGRITE_LIVE.md](VALIDATION_INTEGRITE_LIVE.md#reproduction), avec
les fichiers officiels EL8 copiés avant le banc. Le scénario inclut maintenant
un `.digrc` perturbateur. Les namespaces ou le conteneur doivent isoler réellement
le réseau et le système de fichiers avant de déclarer le laboratoire isolé.

## Qualification restante et risques

**NON TERMINÉ : qualification production et diagnostic court amont.** Les
corrections sont livrées avec les preuves ci-dessus ; aucune garantie de zéro
incident n'est donnée. Les bornes et limites fonctionnelles détaillées sont dans
[FIABILITE_DONNEES_ET_IO.md](FIABILITE_DONNEES_ET_IO.md).

| Statut | Contrôle non exécuté, raison et prérequis | Procédure, attendu et risque restant |
|---|---|---|
| **NON EXÉCUTÉ** | Paire RHEL 8 utilisateur : aucun accès. Préparer recette représentative, inventaire privé contrôlé, sauvegardes et SSH ADM. | Exécuter read/list, lot mixte, `--check`, apply et refus de checksum périmé ; vérifier données, SOA et transfert TSIG BACK. La convergence de la paire et ses chemins particuliers restent à qualifier. |
| **NON EXÉCUTÉ** | SELinux enforcing, systemd et firewalld réels : absents du banc représentatif. | Vérifier `named-checkconf -z`, `ls -lZ`, `systemctl status named`, `journalctl -u named`, clients et redémarrage en recette. Attendus : labels, droits du service et flux corrects ; le double SELinux ne démontre pas cela. |
| **NON EXÉCUTÉ** | Pannes physiques, crash machine, NFS/stockages distants, manque réel de blocs/inodes et saturation : pas de banc de panne autorisé. | Sur clone jetable, interrompre la machine pendant publication, provoquer des fautes de stockage, tester quotas/réserve et suivre journal/lease. Attendus : refus ou reprise explicite sans écrasement manuel. Les injections logiciel ne démontrent pas la fiabilité matérielle ni les contrats du stockage. |
| **NON EXÉCUTÉ** | Charge et objectifs de disponibilité : aucun profil cible fourni. | Mesurer durée de préparation, backup, synchronisation, reload, mémoire et latence sous charge représentative. Fixer les seuils avant production. Les relectures/fsync et quotas peuvent avoir un coût opérationnel. |
| **NON EXÉCUTÉ** | CI distante et autres versions : aucun lancement distant effectué. | Exécuter le workflow livré avec ses jobs requis et seuil de 98 %. La matrice complète reste à qualifier. |

Les opérateurs root doivent utiliser le verrou commun. Les transactions sont
locales à chaque hôte et atomiques par remplacement de fichier, pas par ensemble
de fichiers ni par paire. Un fichier déjà amputé mais valide avant la première
lecture, ou une donnée perdue dans un parseur amont, ne peut pas être reconstitué
sans référence indépendante ; l'inventaire n'est pas substitué à l'état live.

En incident, conserver les fichiers actuels, copies privées, manifestes et lease.
Comparer les empreintes et résoudre les conflits avant toute restauration ciblée ;
valider et contrôler le DNS avant de libérer le verrou. Ne pas relancer à l'aveugle
et ne pas restaurer une archive complète sur des changements manuels. Un retour
au code précédent ne restaure pas les données et retire les protections ajoutées.
