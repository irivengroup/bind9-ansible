#!/usr/bin/python
"""Plan deployment customization with the existing native management validator."""
from __future__ import absolute_import, division, print_function

import errno

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.bind9_management import BatchManager, Configuration, ManagementError

DOCUMENTATION = r'''
module: bind9_deploy_resources
short_description: Validate deployment options, ACLs and logging before publication
description:
  - Read the newly generated deployment baseline and its real include graph.
  - Use the same planners and isolated native validation as resource management.
  - Return private candidate configuration; the deployment transaction owns publication and rollback.
options:
  operations:
    description: Ordered option, acl and logging write operations.
    type: list
    elements: dict
    required: true
  context:
    description: Validated site identities and deployment paths.
    type: dict
    required: true
author:
  - Bind9-Ansible maintainers
'''
EXAMPLES = r'''
- name: Validate custom deployment options before guarded publication
  bind9_deploy_resources:
    operations:
      - resource: option
        action: update
        name: max-cache-size
        value: 128m
    context: {}
  no_log: true
'''
RETURN = r'''
content:
  description: Private complete candidate named.conf; never display it.
  returned: success
  type: str
checksum:
  description: Revision of the observed baseline, checked again after validation.
  returned: success
  type: str
'''


def main():
    """Validate without publishing, reloading or claiming a live transaction."""
    module = AnsibleModule(argument_spec={
        'operations': {'type': 'list', 'elements': 'dict', 'required': True},
        'context': {'type': 'dict', 'required': True}}, supports_check_mode=True)
    try:
        operations = module.params['operations']
        if any(operation.get('resource') not in ('option', 'acl', 'logging') for operation in operations):
            raise ManagementError('Deployment customization accepts option, acl and logging only')
        manager = BatchManager(dict(resource='batch', action='read', operations=operations), module.params['context'])
        manager.config = Configuration(manager.files)
        revision = manager.config.revision()
        manager.plan()
        manager.validate()
        if set(manager.changes) - {'/etc/named.conf'}:
            raise ManagementError('A customization cannot publish another asset')
        if Configuration(manager.files).revision() != revision:
            raise ManagementError('Deployment baseline changed during customization validation')
        content = manager.changes.get('/etc/named.conf', manager.config.contents['/etc/named.conf'])
        module.exit_json(changed=False, checksum=revision, content=content.decode('utf-8'))
    except ManagementError as error:
        module.fail_json(msg=str(error))
    except OSError as error:
        module.fail_json(msg='Deployment customization I/O failed: ' + errno.errorcode.get(error.errno, 'UNKNOWN'))
    except Exception:
        module.fail_json(msg='Deployment customization refused an unsafe or unavailable input/runtime. No configuration content is printed.')


if __name__ == '__main__':
    main()
