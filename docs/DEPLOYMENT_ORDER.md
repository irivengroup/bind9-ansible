# One play / one role / four servers

`playbooks/bind9-deploy.yml` invokes `bind9` once. `dns_deployment_order` explicitly lists
both primaries before both secondaries. `order: inventory` and `serial: 1`
preserve this order; do not use `--limit` to reorder or bypass dependencies.

Before any destructive migration of a secondary, the role checks its local
primary TCP/53 and an authoritative SOA response. This assumes that the primary
accepts back-interface queries from the secondary. If back queries are prohibited,
the topology/policy must be revised rather than bypassing the check.

This is not an atomic multi-host transaction. Existing DNS availability,
backup integrity and rollback must be tested before production deployment.

Example:
`ansible-playbook -i inventory/hosts.ini playbooks/bind9-deploy.yml --ask-vault-pass`

