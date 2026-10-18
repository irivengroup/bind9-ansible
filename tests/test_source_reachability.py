"""Keep shipped Ansible definitions and role assets connected to runtime entry points.

These checks cover this role's explicit include/filter contracts. They do not
claim to infer arbitrary Ansible reflection or external consumers of its API.
"""
import ast
from collections import Counter
from pathlib import Path
import re

import yaml


class ExpandedYamlDumper(yaml.SafeDumper):
    """Count each runtime use of a shared YAML block, not one serialized anchor."""

    def ignore_aliases(self, data):
        return True


class TestSourceReachability:
    """Audit production references independently of documentation and test names."""

    ROOT = Path(__file__).resolve().parents[1]
    ROLE = ROOT / 'roles/bind9'

    @classmethod
    def runtime_text(cls):
        """Remove YAML/Jinja comments so comments cannot keep dead code alive."""
        chunks = []
        for folder in ('roles', 'inventory', 'playbooks'):
            for path in (cls.ROOT / folder).rglob('*'):
                if path.suffix == '.yml':
                    chunks.append(yaml.dump(yaml.safe_load(path.read_text()), Dumper=ExpandedYamlDumper))
                elif path.suffix == '.j2':
                    content = re.sub(r'{#.*?#}', '', path.read_text(), flags=re.S)
                    chunks.append(re.sub(r'//[^\n]*', '', content))
        return '\n'.join(chunks)

    def test_assigned_variables_and_registered_results_have_consumers(self):
        text = self.runtime_text()
        occurrences = Counter(re.findall(r'\bbind_[a-z_0-9]+\b', text))
        definitions = Counter()

        def collect(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if isinstance(key, str) and key.startswith('bind_'):
                        definitions[key] += 1
                    if key == 'register' and isinstance(item, str) and item.startswith('bind_'):
                        definitions[item] += 1
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)

        for folder in ('roles', 'inventory', 'playbooks'):
            for path in (self.ROOT / folder).rglob('*.yml'):
                collect(yaml.safe_load(path.read_text()))
        assert definitions
        assert not [name for name, count in definitions.items() if occurrences[name] <= count]

    def test_reused_yaml_blocks_do_not_hide_variable_consumers(self, tmp_path, monkeypatch):
        # Consolidating the legacy adapter creates YAML aliases. Definitions and
        # references must both be counted after expansion; assertions stay strict.
        folder = tmp_path / 'roles'
        folder.mkdir()
        shared = {'vars': {'bind_example': 1}, 'debug': {'msg': '{{ bind_example }}'}}
        (folder / 'main.yml').write_text(yaml.safe_dump([shared, shared]))
        monkeypatch.setattr(type(self), 'ROOT', tmp_path)
        self.test_assigned_variables_and_registered_results_have_consumers()

    def test_every_task_file_is_reachable_from_the_role_entry_point(self):
        pending = [self.ROLE / 'tasks/main.yml', self.ROLE / 'tasks/manage.yml']
        visited = set()
        while pending:
            path = pending.pop()
            if path in visited:
                continue
            assert path.is_file(), str(path)
            visited.add(path)

            def includes(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key in ('ansible.builtin.import_tasks', 'ansible.builtin.include_tasks'):
                            name = item['file'] if isinstance(item, dict) else item
                            name = name.replace('{{ role_path }}/tasks/', '')
                            pending.append(self.ROLE / 'tasks' / name)
                        includes(item)
                elif isinstance(value, list):
                    for item in value:
                        includes(item)

            includes(yaml.safe_load(path.read_text()))
        assert visited == set((self.ROLE / 'tasks').rglob('*.yml'))

    def test_every_deployed_asset_and_handler_has_a_runtime_reference(self):
        text = self.runtime_text()
        for folder in ('files', 'templates'):
            for path in (self.ROLE / folder).iterdir():
                if path.is_file() and path.suffix != '.pyc':
                    assert path.name in text, str(path)
        for handler in yaml.safe_load((self.ROLE / 'handlers/main.yml').read_text()):
            assert 'notify: ' + handler['name'] in text

    def test_every_exported_filter_is_consumed_by_the_role(self):
        text = self.runtime_text()
        exports = set()
        for path in (self.ROLE / 'filter_plugins').glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.FunctionDef) and node.name == 'filters':
                    for child in ast.walk(node):
                        if isinstance(child, ast.Dict):
                            exports.update(key.s for key in child.keys if isinstance(key, ast.Str))
        assert exports
        assert not [name for name in exports if not re.search(r'\b' + name + r'\b', text)]
