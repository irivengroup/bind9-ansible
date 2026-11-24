"""Expose the shared resource request model to controller-side deployment."""
import importlib.util
from pathlib import Path


class FilterModule:
    """Load the role's canonical planner without requiring a target connection."""

    def filters(self):
        return {'resources_deploy': self.compile}

    @staticmethod
    def compile(request, context):
        source = Path(__file__).resolve().parents[1] / 'module_utils/bind9_management.py'
        spec = importlib.util.spec_from_file_location('bind9_deployment_resources', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.DeploymentResources(request, context).compile()
