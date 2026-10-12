# Organisation des tâches

Le rôle conserve `roles/bind9/tasks/main.yml` comme point d’entrée. Tous les autres fichiers de tâches sont dans `roles/bind9/tasks/tasks.d/`. Les imports statiques du point d’entrée utilisent `tasks.d/<nom>.yml`.
