# Vérification des routes externes

L’implémentation actuelle est exclusivement en tâches Ansible/Jinja standard, sans filtre Python personnalisé et sans netaddr. La persistance passe par `nmcli connection modify`, l’état actif par `ip -4 route add/del`, sans activation de la connexion. Les destinations proviennent exclusivement de `bind_cloud_clients` ; les clients internes et la fusion DNS ne sont jamais passés au plan de routes FRONT. Les tags et le format des journaux de reprise sont conservés ; le changement de contrat client est décrit dans [CLIENTS_UNIQUES.md](CLIENTS_UNIQUES.md).

**EXÉCUTÉ** : 77 tests d’expressions Ansible ; workflows CLI isolés avec erreurs, stdout vide, idempotence, check mode et rollback/reprise ; quatre workflows NetworkManager réels avec persistance et préservation de BACK/ADM/défaut. Les preuves courantes sont dans `DELIVERY.json` et [CLIENTS_UNIQUES.md](CLIENTS_UNIQUES.md). L’historique de l’implémentation native est conservé dans [VALIDATION_ROUTES_NATIVES.md](VALIDATION_ROUTES_NATIVES.md).

```bash
python -m pytest -q tests/test_native_front_routes.py
sudo python -m pytest -q tests/test_front_routes_ansible.py
sudo bash tests/run_integration_front_routes.sh
```

**NON EXÉCUTÉ** : recette réseau/SSH/reboot sur vos serveurs RHEL, versions NM/iproute2 réelles et restauration DNS opérationnelle. Aucune cible accessible ; prérequis, commandes, attendus et risques : [VALIDATION_ROUTES_NATIVES.md](VALIDATION_ROUTES_NATIVES.md). La qualification de production reste **NON TERMINÉE**.
