"""Construct current deployment inputs through the real public resource schema."""
import importlib.util
from pathlib import Path


def role_module(relative):
    """Load an actual role module without adding pytest to integration tools."""
    path = Path(__file__).resolve().parents[1] / 'roles/bind9' / relative
    spec = importlib.util.spec_from_file_location('resource_input_' + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compiler():
    module = role_module('filter_plugins/resources_deploy.py')
    return module.FilterModule().filters()['resources_deploy']


def materialize_zone(zone, primary='192.0.2.11', secondary='192.0.2.12'):
    """Preserve existing template scenarios while supplying their compiled data."""
    request = dict(resource='zone', action='create', kind='authoritative', zone=zone)
    context = dict(primary_front=primary, secondary_front=secondary, primary_back='10.0.0.11', secondary_back='10.0.0.12')
    return compiler()(request, context)['authoritative_zones'][0]


def deployment_context(values):
    """Use actual per-peer IPs and the delivered inventory declaration."""
    peers = values['hostvars']
    primary, secondary = peers[values['bind_primary_host']], peers[values['bind_secondary_host']]
    context = dict(primary_front=primary['bind_front_ip'], secondary_front=secondary['bind_front_ip'],
        primary_back=primary['bind_back_ip'], secondary_back=secondary['bind_back_ip'])
    compiled = compiler()(values['bind_resources_deploy'], context)
    values.update(_bind_deploy_authoritative_zones=compiled['authoritative_zones'],
                  _bind_deploy_forward_zones=compiled['forward_zones'], _bind_deploy_extra_operations=compiled['extra_operations'])


def edit_zone_data(zone, record=None, serial=None):
    """Apply a fixture's record edit to compiled bytes and synchronize its serial."""
    module = role_module('module_utils/bind9_management.py')
    domain = module.canonical_name(zone['domain'])
    data = zone['data']
    if record is not None:
        owner = module.canonical_name(record['name'], domain)
        data = module.ZoneDocument(data, domain).change('upsert', owner, record['type'],
                                                       [record['value']], bump_serial=False)
    if serial is not None:
        token = module.ZoneDocument(data, domain).serial()
        data = data[:token.start] + str(serial) + data[token.end:]
        zone['serial'] = serial
    zone['data'] = data


def rebase_generated_nameservers(context):
    """Move only generated NS addresses into the integration loopback lab."""
    for zone in context['_bind_deploy_authoritative_zones']:
        for name, peer in [('ns1', context['bind_primary_host']), ('ns2', context['bind_secondary_host'])]:
            if not any(row['name'] == name and row['type'] == 'A' for row in zone['records']):
                edit_zone_data(zone, dict(name=name, type='A', value=context['hostvars'][peer]['bind_front_ip']))
