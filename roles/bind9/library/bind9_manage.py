#!/usr/bin/python
"""Expose the targeted management transaction through native Ansible transport."""
from __future__ import absolute_import, division, print_function

import errno
import grp

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.bind9_management import BatchManager, Manager, ManagementError

DOCUMENTATION = r'''
module: bind9_manage
short_description: Execute one revision-checked static BIND maintenance operation
description:
  - Preserve unrelated configuration and manual changes using concrete spans.
  - Validate a complete isolated candidate before guarded publication and reload.
options:
  request:
    description: One CRUD request or an ordered batch; writes require revision checksums and batches require a plan checksum.
    type: dict
    required: true
  context:
    description: Existing site identities; never a new secret or desired full configuration.
    type: dict
    default: {}
  operation_lock:
    description: Acquire the shared host lease. Disable only when an operator serializes all operations externally.
    type: bool
    default: true
author:
  - Bind9-Ansible maintainers
'''
EXAMPLES = r'''
- name: Read the effective revision before editing
  bind9_manage:
    request:
      resource: option
      action: read
      name: minimal-responses

- name: List explicit zones, types and options
  bind9_manage:
    request:
      resource: zone
      action: list
      limit: 100

- name: Preview several resource types without publishing
  bind9_manage:
    request:
      resource: batch
      action: read
      operations:
        - resource: option
          action: update
          name: minimal-responses
          value: 'yes'
        - resource: record
          action: update
          zone: example.test
          name: app
          type: A
          values: ['192.0.2.80']
'''
RETURN = r'''
checksum:
  description: SHA-256 revision of the static configuration/include/master data set.
  returned: success
  type: str
paths:
  description: Files changed by this operation, including necessary delegation/SOA updates.
  returned: changed
  type: list
plan_checksum:
  description: Approval digest binding the initial revision, ordered batch, context and final candidate.
  returned: batch success
  type: str
items:
  description: Explicit declarations or primary records on the requested listing page.
  returned: list success
  type: list
catalog:
  description: Listing identities without option or record values, safe for default display.
  returned: list success
  type: list
'''


def main():
    """Keep sensitive transport output hidden; return only controlled errors."""
    module = AnsibleModule(argument_spec={
        'request': {'type': 'dict', 'required': True},
        'context': {'type': 'dict', 'default': {}},
        'operation_lock': {'type': 'bool', 'default': True}}, supports_check_mode=True)
    try:
        context = dict(module.params['context'], named_gid=grp.getgrnam('named').gr_gid)
        request = module.params['request']
        manager_type = BatchManager if request.get('resource') == 'batch' else Manager
        manager = manager_type(request, context, check=module.check_mode)
        # Keep the low-level API locked by default. The role explicitly selects
        # the temporary operator-requested single-operation mode when disabled.
        if module.params['operation_lock']:
            with manager.lease():
                result = manager.run()
        else:
            result = manager.run()
        module.exit_json(**result)
    except ManagementError as error:
        module.fail_json(msg=str(error))
    except OSError as error:
        # Keep an actionable OS cause without filenames, stdout or secrets.
        code = errno.errorcode.get(error.errno, 'UNKNOWN')
        module.fail_json(msg='Management I/O failed: ' + code + ' (errno ' + str(error.errno) +
                         '). Inspect live state and the private recovery journal before retrying.')
    except Exception:
        # Native checker output and tracebacks may contain keys/configuration.
        module.fail_json(msg='Management refused an unsafe, unsupported or unavailable input/runtime. No configuration details are printed; inspect the protected recovery journal if a transaction began.')


if __name__ == '__main__':
    main()
