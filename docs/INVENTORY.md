# Inventaire Bind9Project

- `inventory/hosts.ini` : groupes et noms des quatre hôtes, sans paramètres.
- `inventory/group_vars/zibo_x1.yml` et `zibo_x3.yml` : paramètres communs au site.
- `inventory/host_vars/<hostname>.yml` : `ansible_host`, `bind_front_ip`, `bind_back_ip`, `bind_role`.

Les IP sont fictives. Adapter les valeurs avant déploiement. Ansible charge automatiquement `host_vars` adjacent à l'inventaire.

```bash
ansible-inventory -i inventory/hosts.ini --graph
ansible-inventory -i inventory/hosts.ini --host zibo-x1-dns01
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --syntax-check
```
