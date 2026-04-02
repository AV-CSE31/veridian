"""
Problem 3: Hallucinated Legal Brief — Agent Fabricates Evidence
===============================================================
How Veridian prevents the Mata v. Avianca pattern.

INCIDENT: Mata v. Avianca, Inc. (S.D.N.Y. 2023)
  Roberto Mata sued Avianca for personal injury (knee struck by serving
  cart on flight). His attorneys — Peter LoDuca and Steven Schwartz of
  Levidow, Levidow & Oberman — used ChatGPT for legal research.

  ChatGPT fabricated 6 case citations that never existed — complete with
  fake judges, fake courts, fake rulings, and fake legal reasoning.

  When opposing counsel couldn't find the cases, the attorneys asked
  ChatGPT to confirm. ChatGPT assured them the cases "indeed exist" and
  "can be found in reputable legal databases such as LexisNexis and
  Westlaw." The attorneys submitted this assurance to the court.

  Judge P. Kevin Castel described the fabricated legal analysis as
  "gibberish." The attorneys were sanctioned with a $5,000 fine and
  ordered to write letters to every judge whose name appeared on the
  fake opinions.

  Key ruling: Using ChatGPT wasn't the sanctionable act. Continuing to
  defend fake citations after being questioned was.

SIGNIFICANCE:
  Leading case on AI misuse in legal pleadings. Cited in 100+ subsequent
  rulings. The pattern continues in 2026 with agents autonomously
  generating legal documents with hallucinated precedents.

ROOT CAUSE:
  LLM generates plausible case names → no verification against corpus
  → fake citations submitted as evidence → discovered by opposing counsel
  → sanctions imposed

VERIDIAN'S FIX:
  CitationGroundingVerifier — a real BaseVerifier extension that extracts
  case-law citation patterns from agent output and verifies each one
  exists in a provided corpus. Deterministic text matching on a known
  database. The LLM cannot fabricate a citation that passes a corpus
  lookup.

  In production: connect to LexisNexis, Westlaw, or CourtListener API.
  This demo uses a built-in corpus of 20 US Supreme Court cases.

USAGE:
    pip install veridian-ai
    python solution.py
    python solution.py "In Martinez v. GlobalCorp, 892 F.3d 1156, the court held..."
"""

from __future__ import annotations

import re
import sys
import time
from typing import ClassVar

from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult


# ── Real case law corpus ────────────────────────────────────────────────────
# In production, this would query LexisNexis/Westlaw/CourtListener.
# For this demo: 20 landmark US Supreme Court cases.

CASE_LAW_CORPUS: set[str] = {
    "brown v. board of education",
    "roe v. wade",
    "miranda v. arizona",
    "marbury v. madison",
    "terry v. ohio",
    "mapp v. ohio",
    "gideon v. wainwright",
    "tinker v. des moines",
    "new york times v. sullivan",
    "engel v. vitale",
    "griswold v. connecticut",
    "loving v. virginia",
    "texas v. johnson",
    "plessy v. ferguson",
    "dred scott v. sandford",
    "united states v. nixon",
    "citizens united v. fec",
    "obergefell v. hodges",
    "dobbs v. jackson",
    "burwell v. hobby lobby",
}


class CitationGroundingVerifier(BaseVerifier):
    """Verifies case citations exist in a known legal corpus.

    This is a real BaseVerifier extension — the same pattern developers use
    to build domain-specific verifiers for legal, healthcare, and financial
    pipelines. Register via pyproject.toml entry-points for autodiscovery.

    The verification is deterministic: regex extracts "X v. Y" patterns,
    then each is checked against the corpus via normalized string matching.
    No LLM call. No prompt. A text lookup.
    """

    id: ClassVar[str] = "citation_grounding"
    description: ClassVar[str] = "Verifies legal case citations against a known corpus"

    def __init__(self, corpus: set[str] | None = None) -> None:
        self._corpus = corpus or CASE_LAW_CORPUS

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        raw = getattr(result, "raw_output", "") or ""

        # Extract "X v. Y" patterns — matches "Smith v. Jones" or "Smith v. Jones, 123 F.3d 456"
        citations = re.findall(
            r"([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*\s+v\.\s+[A-Z][a-zA-Z]+(?:\s+[a-zA-Z]+)*)",
            raw,
        )

        if not citations:
            return VerificationResult(passed=True, evidence={"citations_found": 0})

        verified: list[str] = []
        fabricated: list[str] = []

        # Common words that precede case names but aren't part of the citation
        _STRIP_PREFIXES = {"in", "under", "see", "per", "from", "as", "cf", "but", "and", "also", "while"}

        for cite in citations:
            # Normalize: "Under Miranda v. Arizona" → "miranda v. arizona"
            normalized = cite.strip().lower()
            # Strip leading prepositions/conjunctions that aren't part of the case name
            words = normalized.split()
            while words and words[0] in _STRIP_PREFIXES:
                words = words[1:]
            normalized = " ".join(words)
            # Check against corpus
            parts = normalized.split(",")[0].strip()
            if parts in self._corpus or normalized in self._corpus:
                verified.append(cite.strip())
            else:
                fabricated.append(cite.strip())

        if fabricated:
            return VerificationResult(
                passed=False,
                error=(
                    f"Fabricated citation(s) not found in legal corpus: "
                    f"{'; '.join(fabricated[:3])}"
                    + (f" (+{len(fabricated) - 3} more)" if len(fabricated) > 3 else "")
                ),
                evidence={
                    "fabricated": fabricated,
                    "verified": verified,
                    "total_citations": len(citations),
                },
            )

        return VerificationResult(
            passed=True,
            evidence={"verified": verified, "total_citations": len(citations)},
        )


