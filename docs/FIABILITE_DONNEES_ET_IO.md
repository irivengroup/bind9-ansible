# Fiabilité des données récupérées et des écritures

Révision `2026.10.17-management-io-safety.1`. Les contrôles ci-dessous complètent
la [préservation de la configuration live](INTEGRITE_CONFIGURATION_LIVE.md).
Ils protègent le workflow de management, sans reconstruire depuis l'inventaire
et sans modifier les règles DNS, les routes ou les templates de déploiement.

## Ce que le code refuse

Une lecture de fichier doit restituer **exactement** la taille annoncée par
`fstat`, dans la limite autorisée. Une lecture courte, un dépassement, un changement
d'inode/métadonnées pendant la lecture, un texte non UTF-8 ou un octet NUL
provoquent un refus. Aucun décodage avec remplacement des caractères ni retour
d'un préfixe partiel n'est utilisé. Les octets des autres réglages manuels restent
la base du candidat ; ils ne sont pas reconstruits depuis des valeurs par défaut.

Les requêtes unitaires sont copiées après sérialisation JSON contrôlée : une
modification du dictionnaire d'origine par l'appelant ne change plus une demande
déjà vérifiée. Les objets non JSON, valeurs numériques non finies et requêtes
trop grandes sont refusés. Les lots conservent leur copie propre et les contrôles
d'empreintes d'état et de plan.

Les sorties des commandes internes sont lues jusqu'à EOF avec un budget mémoire
et une échéance. Dépasser la limite provoque une erreur, pas une troncature.
Un code de sortie non nul ou un UTF-8 invalide provoque un refus sans afficher
le contenu potentiellement sensible. À l'échec ou au timeout, le groupe de
processus créé pour cette commande est arrêté et le processus principal attendu.
Cela ne termine pas le démon BIND indépendant : le groupe appartient au client
RNDC, au helper ou au validateur lancé par le manager.

Les sondes DNS utilisent `dig -r` pour ignorer le `.digrc` opérateur et `+ignore`
pour que la sonde UDP ne soit pas remplacée silencieusement par du TCP. Le passage
TCP reste distinct. Le parseur refuse le drapeau `TC`, les statuts multiples ou
contradictoires et une différence entre le nombre de réponses annoncé et les
enregistrements reçus. Propriétaire, type, adresse, autorité et serial SOA restent
contrôlés comme auparavant.

## Écriture et sauvegardes

Pour les fichiers du candidat, les copies privées de secours, le manifeste de
transaction et le temporaire de publication, `VerifiedIO` :

1. vérifie le nombre d'octets accepté par `write` ;
2. exécute `flush` puis `fsync` ;
3. compare la taille persistée aux octets attendus ;
4. relit le descripteur et compare son contenu intégral.

Le candidat relu est ensuite soumis à `named-checkconf -z`. Le temporaire live
est relu avant tout remplacement. Une écriture courte, un compteur de succès
mensonger, un octet différent ou une erreur système interrompt donc la préparation.
Les nouvelles copies privées utilisent une création exclusive sans suivre un lien.
Les répertoires des copies et du journal sont synchronisés pour rendre leurs
entrées persistantes sur les systèmes de fichiers locaux qui fournissent ces
garanties.

Le helper existant vérifie déjà son archive et son SHA-256. Après réception de son
résultat, le manager exécute en plus `/usr/bin/sync -- ARCHIVE SHA256 DOSSIER`
avant publication. Les opérandes désignent ces fichiers/répertoire précis ; ce
n'est pas un `sync -f` de tout le système de fichiers. Cette étape fonctionne
aussi avec le helper précédemment installé sur le serveur et n'exige pas de
redéployer la configuration DNS. GNU coreutils et son binaire `sync`, présents
sur la cible EL8 de référence, sont requis ; aucun paquet Python ou collection
de production supplémentaire n'est ajouté.

Le rollback conserve son contrôle d'identité : il ne remplace pas une intervention
manuelle par une ancienne copie. Une cible modifiée par un tiers est laissée en
place et peut imposer `recovery-required`. Une erreur système non encapsulée
remonte via le module avec son symbole et numéro (`ENOSPC`, `EIO`, etc.), sans
chemin ou sortie brute sensible. Toujours examiner l'état live et le journal
avant de relancer : un échec de transport ou de journalisation ne prouve pas
qu'aucune phase n'a été exécutée.

## Limites explicites, sans suppression silencieuse

