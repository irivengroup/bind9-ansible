"""Run real loader startup on every CI controller, independently of observer pins."""
import pytest


class TestNativeLoaderCompatibility:
    """Missing startup APIs must fail here rather than disable native validation."""

    @pytest.mark.parametrize('mode,expected', [(True, 0), ('yes', 2)])
    def test_native_controller_keeps_success_and_refusal_contracts(self, mode, expected):
        # Import inside the test so a missing version API is an observed failure.
        # The richer observer suite is deliberately pinned to a single compiler.
        from native_ansible_runner import NativeTaskRun

        status, result = NativeTaskRun('tasks.d/dnssec_settings.yml',
                                      {'bind_dnssec_active': mode}).run()
        assert status == expected
        assert result.failed == ([] if expected == 0 else ['Validate the optional DNSSEC mode'])
        assert len(result.ok) == int(expected == 0)