_verifier = CitationGroundingVerifier()


def check_brief(text: str, label: str = "brief") -> tuple[bool, str, dict[str, object]]:
    """Run a legal brief through citation grounding verification."""
    task = Task(id=label, title="Legal brief verification", verifier_id="citation_grounding")
    result = TaskResult(raw_output=text)
    v = _verifier.verify(task, result)
    return v.passed, v.error or "", v.evidence


# ── Scenarios ────────────────────────────────────────────────────────────────
# Derived from the actual Mata v. Avianca case and common hallucination patterns

SCENARIOS: list[tuple[str, str, str]] = [
    # Real citations that exist in corpus
    (
        "real_brown_v_board",
        "As the Court held in Brown v. Board of Education, racial segregation "
        "in public schools violates the Equal Protection Clause of the 14th Amendment.",
        "Real citation — Brown v. Board of Education (1954)",
    ),
    (
        "real_miranda",
        "Under Miranda v. Arizona, statements made during custodial interrogation "
        "are inadmissible unless the suspect was informed of their rights.",
        "Real citation — Miranda v. Arizona (1966)",
    ),

    # Fabricated citations — exactly the Mata v. Avianca pattern
    (
        "mata_pattern_fabricated",
        "In Martinez v. GlobalCorp, 892 F.3d 1156 (2d Cir. 2019), the court "
        "held that airlines bear strict liability for injuries caused by "
        "service equipment during international flights.",
        "FABRICATED — this is exactly the pattern from Mata v. Avianca",
    ),
    (
        "plausible_fabrication",
        "The principle established in Henderson v. Pacific Air Transport, "
        "445 F. Supp. 2d 891 (N.D. Cal. 2018) confirms that carriers owe "
        "a heightened duty of care to elderly passengers.",
        "FABRICATED — plausible-sounding but the case doesn't exist",
    ),
    (
        "mixed_real_and_fake",
        "While Miranda v. Arizona established the requirement for rights "
        "advisement, the more recent ruling in Chen v. Skybridge Airways, "
        "901 F.3d 234 (9th Cir. 2021) extended this principle to automated "
        "customer service interactions.",
        "MIXED — Miranda is real, Chen v. Skybridge is fabricated",
    ),

    # No citations at all — should pass
    (
        "no_citations",
        "The contract between the parties specifies a 30-day notice period "
        "for termination. This clause is enforceable under standard contract law.",
        "No citations — pure legal analysis, should pass",
    ),

    # Multiple fabricated — the worst case
    (
        "multiple_fabricated",
        "See Thompson v. Meridian Corp, 567 F.3d 890 (7th Cir. 2020); "
        "also Park v. United Digital, 712 F. Supp. 3d 445 (E.D.N.Y. 2022); "
        "cf. Ramirez v. AutoTech Solutions, 834 F.3d 112 (3d Cir. 2023).",
        "THREE fabricated citations in one paragraph",
    ),
]


def run_demo() -> None:
    start = time.monotonic()

    print("\n" + "=" * 75)
    print("  VERIDIAN — Citation Grounding Verification")
    print("  Preventing the Mata v. Avianca hallucinated citation pattern")
    print("  Verifier: CitationGroundingVerifier (BaseVerifier extension)")
    print("  Corpus: 20 US Supreme Court landmark cases")
    print("=" * 75)

    passed_count = blocked_count = 0

    for label, text, desc in SCENARIOS:
        ok, error, evidence = check_brief(text, label)
        status = "PASS   " if ok else "BLOCKED"
        print(f"\n  [{status}] {label}")
        print(f"            {desc}")

        if not ok:
            print(f"            Error: {error[:70]}")
            fab = evidence.get("fabricated", [])
            if fab:
                for f in fab[:3]:
                    print(f"            Fabricated: {f}")
            blocked_count += 1
        else:
            ver = evidence.get("verified", [])
            if ver:
                print(f"            Verified: {', '.join(str(v) for v in ver)}")
            passed_count += 1

    elapsed = int((time.monotonic() - start) * 1000)

    print(f"\n  {'=' * 71}")
    print(f"  Passed: {passed_count}  |  Blocked: {blocked_count}  |  {elapsed}ms")
    print(f"  Corpus: {len(CASE_LAW_CORPUS)} landmark cases")
    print(f"  In production: connect to LexisNexis, Westlaw, or CourtListener API")
    print(f"  The $5,000 sanction in Mata v. Avianca would not have happened.")
    print(f"  {'=' * 71}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
        ok, err, ev = check_brief(text, "cli")
        print(f"\n  Input:  {text[:80]}")
        print(f"  Result: {'PASS' if ok else 'BLOCKED'}")
        if err:
            print(f"  Error:  {err}")
        if ev.get("fabricated"):
            for f in ev["fabricated"]:
                print(f"  Fabricated: {f}")
        if ev.get("verified"):
            for v in ev["verified"]:
                print(f"  Verified: {v}")
    else:
        run_demo()
