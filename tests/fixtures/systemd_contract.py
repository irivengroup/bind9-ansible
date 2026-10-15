#!/usr/bin/env python3
"""Filesystem-isolated unit contract double; this never controls real systemd.

The workflow adapter preserves production conditions and module arguments.
This double records unit state so file ownership, cleanup and rollback can be
exercised by a real Ansible runner when RHEL/systemd are unavailable.
"""
import json
from pathlib import Path
import sys


class UnitContract:
    """Model the small unit API used by the role, without daemon/boot claims."""

    def run(self):
        state=Path(sys.argv[1])
        data=json.loads(state.read_text())
        if sys.argv[2] in ('is-active','is-enabled'):
            operation,name=sys.argv[2:4]
            unit=data['units'].get(name,{'active':False,'enabled':False})
            if operation=='is-active':
                print('active' if unit['active'] else 'inactive')
                return 0 if unit['active'] else 3
            print('enabled' if unit['enabled'] else 'disabled')
            return 0 if unit['enabled'] else 1
        request=json.loads(sys.argv[2])
        before=json.dumps(data['units'],sort_keys=True)
        name=request.get('name')
        if name:
            unit=data['units'].setdefault(name,{'active':False,'enabled':False})
            if request.get('state') in ('started','restarted'):
                unit['active']=True
            elif request.get('state')=='stopped':
                unit['active']=False
            enabled=request.get('enabled')
            if isinstance(enabled,bool):
                unit['enabled']=enabled
            elif str(enabled).lower() in ('true','false'):
                unit['enabled']=str(enabled).lower()=='true'
        changed=before!=json.dumps(data['units'],sort_keys=True)
        data['operations'].append(request)
        state.write_text(json.dumps(data))
        print(json.dumps({'changed':changed}))
        return 0


if __name__=='__main__':
    sys.exit(UnitContract().run())
