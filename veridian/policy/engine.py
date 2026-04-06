"""
veridian.policy.engine
───────────────────────
PolicyEngine: runtime policy evaluation and management.

Usage::

    engine = PolicyEngine()
    engine.load_yaml(yaml_str)          # add a YAML-defined policy
    engine.load_builtins()              # load all 10 built-in templates

    result = engine.evaluate("gdpr_no_pii_output", task, task_result)
    all_results = engine.evaluate_all(task, task_result)
"""

from __future__ import annotations

from veridian.core.exceptions import PolicyNotFound
from veridian.core.task import Task, TaskResult
from veridian.policy.compiler import PolicyCompiler
from veridian.policy.models import BUILTIN_POLICIES, Policy
from veridian.verify.base import BaseVerifier, VerificationResult

__all__ = ["PolicyEngine"]


class PolicyEngine:
    """
    Runtime registry and evaluator for compiled policy verifiers.

    Maintains a mapping of policy_id → (Policy metadata, compiled verifier class).
    """

    def __init__(self) -> None:
        self._compiler = PolicyCompiler()
        # policy_id → (Policy, type[BaseVerifier])
        self._registry: dict[str, tuple[Policy, type[BaseVerifier]]] = {}

    def load_yaml(self, yaml_text: str) -> Policy:
        """
        Parse and register a YAML policy.

        Returns the parsed Policy model.
        Raises PolicyCompilationError on parse/validation errors.
        """
        policy = self._compiler._parse_yaml(yaml_text)
        verifier_cls = self._compiler.compile(policy)
        self._registry[policy.policy_id] = (policy, verifier_cls)
        return policy

    def load_builtins(self) -> None:
        """Load all built-in policy templates into the registry."""
        for policy in BUILTIN_POLICIES:
            verifier_cls = self._compiler.compile(policy)
            self._registry[policy.policy_id] = (policy, verifier_cls)

    def has_policy(self, policy_id: str) -> bool:
        """Return True iff the policy_id is registered."""
        return policy_id in self._registry

    def get_policy(self, policy_id: str) -> Policy | None:
        """Return the Policy metadata for the given ID, or None if not found."""
        entry = self._registry.get(policy_id)
        return entry[0] if entry else None

    def list_policies(self) -> list[str]:
        """Return a sorted list of all registered policy IDs."""
        return sorted(self._registry.keys())

    def evaluate(
        self,
        policy_id: str,
        task: Task,
        result: TaskResult,
    ) -> VerificationResult:
        """
        Evaluate a single policy against (task, result).

        Raises PolicyNotFound if policy_id is not registered.
        """
        entry = self._registry.get(policy_id)
        if entry is None:
            raise PolicyNotFound(
                f"Policy {policy_id!r} not found. Registered: {self.list_policies()}"
            )
        _, verifier_cls = entry
        verifier: BaseVerifier = verifier_cls()
        return verifier.verify(task, result)

    def evaluate_all(
        self,
        task: Task,
        result: TaskResult,
    ) -> dict[str, VerificationResult]:
        """
        Evaluate all registered policies against (task, result).

        Returns a mapping of policy_id → VerificationResult.
        """
        return {
            policy_id: self.evaluate(policy_id, task, result) for policy_id in self.list_policies()
        }
