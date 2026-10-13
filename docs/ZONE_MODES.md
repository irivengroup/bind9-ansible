# Modes de zones DNS

`bind_forward_zones` et `bind_authoritative_zones` sont deux listes indépendantes.

| Mode | bind_forward_zones | bind_authoritative_zones |
| --- | --- | --- |
| Forward-only | liste de zones | `[]` |
| Authoritative-only | `[]` | liste de zones |
| Mixte | liste de zones | liste de zones |
| Préparation | `[]` | `[]` |

Les ACL clients Route 53 sont configurées via `bind_frontend_clients`.
Les adresses ADM restent exclusivement dans `ansible_host` des `host_vars` et ne sont jamais utilisées dans BIND.