| Objet | Limite du code |
|---|---|
| Fichier statique ou candidat | 16 Mio par fichier |
| Graphe statique, y compris CRUD unitaire | 512 assets et 64 Mio cumulés |
| Requête unitaire sérialisée JSON | 256 Kio |
| Opérations d'un lot sérialisées JSON | 256 Kio, 1 à 64 opérations |
| Syntaxe d'un fichier de configuration | 250 000 tokens, profondeur 64 |
| Graphe des configurations incluses | 500 000 tokens, avec limite d'includes existante de 128 |
| Document d'une zone consultée ou modifiée | 100 000 enregistrements |
| Sortie cumulée stdout/stderr d'une commande interne | 1 Mio |
| Échéance d'une commande une fois lancée | 60 secondes |

Un dépassement est un refus explicite ; il ne retire pas de données pour faire
tenir le résultat. La pagination des listings reste distincte : `limit`,
`total`, `count` et `next_offset` décrivent une page, dont le checksum couvre
toujours le graphe statique complet. Une zone qui dépasse le plafond du parseur
ne fournit pas un catalogue artificiellement réduit à 100 000 lignes.

Ces limites sont celles du moteur livré, pas de nouvelles variables d'inventaire.
Une adaptation de capacité doit être revue, testée et mesurée avant utilisation.
Les contrôles de relecture/synchronisation ajoutent des I/O ; aucune amélioration
de performance ou disponibilité sous charge n'est revendiquée.

## Entrées YAML et configuration du contrôleur

Utiliser le fichier `ansible.cfg` livré :

```bash
export ANSIBLE_CONFIG="$PWD/ansible.cfg"
ansible-config dump --only-changed
```

Il active `duplicate_dict_key = error`, option vérifiée dans Ansible 2.9. Une clé
YAML répétée pouvait remplacer une action ou une liste d'opérations avant même
que le module les reçoive. Elle est maintenant refusée au chargement, avant les
tâches. La priorité entre fichiers d'inventaire distincts n'est pas changée.
Certaines surcharges par merge d'ancres YAML peuvent aussi être concernées :
contrôler les inventaires privés avant usage. Ne pas masquer le contrôle avec
`ANSIBLE_DUPLICATE_YAML_DICT_KEY=warn/ignore` ni charger un autre fichier de
configuration sans ces garanties.

**Limite amont observée :** Ansible 2.9.27 peut mal présenter le diagnostic d'un
doublon et afficher `'NoneType' object has no attribute 'line'`. Le refus est
effectif avant les tâches ; la bibliothèque installée n'a pas été patchée. Les
tests synthétiques utilisent le détail de la trace pour prouver la cause. Ne pas
activer des traces détaillées sur des données réelles sensibles sans précaution.

Cette option ne traite pas les doublons d'un JSON déjà chargé par Ansible avant
le module. Préférer des fichiers YAML d'actions contrôlés ; générer les fichiers
JSON d'empreintes avec le script `json.dump` du guide, sans édition ambiguë.
Le manager ne peut pas retrouver une information supprimée par un parseur amont.

## Ce que ces contrôles ne peuvent pas promettre

Une modification détectable pendant une opération est refusée. En revanche,
un fichier déjà tronqué mais encore valide **avant la lecture initiale** ne peut
pas être distingué d'une suppression manuelle autorisée sans référence externe
approuvée. Le manager ne restaure pas silencieusement l'inventaire ou une ancienne
archive à la place des choix opérateur. Les checksums ne sont pas des signatures.

Les garanties supposent un stockage et un noyau qui respectent leurs contrats
d'I/O. Une relecture peut provenir du cache ; `fsync` ne démontre pas la santé
physique du support, du contrôleur ou de son alimentation. Les conséquences
systémiques d'un disque plein ne sont pas annulées par un refus : capacité,
réserve d'espace, quotas, supervision et sauvegardes indépendantes restent à
qualifier. Le moteur ne supprime pas les données manuelles pour libérer de l'espace.

Le verrou reste coopératif face à root. Tous les opérateurs et outils doivent
suivre le [protocole commun](INTEGRITE_CONFIGURATION_LIVE.md). Il n'existe pas de
transaction atomique multi-fichiers face à un crash, ni de transaction distribuée
entre les deux serveurs. Les sorties d'un terminal ou d'une interface peuvent
être visuellement tronquées : ne pas les confondre avec un export intègre des
données ; relire les ressources, examiner les pages et le plan exact avant apply.

La [validation de cette révision](VALIDATION_FIABILITE_IO.md) distingue les essais
réellement exécutés et les qualifications restantes. Aucun engagement d'absence
totale d'incident en production n'est donné.
