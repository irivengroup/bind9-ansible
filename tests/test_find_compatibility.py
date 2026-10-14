"""Guard reverse-zone discovery against optional newer Ansible parameters."""
from pathlib import Path
import re
import unittest


class FindCompatibilityTests(unittest.TestCase):
    """Preserve managed-zone filtering without requiring read_whole_file."""

    def test_reverse_discovery_uses_legacy_compatible_parameters(self):
        """Reject the unsupported argument while retaining the ownership marker."""
        source = (Path(__file__).resolve().parents[1] / 'roles/bind9/tasks/main.yml').read_text(encoding='utf-8')
        block = source.split('  ansible.builtin.find:', 1)[1].split('  register:', 1)[0]
        self.assertIsNone(re.search(r'^\s*read_whole_file\s*:', block, re.MULTILINE))
        self.assertIn('paths: /var/named/zones', block)
        self.assertIn("'*.in-addr.arpa.zone'", block)
        self.assertIn("'*.ip6.arpa.zone'", block)
        self.assertIn("contains: '^; Bind9-Ansible-MANAGED-REVERSE$'", block)


if __name__ == '__main__':
    unittest.main()
