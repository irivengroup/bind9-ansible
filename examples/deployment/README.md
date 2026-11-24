# Déployer une configuration de ressources complète

[Retour au guide des ressources](../../docs/wiki.d/configuration/bind/RESSOURCES.md)

Depuis la racine du projet, adapter les adresses du [lot](batch.yml), les interfaces et les clients de l’inventaire, puis exécuter :

```bash
ansible-playbook -i inventory/dev/hosts.ini playbooks/bind9-deploy.yml \
  -e @examples/deployment/batch.yml --tags deploy
```

Le lot contient options, ACL, journalisation, autorité, enregistrements et forwarding. Il décrit l’état neuf ; les options et le bloc logging déjà présents emploient `update`. Les anciens noms de listes de zones sont refusés. Pour modifier ensuite une installation existante, utiliser `bind_resources_manage` et ses empreintes de révision et de plan.
