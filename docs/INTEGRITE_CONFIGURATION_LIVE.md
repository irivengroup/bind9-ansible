# Intégrité de la configuration live et interventions concurrentes

**Exclusion actuelle :** routes et management utilisent toujours un verrou de processus commun `/var/lib/Bind9-Ansible/operations.flock`, libéré automatiquement à la fin du processus. Le fichier vide peut rester présent sans bloquer une nouvelle opération. `bind_operation_lock_enabled: false` ignore uniquement le répertoire historique `.operation.lock` hors hibernation ; il ne désactive pas cette exclusion de processus. Les journaux incomplets sont des données de reprise, jamais des verrous à effacer. Organiser les déploiements DNS complets et interventions manuelles dans une fenêtre exclusive. [Fonctionnement, preuves et limites](REFONTE_ROUTES_MANAGEMENT.md).

Ce document décrit la révision intégrité. Pour les contrôles courants, voir [VALIDATION_FIABILITE_IO.md](VALIDATION_FIABILITE_IO.md) et les [garanties I/O](FIABILITE_DONNEES_ET_IO.md).

Révision `2026.10.17-management-integrity.1`, construite à partir de la livraison
`Bind9-Ansible-management-lots.zip`. Cette évolution porte sur le manager,
ses tests et sa documentation. Le déploiement, les templates DNS, les routes,
les inventaires et les variables par défaut ne sont pas réécrits.

## Contrat de préservation

La configuration **présente sur disque sur le serveur** est la source de vérité
du management : `/etc/named.conf`, ses inclusions et les données des zones
primaires statiques découvertes. Le manager ne reconstruit pas ces fichiers
depuis les templates ou les variables de l'inventaire. Les données runtime,
le cache et les mises à jour DNS dynamiques ne relèvent pas de ce contrat.
Ces fichiers ne constituent pas une copie de la mémoire du démon : un changement
manuel encore en attente de rechargement sera également activé lors du prochain
reload. L'opérateur doit laisser le service et les fichiers dans un état cohérent.

Une écriture ne vise que la ressource explicitement demandée, ainsi que ses
conséquences nécessaires : avance du serial SOA et éventuelles délégations/glue
des zones forwardées. Les clauses et données hors de ce périmètre sont conservées.
Une demande explicite `update` ou `delete` peut donc modifier une ressource créée
manuellement ; la lecture préalable et son empreinte restent obligatoires.

Un changement observé entre lecture et application impose un refus et une
nouvelle lecture. Le manager ne fusionne pas automatiquement deux intentions
concurrentes et ne remplace pas une empreinte périmée par une nouvelle approbation.
Les lots exigent aussi l'empreinte du plan exact. L'inventaire n'est pas synchronisé
automatiquement avec les modifications live ; un déploiement forcé conserve son
comportement de reconstruction et peut les écraser.

## Créations : aucun upsert

| Ressource | Identité déjà présente entraînant un refus |
|---|---|
| Option globale | Même nom de directive, y compris dans les includes du bloc `options` |
| Zone | Même nom DNS canonique, quelle que soit la casse ou le type, y compris les includes |
| Record | Même nom propriétaire dans la même zone, **quel que soit le type DNS** |
| Fichier d'une nouvelle zone | Chemin déjà occupé ; aucune adoption ni suppression automatique |

La dernière règle sur les records est un durcissement volontaire du contrat
précédent, qui ne refusait que le même couple nom/type. Par exemple, créer
`app A` lorsque `app AAAA` existe est désormais refusé. Un même nom dans deux
zones distinctes reste distinct. `update` et `delete` conservent leur portée
sur l'ensemble nom/type sélectionné ; ils exigent que cet ensemble existe.
L'ajout d'un autre type sur un nom existant nécessite une intervention explicite
hors de `create`, avec la procédure opérateur ci-dessous ; aucun mode implicite
de contournement n'est ajouté.

Les options incluses peuvent être lues/listées avec leur provenance. Le manager
refuse leur duplication et leur modification via ce workflow lorsqu'elles sont
dans un fichier externe. Une option présente plusieurs fois est ambiguë et
refusée pour lecture ciblée ou modification. Les lots utilisent les mêmes règles
contre le snapshot initial et le candidat construit dans l'ordre demandé : une
création conflictuelle annule la préparation de tout le lot, sans publication.

