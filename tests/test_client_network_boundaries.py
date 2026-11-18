"""Reject prohibited address overlap even when a supernet has unicast ends."""
import importlib.util
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
source = Path(os.environ.get('BIND9_CLIENT_BASELINE', str(ROOT / 'roles/bind9/filter_plugins/dns_topology.py')))
spec = importlib.util.spec_from_file_location('client_network_boundary_contract', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize('network', ['255.255.255.255', '255.255.255.255/32', '255.255.255.254/31',
    '128.0.0.0/1', '0.0.0.0/1', '169.254.0.0/15', '224.0.0.0/3', '0.0.0.0/8'])
def test_prohibited_ranges_cannot_be_hidden_inside_a_client_supernet(network):
    assert module.FilterModule.client(network) is False


@pytest.mark.parametrize('network', ['192.0.2.0/24', '10.0.0.0/8', '127.0.0.1', '169.253.0.0/16',
    '169.255.0.0/16', '223.255.255.255', '255.255.255.254/32', '0.0.0.1/32'])
def test_adjacent_previously_supported_unicast_inputs_remain_accepted(network):
    assert module.FilterModule.client(network) is True
