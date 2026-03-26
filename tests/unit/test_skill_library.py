"""
Tests for SkillLibrary: Bayesian reliability, atomic persistence, extraction, integration.
Run: pytest tests/unit/test_skill_library.py -x -q --timeout=30
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest

from veridian.core.task import Task, TaskResult, TaskStatus
from veridian.ledger.ledger import TaskLedger
from veridian.providers.mock_provider import MockProvider
from veridian.skills.admission import SkillAdmissionControl
from veridian.skills.extractor import SkillExtractor
from veridian.skills.library import SkillLibrary
from veridian.skills.models import Skill, SkillCandidate, SkillStep
from veridian.skills.store import SkillStore

# ── Test helpers ───────────────────────────────────────────────────────────────


def _hash_embed(text: str) -> list[float]:
    """Deterministic hash-based embedding. Same text → same vector."""
    h = hashlib.sha256(text.encode()).digest()
    return [(b / 127.5) - 1.0 for b in h]


def _constant_embed(text: str) -> list[float]:
    """Always returns the same unit vector — forces cosine_sim = 1.0 between any two."""
    dim = 32
    val = 1.0 / (dim**0.5)
    return [val] * dim


def _make_skill(
    name: str = "Test Skill",
    trigger: str = "doing something repeatable in documents",
    domain: str = "generic",
    confidence: float = 0.9,
) -> Skill:
    return Skill(
        id=str(uuid.uuid4()),
        name=name,
        trigger=trigger,
        domain=domain,
        verifier_id="bash_exit",
        steps=[SkillStep(description="Step 1"), SkillStep(description="Step 2")],
        tools_used=[],
        context_requirements=[],
        confidence_at_extraction=confidence,
        source_task_id="task_001",
        source_run_id="run_001",
    )


def _make_candidate(
    confidence: float = 0.85,
    retry_count: int = 0,
    domain_hint: str = "generic",
    task_title: str = "Extract data from PDF",
    task_description: str = (
        "A repeatable procedure for extracting structured data from PDF documents"
    ),
) -> SkillCandidate:
    return SkillCandidate(
        task_id="task_001",
        run_id="run_001",
        task_title=task_title,
        task_description=task_description,
        verifier_id="bash_exit",
        confidence=confidence,
        retry_count=retry_count,
        bash_outputs=[
            {"cmd": "pdftotext contract.pdf -", "stdout": "text...", "exit_code": 0},
            {"cmd": "grep -i clause output.txt", "stdout": "found", "exit_code": 0},
        ],
        structured_output={"key": "value"},
        domain_hint=domain_hint,
    )


def _make_done_task(
    task_id: str = "t1",
    retry_count: int = 0,
    verifier_id: str = "bash_exit",
    title: str = "Extract clause from PDF",
    description: str = "Extract the change-of-control clause from contract.pdf",
) -> Task:
    result = TaskResult(
        raw_output="done",
        structured={"clause": "no change of control"},
        bash_outputs=[
            {"cmd": "pdftotext contract.pdf -", "stdout": "text output", "exit_code": 0},
            {"cmd": "grep -i control output.txt", "stdout": "found clause", "exit_code": 0},
        ],
        verified=True,
    )
    t = Task(
        id=task_id,
        title=title,
        description=description,
        verifier_id=verifier_id,
        retry_count=retry_count,
        status=TaskStatus.DONE,
    )
    t.result = result
    return t


# ── Model tests ────────────────────────────────────────────────────────────────


class TestSkillModel:
    def test_reliability_score_starts_at_0_5(self) -> None:
        """Default alpha=1, beta_=1 → reliability = 0.5."""
        skill = _make_skill()
        assert skill.reliability_score == pytest.approx(0.5)

    def test_bayesian_lower_bound_lte_reliability_score(self) -> None:
        """Lower bound is always ≤ reliability_score (conservative estimate)."""
        skill = _make_skill()
        assert skill.bayesian_lower_bound <= skill.reliability_score

    def test_record_success_increments_alpha_not_beta(self) -> None:
        """record_success() increments alpha and use_count, not beta_."""
        skill = _make_skill()
        skill.record_success()
        assert skill.alpha == pytest.approx(2.0)
        assert skill.beta_ == pytest.approx(1.0)
        assert skill.use_count == 1

    def test_record_failure_increments_beta_not_alpha(self) -> None:
        """record_failure() increments beta_ and use_count, not alpha."""
        skill = _make_skill()
        skill.record_failure()
        assert skill.alpha == pytest.approx(1.0)
        assert skill.beta_ == pytest.approx(2.0)
        assert skill.use_count == 1

    def test_reliability_improves_after_successes(self) -> None:
        """After several successes, reliability > 0.5."""
        skill = _make_skill()
        skill.record_success()
        skill.record_success()
        assert skill.reliability_score > 0.5

    def test_to_dict_from_dict_roundtrip(self) -> None:
        """Skill serializes and deserializes losslessly."""
        skill = _make_skill()
        skill.record_success()
        restored = Skill.from_dict(skill.to_dict())
        assert restored.id == skill.id
        assert restored.name == skill.name
        assert restored.alpha == pytest.approx(skill.alpha)
        assert restored.beta_ == pytest.approx(skill.beta_)
        assert restored.use_count == skill.use_count
        assert len(restored.steps) == len(skill.steps)
        assert restored.steps[0].description == skill.steps[0].description


# ── SkillStore tests ───────────────────────────────────────────────────────────


class TestSkillStore:
    @pytest.fixture
    def store(self, tmp_path: Path) -> SkillStore:
        return SkillStore(path=tmp_path / "skills.json", embed_fn=_hash_embed)

    def test_save_get_roundtrip(self, store: SkillStore) -> None:
        """save() then get() returns an identical skill."""
        skill = _make_skill()
        skill_id = store.save(skill)
        retrieved = store.get(skill_id)
        assert retrieved is not None
        assert retrieved.id == skill.id
        assert retrieved.name == skill.name
        assert retrieved.domain == skill.domain

    def test_get_nonexistent_returns_none(self, store: SkillStore) -> None:
        """get() returns None for an unknown skill ID."""
        assert store.get("nonexistent-id-xyz") is None

    def test_delete_removes_skill(self, store: SkillStore) -> None:
        """delete() removes skill and returns True."""
        skill = _make_skill()
        store.save(skill)
        assert store.delete(skill.id) is True
        assert store.get(skill.id) is None

    def test_delete_nonexistent_returns_false(self, store: SkillStore) -> None:
        """delete() returns False when skill does not exist."""
        assert store.delete("no-such-id") is False

    def test_list_returns_all_skills(self, store: SkillStore) -> None:
        """list() returns all saved skills."""
        store.save(_make_skill(name="S1", domain="legal"))
        store.save(_make_skill(name="S2", domain="compliance"))
        assert len(store.list()) == 2

    def test_list_filters_by_domain(self, store: SkillStore) -> None:
        """list(domain=...) returns only skills in that domain."""
        store.save(_make_skill(name="S1", domain="legal"))
        store.save(_make_skill(name="S2", domain="compliance"))
        legal = store.list(domain="legal")
        assert len(legal) == 1
        assert legal[0].name == "S1"

    def test_query_ordered_by_bayesian_lower_bound(self, store: SkillStore) -> None:
        """query() returns skills sorted by bayesian_lower_bound descending."""
        s1 = _make_skill(name="Weak", trigger="extract data from PDF file document")
        s1.alpha = 1.0
        s1.beta_ = 2.0  # reliability ≈ 0.33 → low

        s2 = _make_skill(name="Strong", trigger="extract data from PDF document file")
        s2.alpha = 6.0
        s2.beta_ = 1.0  # reliability ≈ 0.86 → high

        store.save(s1)
        store.save(s2)

        results = store.query("extract data from PDF", top_k=5, min_reliability=0.0)
        assert len(results) >= 2
        names = [skill.name for skill, _ in results]
        assert names.index("Strong") < names.index("Weak")

    def test_atomic_write_no_tmp_files_left(self, tmp_path: Path) -> None:
        """After save(), no .tmp files remain and skills.json is valid JSON."""
        store = SkillStore(path=tmp_path / "skills.json", embed_fn=_hash_embed)
        store.save(_make_skill())
        assert not list(tmp_path.glob("*.tmp"))
        data = json.loads((tmp_path / "skills.json").read_text())
        assert "skills" in data

    def test_update_reliability_success_increments_alpha(self, store: SkillStore) -> None:
        """update_reliability(success=True) increments alpha and persists."""
        skill = _make_skill()
        store.save(skill)
        store.update_reliability(skill.id, success=True)
        updated = store.get(skill.id)
        assert updated is not None
        assert updated.alpha == pytest.approx(2.0)
        assert updated.beta_ == pytest.approx(1.0)

    def test_update_reliability_failure_increments_beta(self, store: SkillStore) -> None:
        """update_reliability(success=False) increments beta_ and persists."""
        skill = _make_skill()
        store.save(skill)
        store.update_reliability(skill.id, success=False)
        updated = store.get(skill.id)
        assert updated is not None
        assert updated.beta_ == pytest.approx(2.0)
        assert updated.alpha == pytest.approx(1.0)

    def test_stats_returns_dict(self, store: SkillStore) -> None:
        """stats() returns a dict with total_skills."""
        store.save(_make_skill())
        s = store.stats()
        assert isinstance(s, dict)
        assert "total_skills" in s
        assert s["total_skills"] == 1


# ── SkillAdmissionControl tests ────────────────────────────────────────────────


class TestSkillAdmissionControl:
    @pytest.fixture
    def store(self, tmp_path: Path) -> SkillStore:
        return SkillStore(path=tmp_path / "skills.json", embed_fn=_hash_embed)

    @pytest.fixture
    def admission(self, store: SkillStore) -> SkillAdmissionControl:
        return SkillAdmissionControl(store=store, embed_fn=_hash_embed)

    def test_rejects_low_confidence(self, admission: SkillAdmissionControl) -> None:
        """Candidate with confidence < 0.70 is rejected with 'confidence' in reason."""
        candidate = _make_candidate(confidence=0.65)
        admitted, reason = admission.admit(candidate)
        assert not admitted
        assert "confidence" in reason.lower()

    def test_rejects_excessive_retries(self, store: SkillStore) -> None:
        """Candidate with retry_count > max_retries_for_skill is rejected."""
        admission = SkillAdmissionControl(
            store=store, max_retries_for_skill=1, embed_fn=_hash_embed
        )
        candidate = _make_candidate(confidence=0.90, retry_count=2)
        admitted, reason = admission.admit(candidate)
        assert not admitted
        assert "retr" in reason.lower()

    def test_admits_novel_high_confidence_candidate(
        self, admission: SkillAdmissionControl
    ) -> None:
        """Novel candidate with confidence >= 0.70 is admitted."""
        candidate = _make_candidate(confidence=0.85)
        admitted, reason = admission.admit(candidate)
        assert admitted, f"Expected admit but got: {reason}"

    def test_rejects_duplicate(self, tmp_path: Path) -> None:
        """Candidate whose embedding is near-identical to an existing skill is rejected."""
        # constant_embed → all vectors identical → cosine_sim = 1.0 > 0.92
        store = SkillStore(path=tmp_path / "skills_dup.json", embed_fn=_constant_embed)
        admission = SkillAdmissionControl(
            store=store, dedup_threshold=0.92, embed_fn=_constant_embed
        )

        existing = _make_skill(trigger="extract change-of-control clause")
        existing.embedding = _constant_embed("extract change-of-control clause")
        store.save(existing)

        candidate = _make_candidate(
            confidence=0.85,
            task_title="Extract change-of-control clause",
            task_description="Extract change-of-control clause from contract PDF",
        )
        admitted, reason = admission.admit(candidate)
        assert not admitted
        assert "duplicate" in reason.lower()


# ── SkillExtractor tests ───────────────────────────────────────────────────────


class TestSkillExtractor:
    @pytest.fixture
    def provider(self) -> MockProvider:
        return MockProvider()

    @pytest.fixture
    def extractor(self, provider: MockProvider) -> SkillExtractor:
        return SkillExtractor(provider=provider)

    def test_extract_skips_failed_tasks(
        self, extractor: SkillExtractor, tmp_path: Path
    ) -> None:
        """extract() does not create candidates for FAILED tasks."""
        ledger = TaskLedger(path=tmp_path / "ledger.json")
        t = Task(id="t1", title="Failed task", verifier_id="bash_exit",
                 status=TaskStatus.FAILED)
        ledger.add([t])
        candidates = extractor.extract(ledger, run_id="run_001")
        assert all(c.task_id != "t1" for c in candidates)

    def test_extract_skips_in_progress_tasks(
        self, extractor: SkillExtractor, tmp_path: Path
    ) -> None:
        """extract() does not create candidates for IN_PROGRESS tasks."""
        ledger = TaskLedger(path=tmp_path / "ledger.json")
        t = Task(id="t1", title="In-progress task", verifier_id="bash_exit",
                 status=TaskStatus.IN_PROGRESS)
        ledger.add([t])
        candidates = extractor.extract(ledger, run_id="run_001")
        assert all(c.task_id != "t1" for c in candidates)

    def test_extract_skips_high_retry_tasks(
        self, provider: MockProvider, tmp_path: Path
    ) -> None:
        """extract() skips tasks with retry_count > max_retries_for_skill."""
        extractor = SkillExtractor(provider=provider, max_retries_for_skill=1)
        ledger = TaskLedger(path=tmp_path / "ledger.json")
        t = _make_done_task("t1", retry_count=2)
        ledger.add([t])
        candidates = extractor.extract(ledger, run_id="run_001")
        assert all(c.task_id != "t1" for c in candidates)

    def test_extract_returns_candidates_for_done_tasks(
        self, extractor: SkillExtractor, tmp_path: Path
    ) -> None:
        """extract() returns SkillCandidate for qualifying DONE tasks."""
        ledger = TaskLedger(path=tmp_path / "ledger.json")
        t = _make_done_task("t1", retry_count=0)
        ledger.add([t])
        candidates = extractor.extract(ledger, run_id="run_001")
        assert any(c.task_id == "t1" for c in candidates)


# ── SkillLibrary integration tests ────────────────────────────────────────────


class TestSkillLibrary:
    @pytest.fixture
    def library(self, tmp_path: Path) -> SkillLibrary:
        return SkillLibrary(
            store_path=tmp_path / "skills.json",
            embed_fn=_hash_embed,
        )

    def test_post_run_empty_when_no_done_tasks(
        self, library: SkillLibrary, tmp_path: Path
    ) -> None:
        """post_run() returns [] when ledger has no qualifying DONE tasks."""
        ledger = TaskLedger(path=tmp_path / "ledger.json")
        ledger.add([Task(id="t1", title="Pending task", verifier_id="bash_exit")])
        skill_ids = library.post_run(ledger, run_id="run_001")
        assert skill_ids == []

    def test_record_outcome_success_increments_alpha(
        self, library: SkillLibrary
    ) -> None:
        """record_outcome(success=True) increments alpha via the store."""
        skill = _make_skill()
        library._store.save(skill)
        library.record_outcome(skill.id, success=True)
        updated = library._store.get(skill.id)
        assert updated is not None
        assert updated.alpha == pytest.approx(2.0)
        assert updated.beta_ == pytest.approx(1.0)

    def test_record_outcome_failure_increments_beta(
        self, library: SkillLibrary
    ) -> None:
        """record_outcome(success=False) increments beta_, not alpha."""
        skill = _make_skill()
        library._store.save(skill)
        library.record_outcome(skill.id, success=False)
        updated = library._store.get(skill.id)
        assert updated is not None
        assert updated.beta_ == pytest.approx(2.0)
        assert updated.alpha == pytest.approx(1.0)

    def test_export_import_roundtrip(self, library: SkillLibrary, tmp_path: Path) -> None:
        """export() + import_skills() produces the same set of skills in a fresh library."""
        s1 = _make_skill(name="S1", trigger="trigger one for export test")
        s2 = _make_skill(name="S2", trigger="trigger two for export test")
        library._store.save(s1)
        library._store.save(s2)

        export_path = tmp_path / "export.json"
        library.export(export_path)
        assert export_path.exists()

        lib2 = SkillLibrary(
            store_path=tmp_path / "skills2.json",
            embed_fn=_hash_embed,
        )
        count = lib2.import_skills(export_path)
        assert count == 2
        assert lib2._store.get(s1.id) is not None
        assert lib2._store.get(s2.id) is not None

    def test_query_returns_relevant_skills(self, library: SkillLibrary) -> None:
        """query() returns stored skills for a given description."""
        skill = _make_skill(name="PDF Extractor", trigger="extract data from PDF")
        library._store.save(skill)
        results = library.query("extract data from PDF document", top_k=3)
        assert len(results) >= 1

    def test_stats_includes_total_skills(self, library: SkillLibrary) -> None:
        """stats() returns a dict with total_skills."""
        library._store.save(_make_skill())
        s = library.stats()
        assert isinstance(s, dict)
        assert s["total_skills"] == 1

    def test_post_run_with_done_task_creates_skill(
        self, library: SkillLibrary, tmp_path: Path
    ) -> None:
        """Full integration: DONE task in ledger → post_run() → skill queryable."""
        ledger = TaskLedger(path=tmp_path / "ledger.json")
        t = _make_done_task(
            "t1",
            retry_count=0,
            title="Extract clause from PDF",
            description="Extract the change-of-control clause from contract.pdf",
        )
        ledger.add([t])

        skill_ids = library.post_run(ledger, run_id="run_001")
        assert len(skill_ids) >= 1

        # Skill must be queryable
        results = library.query("extract clause from PDF contract")
        assert len(results) >= 1
