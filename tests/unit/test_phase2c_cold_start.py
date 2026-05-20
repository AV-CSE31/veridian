"""
tests.unit.test_phase2c_cold_start
------------------------------------------------------------------------------------------------------
Pin the Phase 2.C cold-start optimisations.

The cold-start observations require purging ``veridian.*`` from
``sys.modules`` to simulate a fresh interpreter. That would break any
subsequent tests in the same pytest session, so the import-footprint
assertions are run in a subprocess and the in-process tests only
exercise lazy-attribute resolution against an already-loaded ``veridian``
(which is the operationally interesting code path anyway).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


class TestLazyTopLevelAccess:
    def test_taskledger_attribute(self) -> None:
        import veridian

        cls = veridian.TaskLedger  # triggers __getattr__ when absent
        assert cls.__name__ == "TaskLedger"

    def test_litellm_provider_attribute(self) -> None:
        import veridian

        cls = veridian.LiteLLMProvider
        assert cls.__name__ == "LiteLLMProvider"

    def test_quickstart_imports_still_work(self) -> None:
        # Quick Start shape from the package docstring.
        from veridian import LiteLLMProvider, Task, TaskLedger, verified  # noqa: F401


class TestImportFootprint:
    def test_lazy_modules_unloaded_until_first_access(self) -> None:
        """Spawn a clean interpreter and assert ``import veridian`` alone
        does not load ``veridian.providers.litellm_provider`` or
        ``veridian.ledger.ledger`` --- they must only load on first
        attribute access.
        """
        script = textwrap.dedent(
            """
            import sys, json
            import veridian
            before_litellm = "veridian.providers.litellm_provider" in sys.modules
            before_ledger = "veridian.ledger.ledger" in sys.modules
            before_decorators = "veridian.decorators" in sys.modules
            before_builtin = any(name.startswith("veridian.verify.builtin.") for name in sys.modules)
            _ = veridian.TaskLedger
            _ = veridian.LiteLLMProvider
            _ = veridian.verified
            after_litellm = "veridian.providers.litellm_provider" in sys.modules
            after_ledger = "veridian.ledger.ledger" in sys.modules
            after_decorators = "veridian.decorators" in sys.modules
            print(json.dumps({
                "before_litellm": before_litellm,
                "before_ledger": before_ledger,
                "before_decorators": before_decorators,
                "before_builtin": before_builtin,
                "after_litellm": after_litellm,
                "after_ledger": after_ledger,
                "after_decorators": after_decorators,
            }))
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
        )
        import json

        flags = json.loads(result.stdout.strip().splitlines()[-1])
        assert flags["before_litellm"] is False, (
            "veridian.providers.litellm_provider must remain unloaded after "
            "`import veridian` (Phase 2.C lazy boundary regressed)"
        )
        assert flags["before_ledger"] is False, (
            "veridian.ledger.ledger must remain unloaded after `import veridian`"
        )
        assert flags["before_decorators"] is False
        assert flags["before_builtin"] is False
        assert flags["after_litellm"] is True
        assert flags["after_ledger"] is True
        assert flags["after_decorators"] is True
