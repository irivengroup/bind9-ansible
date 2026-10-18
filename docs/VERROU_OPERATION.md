# Diagnostic du verrou d’opération

## Nature et acquisition

`/var/lib/Bind9-Ansible/.operation.lock` est un **répertoire**, partagé par le
déploiement et le management. Il protège une opération à la fois. Il ne s’agit
pas du fichier persistant `.deployment.lock`, qui interdit les redéploiements
après hibernation.

Le dossier parent est déjà créé avec `ansible.builtin.file`, propriétaire
`root:root`, mode `0700`, après contrôle des chemins existants. L’acquisition
emploie `/usr/bin/mkdir -m 0700 -- /var/lib/Bind9-Ansible/.operation.lock` :
la création doit échouer si le chemin existe. `file: state=directory`,
`mkdir -p` ou `creates:` accepteraient un répertoire préexistant et ne
permettraient pas d’obtenir cette exclusivité.

Un `rc: 1` seul ne permet pas d’identifier la cause. Le garde enrichit désormais
l’échec avec le code retour, le diagnostic original et le type du chemin observé
sans suivre les liens ni lire le contenu du verrou. Il arrête l’opération sans
revendiquer ni libérer un verrou dont la création a échoué. L’observation du
chemin intervient après l’échec ; son existence peut évoluer entre les deux.

La révision du parcours de routage ajoute aussi une observation juste avant
l’acquisition et un affichage structuré : cible Ansible, commande, `rc`,
`stderr`, `exists_before` et `exists_after`. Un chemin absent après l’échec ne
reçoit plus l’avertissement générique sur un répertoire occupé. Pour une
opération `routes`, aucune reconstruction DNS n’est demandée ; voir
[ROUTES_SEULES_ET_VERROU.md](ROUTES_SEULES_ET_VERROU.md).

| Observation | Action opérateur |
|---|---|
| `File exists`, répertoire | Rechercher une opération en cours ou une récupération interrompue. L’ancien verrou est conservé. |
| Fichier, lien symbolique ou fichier spécial | Chemin inattendu : inspecter sa provenance ; aucun remplacement automatique. |
| `Permission denied`, système en lecture seule ou autre erreur | Traiter la cause affichée, y compris une éventuelle politique SELinux. Ne pas supprimer un verrou pour contourner cette erreur. |
| Chemin absent après l’échec | Lire le diagnostic initial ; cela ne prouve ni une concurrence ni un verrou abandonné. |

Inspection en lecture seule sur le serveur :

```bash
sudo ls -ld -- /var/lib/Bind9-Ansible /var/lib/Bind9-Ansible/.operation.lock
sudo stat -- /var/lib/Bind9-Ansible/.operation.lock
```

Ne pas confondre un refus d’acquisition avec un échec de configuration DNS :
le corps du déploiement n’a pas encore commencé. Un nouvel essai ne doit être
lancé qu’après traitement de la cause.

## Récupération contrôlée

L’âge du répertoire ou l’absence d’un processus local ne prouve pas qu’il est
abandonné : un contrôleur distant peut encore travailler. Le rôle ne le vole
jamais et n’ajoute aucun déverrouillage automatique.

Si le chemin réapparaît après son retrait, suspendre les nouvelles tentatives
et rechercher quelle exécution le recrée. Le playbook standard livré importe
le garde une seule fois. Cela ne démontre pas ce qui s’exécute sur un contrôleur
ou serveur inaccessible, ni que l’opération précédente a fini sa récupération.

Avant toute intervention, suspendre les autres automatismes et confirmer avec
leurs responsables qu’aucune opération ne travaille sur cet hôte. Identifier
l’opération interrompue, préserver les fichiers live et ses journaux de reprise,
et suivre la [procédure de configuration live](INTEGRITE_CONFIGURATION_LIVE.md).
Une récupération DNS ou FRONT incomplète peut volontairement conserver le
verrou. Si `front-routes.lock` existe, suivre aussi la
[procédure FRONT](FRONT_ROUTES.md) ; ne pas supprimer ce journal.

Après ces contrôles uniquement, un opérateur peut retirer un répertoire
`.operation.lock` confirmé abandonné et **vide**, sans retrait récursif :

```bash
sudo rmdir -- /var/lib/Bind9-Ansible/.operation.lock
```

L’échec de cette commande exige une inspection ; ne pas le contourner avec
`rm -rf`. Cette action ne restaure pas les données, ne retire pas le sceau
`.deployment.lock` et n’autorise pas à écraser une configuration modifiée
manuellement. Une récupération doit réacquérir son propre verrou.

## Portée de la correction

Seul `roles/bind9/tasks/tasks.d/deploy/guard.yml` change dans le code de
production : chemin explicite du binaire RHEL 8, acquisition exclusive
inchangée, bloc de diagnostic et arrêt explicite. L’argument `warn: false`
désactive uniquement la suggestion générique de remplacer cette commande par
`file` sous Ansible 2.9 ; il est omis pour les versions ≥ 2.10. Les erreurs
d’acquisition restent bloquantes et visibles. Les règles de libération par
identité du répertoire, l’hibernation, le manager, les routes et le template
DNS restent identiques. Les tests ajoutés couvrent les chemins occupés de
différents types et la restitution d’erreurs système simulées, dans les deux
modes Jinja d’Ansible 2.9.

Les résultats de cette livraison et les contrôles restant à effectuer sont dans
le [rapport courant du parcours routes](ROUTES_SEULES_ET_VERROU.md). Le
[rapport de la livraison source](CORRECTION_VERROU_ET_VALIDATION.md) reste
un historique des contrôles précédents.