## Vérifications aux frontières d'écriture

La lecture vérifie la stabilité de chaque fichier pendant son ouverture puis
recontrôle le graphe observé pour ne pas retourner un catalogue assemblé à partir
d'états différents. L'identité observée comprend le périphérique, l'inode, la
taille, le propriétaire, le groupe, le mode, les liens, mtime et ctime.
Les empreintes publiques restent des SHA-256 du contenu statique ; les contrôles
d'identité et de métadonnées sont des protections supplémentaires pendant
l'exécution, sans promettre une signature ou une authentification de root.

Le répertoire officiel EL8 `/var/named`, root:named avec le mode `1770`, est
accepté sans changement de permissions. Son sticky bit interdit aux membres
du groupe de supprimer ou remplacer les entrées appartenant à root. Cette
exception est limitée à ce chemin et à ce mode exact ; `0770`, `1777` et les
autres ancêtres inscriptibles restent refusés. Chaque fichier statique lu reste
exigé régulier, root, sans lien supplémentaire ni écriture groupe/autres.

Avant publication, après la sauvegarde, avant chaque fichier, après chaque
publication et après les contrôles DNS, le manager vérifie les dépendances live,
y compris celles qui ne sont pas modifiées par la demande. Il vérifie aussi
l'identité de son verrou avant les mutations. Un journal `.jnl`, même représenté
par un lien pendant, bloque la maintenance statique.

Le contrôle de la cible est répété **après** l'écriture/fsync du fichier temporaire,
juste avant son remplacement. Les créations utilisent un lien atomique qui
échoue si le chemin est occupé. Les nouvelles données précèdent leur déclaration ;
les purges suivent le retrait de la déclaration. Le candidat complet est contrôlé
par `named-checkconf -z` avant publication. Le mode simulation ne publie pas les
fichiers DNS et ne recharge pas le service.

La publication enregistre l'identité du fichier effectivement posé avant les
opérations susceptibles d'échouer ensuite. Le rollback n'agit jamais sur une
création qui a échoué avant publication. Un fichier remplacé par un opérateur
est conservé même si ses octets correspondent au candidat. Une modification
de contenu, d'identité ou de permissions interdit sa restauration automatique.
Les autres fichiers appartenant encore à la transaction peuvent être restaurés.
Le graphe réellement restauré est validé avant son rechargement. Une reprise
incomplète conserve le journal et le verrou avec `recovery-required`.

Les contrôles répétés augmentent les lectures proportionnellement au nombre de
fichiers modifiés et à la taille du graphe statique. Aucune amélioration de débit
ou disponibilité sous charge n'est revendiquée.

## Limite de concurrence et protocole opérateur

Routes et management prennent le même verrou de processus `operations.flock`.
Sa fin libère l’exclusion, même après SIGKILL. Les checksums et instantanés
détectent les conflits observables, mais root peut écrire entre deux contrôles.
Le déploiement complet exige une fenêtre exclusive : aucune transaction
distribuée ne couvre la paire ou la publication de plusieurs fichiers.

Pour une intervention manuelle, suspendre toutes les automatisations et maintenir
cette fenêtre exclusive jusqu’à la recette. Sauvegarder la configuration et les
zones avant modification. Sous root, valider avec `named-checkconf -z`, appliquer
les labels avec `restorecon`, recharger par `rndc -s 127.0.0.1 reload`, puis vérifier
les réponses exactes en UDP/TCP, les SOA et la réplication. Le seul code retour de
`dig` ne démontre pas la santé DNS. Pour reprendre une transaction, comparer les
fichiers aux copies et manifestes, conserver les changements manuels et restaurer
uniquement les cibles nécessaires selon [MANAGEMENT.md](MANAGEMENT.md).

Le fichier vide d’exclusion ne doit pas être supprimé. Un journal incomplet est
une donnée de reprise : la libération automatique de l’exclusion ne justifie
jamais sa suppression ou une restauration complète au-dessus des changements
opérateur. L’ancien `.operation.lock` reste réservé au mode historique optionnel
et à l’hibernation.

Les résultats propres à cette révision et les contrôles non exécutés sont dans
[REFONTE_ROUTES_MANAGEMENT.md](REFONTE_ROUTES_MANAGEMENT.md).
