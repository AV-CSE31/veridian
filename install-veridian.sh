#!/usr/bin/env bash
# ================================================================
# install-veridian.sh
# Complete Claude Code Session System for the Veridian repo.
# Run this once from the veridian-ai/veridian project root.
#
# Usage:
#   chmod +x install-veridian.sh
#   ./install-veridian.sh
#
# What it does:
#   - Creates .claude/ structure (commands, skills, scripts)
#   - Writes CLAUDE.md, AGENTS.md, PROJECT_INTEL.md
#   - Writes context-essentials.md (post-compaction injection)
#   - Writes settings.json (hooks: compact, write, stop, session-start)
#   - Writes 7 generic + 7 Veridian-specific slash commands
#   - Writes 3 Veridian skills (verifiers, providers, bench)
#   - Writes bench.py (verifier benchmark runner)
#   - Writes evals/veridian-evals.json (5 eval cases)
#   - Touches SESSION_HANDOFF.md, ARCHITECTURE.md, CODEBASE_HEALTH.md
#   - Runs verification and prints next steps
#
# Safe to re-run: existing files are preserved with a warning.
# ================================================================

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────
B='\033[0;34m'; G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; N='\033[0m'
ok()   { echo -e "${G}✅${N}  $*"; }
info() { echo -e "${B}→${N}  $*"; }
warn() { echo -e "${Y}⚠️ ${N}  $*"; }
fail() { echo -e "${R}❌${N}  $*"; exit 1; }

# ── Guard ────────────────────────────────────────────────────
[ -f "pyproject.toml" ] || fail "Run from the veridian project root (pyproject.toml not found)"
grep -q "veridian" pyproject.toml 2>/dev/null || \
  warn "pyproject.toml found but 'veridian' not in it — continuing anyway"

echo ""
echo -e "${B}╔══════════════════════════════════════════════════════╗${N}"
echo -e "${B}║  Veridian — Claude Code Session System Installer    ║${N}"
echo -e "${B}╚══════════════════════════════════════════════════════╝${N}"
echo ""

# ── Dirs ─────────────────────────────────────────────────────
info "Creating directories..."
mkdir -p \
  .claude/commands \
  .claude/skills \
  .claude/scripts \
  .claude/session-log \
  docs/agent-guides \
  evals \
  benchmarks

# ── Helper: write file only if missing ───────────────────────
w() {   # w <path> <content via heredoc from stdin>
  local path="$1"
  if [ -f "$path" ]; then
    warn "Kept existing: $path"
    cat > /dev/null   # drain stdin
  else
    cat > "$path"
    ok "Created: $path"
  fi
}

# ── Touch files (never clobber) ──────────────────────────────
for f in SESSION_HANDOFF.md ARCHITECTURE.md CODEBASE_HEALTH.md CHANGELOG.md \
          benchmarks/VERIDIAN_BENCH.json; do
  [ -f "$f" ] && warn "Kept existing: $f" || { touch "$f"; ok "Touched: $f"; }
done

# ════════════════════════════════════════════════════════════
# CLAUDE.md
# ════════════════════════════════════════════════════════════
w CLAUDE.md << 'CLAUDE'
# Veridian — Python Framework for Reliable Long-Running AI Agents

## WHAT
Open-source MIT. 10 deterministic Python verifiers. Not prompt-based.
The LLM cannot self-certify task completion — that is the whole point.
PyPI: veridian | Python ≥3.11 | Result: <veridian:result confidence="[0-1]">

## HOW
- Install:      pip install -e ".[dev]"
- Test:         pytest tests/ -v                    # must show ≥31 passing
- Type:         mypy src/ --strict                  # must show 0 errors
- Lint:         ruff check src/                     # must show 0 violations
- Bench:        python .claude/scripts/bench.py     # verifier performance
- Build:        python -m build
- Publish:      twine upload dist/* (after release-check passes)

## WHY — invariants (WHAT — FILE — BREAKS: consequence)
- Any FORBIDDEN — src/veridian/ — BREAKS: mypy --strict fails, type contracts collapse
- VERIDIAN_MODEL from env — src/providers/litellm_provider.py — BREAKS: wrong model runs silently
- TaskLedger records BEFORE action — src/ledger/task_ledger.py — BREAKS: crash loses task forever
- TrustedExecutor wraps ALL tools — src/executor/trusted_executor.py — BREAKS: ACI defense removed
- OutputSanitizer on EVERY output — src/sanitizer/output_sanitizer.py — BREAKS: PII leaks
- LiteLLMProvider owns retry+CB+fallback — src/providers/ — BREAKS: duplicate backoff, races
- <veridian:result> marker canonical — src/veridian/__init__.py — BREAKS: all consumers break
- match over if/elif for dispatch — src/veridian/ — BREAKS: exhaustiveness checking lost
- Test count must stay ≥31 — tests/ — BREAKS: regression blind spots in verifier chain

## NEVER (action — RISK: consequence)
- Never use Any type — RISK: type safety collapses chain-wide
- Never hardcode model name — RISK: prod runs against wrong model silently
- Never bypass TrustedExecutor — RISK: ACI defense completely removed
- Never skip OutputSanitizer — RISK: agent leaks PII through unsanitized output
- Never add GPL/AGPL dependency — RISK: MIT license contaminated, PyPI illegal
- Never reduce tests below 31 — RISK: verifier regression goes undetected

## LOAD (read on demand, not every session)
- Verifiers: docs/agent-guides/verifiers.md
- TaskLedger: docs/agent-guides/task-ledger.md
- ACI defense: docs/agent-guides/aci-defense.md
- Hooks: docs/agent-guides/hooks.md
- LiteLLM: docs/agent-guides/litellm-provider.md
- Experiments: docs/agent-guides/experiments.md
- Bench log: benchmarks/VERIDIAN_BENCH.json
CLAUDE

# ════════════════════════════════════════════════════════════
# AGENTS.md
# ════════════════════════════════════════════════════════════
w AGENTS.md << 'AGENTS'
# Agent Navigation — Veridian

## Session lifecycle
| Moment | Command | Cost |
|--------|---------|------|
| Every session start | /project:start-session | ~1K tokens |
| Daily triage | /project:quick-intel | ~3K tokens |
| First session of week | /project:intel-briefing | ~8K tokens |
| Every session end | /project:end-session | writes files |
| Before any PR merge | /veridian:pr-review | read-only |
| Before any PyPI publish | /veridian:release-check | read-only |
| After modifying a verifier | /veridian:verifier-audit | read-only |
| Weekly | /veridian:bench-verifiers | read-only |
| Start of intel session | /veridian:aria-ingest | read-only |

## Context budget
Tier 1 always-on (≤2K): CLAUDE.md + AGENTS.md + SESSION_HANDOFF.md
Tier 2 on-demand (≤20K): ARCHITECTURE.md, CODEBASE_HEALTH.md, docs/agent-guides/
Tier 3 reference: CHANGELOG.md, README.md — never auto-load

## Compaction rule
Compact at 50%, not 60-80%. Manual with directive:
  /compact Keep only current task state. SESSION_HANDOFF.md has everything else.

## Subagent rule
Before reading ≥3 files to understand something — always spawn a subagent:
  "Use subagents to investigate [X]. Report findings. Do NOT modify files."
AGENTS

# ════════════════════════════════════════════════════════════
# PROJECT_INTEL.md
# ════════════════════════════════════════════════════════════
w PROJECT_INTEL.md << 'INTEL'
# Project Intelligence Configuration — Veridian

## Identity
- Name: Veridian | Type: Open-source Python framework (MIT)
- Domain: Deterministic AI agent verification and task integrity
- Stack: Python 3.11+, LiteLLM, pytest, mypy, ruff
- Stage: v0.1.0 shipped → v0.2.0 in development
- PyPI: veridian · GitHub: veridian-ai/veridian
- Tagline (exact): "The verification contract between an agent and the world."

## Core differentiator — never dilute
"They use prompt-based verification. We use deterministic Python."
Our verifiers can be unit-tested, audited, formally proven. Theirs cannot.
If a competitor adds deterministic Python verifiers: update this section immediately.

## Competitors — scan every intel session
- LangGraph: https://langchain-ai.github.io/langgraph/
  Monitor: v0.4+ verification additions, enterprise reliability changelog
- AutoGen: https://microsoft.github.io/autogen/
  Monitor: task completion verification, agent reliability patterns
- CrewAI: https://crewai.com
  Monitor: enterprise reliability tier, verification claims
- Pydantic AI: https://ai.pydantic.dev
  Monitor: output validation — closest architectural overlap
- smolagents: https://huggingface.co/docs/smolagents
  Monitor: agent reliability, verification hooks
- Guardrails AI: https://guardrailsai.com (scan monthly)
- NVIDIA NeMo Guardrails (scan monthly)

## Custom search queries
- "LangGraph verification hooks deterministic Python 2026"
- "AI agent task completion verification Python framework 2026"
- "veridian-ai GitHub stars issues" (community traction)
- "LLM self-certification problem solution 2026"
- "agent verifier deterministic site:github.com"

## arXiv categories
- cs.MA: multi-agent orchestration reliability
- cs.AI: agent correctness, self-consistency
- cs.SE: software verification applied to AI systems

## Innovation sources
- https://github.com/langchain-ai/langgraph/releases
- https://microsoft.github.io/autogen/blog
- Papers With Code: "agent verification" + "task completion"

## Quality targets — each is a release gate
- Tests: ≥31 passing, 0 failures, 0 skips
- Type: mypy --strict → 0 errors
- Lint: ruff → 0 violations
- License: pip-licenses --fail-on="GPL;AGPL" → 0 hits
- Docs: all public APIs have docstrings
- Build: python -m build → wheel + sdist clean
- Bench: all 10 verifiers → pass_rate ≥ 0.95

## Priority rules
### P0 — stop everything
- Test count < 31 | Any type in public API | TrustedExecutor bypassed
- TaskLedger crash-safety violated | OutputSanitizer skipped
- GPL/AGPL dep introduced | <veridian:result> marker changed
- VERIDIAN_MODEL hardcoded

### P1 — do this session if P0 clear
- New verifier not in chain | Circuit breaker not through LiteLLMProvider
- Public API without docstring | Python < 3.11 break

### P3 — don't start until P0+P1 clear
- Internal refactors with no public API change
- Perf optimisations without benchmark backing

## ARIA integration
ARIA (Ashish's daily research pipeline) delivers: LLMs, Agentic AI, Open Source.
Before Phase 5 innovation radar: run /veridian:aria-ingest.
ARIA's "Agentic AI" + "Open Source" streams overlap directly with Veridian's sources.
INTEL

# ════════════════════════════════════════════════════════════
# settings.json
# ════════════════════════════════════════════════════════════
w .claude/settings.json << 'SETTINGS'
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "compact",
        "hooks": [{"type": "command", "command": "cat .claude/context-essentials.md"}]
      },
      {
        "matcher": "Write|Edit",
        "hooks": [{"type": "command", "command": "echo '⚡ File written — update SESSION_HANDOFF.md before closing'"}]
      }
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "echo '⚠️  Session ending — run /project:end-session if work happened'"}]}
    ],
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "[ -f SESSION_HANDOFF.md ] && echo '📋 Handoff exists — /project:start-session first' || echo '🆕 No handoff — fresh session'"}]}
    ]
  }
}
SETTINGS

python3 -m json.tool .claude/settings.json > /dev/null && ok "settings.json valid" || fail "settings.json invalid JSON"

# ════════════════════════════════════════════════════════════
# context-essentials.md
# ════════════════════════════════════════════════════════════
w .claude/context-essentials.md << 'ESSENTIALS'
# Context Essentials — Veridian Post-Compaction Injection
<!-- Auto-injected after /compact. Hard limit: 50 lines. -->
<!-- Rule: "Would a bug occur without this?" If no → don't include. -->

## Manifesto
"Verification is Python, never prompts. The LLM cannot self-certify task completion."
Result block: <veridian:result confidence="[0-1]">

## Invariants (each causes a bug if violated)
- Any: FORBIDDEN — src/veridian/ — BREAKS: mypy --strict fails
- VERIDIAN_MODEL: env only — src/providers/litellm_provider.py — BREAKS: wrong model silently
- TaskLedger: BEFORE action — src/ledger/task_ledger.py — BREAKS: crash loses task
- TrustedExecutor: ALL tools — src/executor/trusted_executor.py — BREAKS: ACI gap
- OutputSanitizer: EVERY output — src/sanitizer/ — BREAKS: PII leakage
- Result marker: <veridian:result — NEVER change — BREAKS: all consumers
- Tests: ≥31 — pytest tests/ -v

## Commands
- Tests:  pytest tests/ -v  (must show ≥31 passing)
- Type:   mypy src/ --strict  (must show 0 errors)
- Lint:   ruff check src/
- Build:  python -m build

## Bench status (update after each bench run)
- Last run: [date] | Lowest: [verifier] at [pass_rate]

## Current task (update each session)
- Working on: [task]
- Do next: [file:line]
- Do NOT touch: [broken files]
ESSENTIALS

# ════════════════════════════════════════════════════════════
# GENERIC COMMANDS (7)
# ════════════════════════════════════════════════════════════
info "Writing generic session commands..."

w .claude/commands/start-session.md << 'CMD'
---
description: >
  Orient a new session. USE AS FIRST COMMAND every session before reading
  source files. Trigger on: "start session", "where were we", "resume",
  "what's next", "continue", "what should I work on".
mode: read-only
---

1. Read SESSION_HANDOFF.md (REQUIRED — before any source files)
   WHY: source files first = 30-50% context burned on re-orientation.
2. Read CLAUDE.md (REQUIRED)
3. Read CODEBASE_HEALTH.md summary row only

Print Project Pulse:
```
┌──────────────────────────────────────────┐
│ STATUS: [from handoff]                   │
│ Last: [date] — [one sentence]            │
│ Tests: [X/Y] Type: [ok/N errors]         │
│ Branch: [name]  Blocker: [or "None"]     │
└──────────────────────────────────────────┘
```

List top 3 next actions from "Next session: start here".
Ask: "Which of these?" — wait for answer before acting.

ANTI-PATTERN: Never read source files first. Never start working before confirmation.
CMD

w .claude/commands/end-session.md << 'CMD'
---
description: >
  Generate end-of-session handoff. USE AS LAST COMMAND every session.
  Trigger on: "wrap up", "end session", "done for today", "finishing up",
  "generate handoff", "commit and close".
mode: writes-files
---

STEP 1 — GIT HYGIENE (run first, before writing anything)
git branch --show-current && git status --short && git diff --stat && git log --oneline -3
If uncommitted: commit with descriptive message now.

STEP 2 — WRITE SESSION_HANDOFF.md
```
# Session Handoff
<!-- Updated: [ISO] Branch: [name] HEAD: [SHA] Context: [X%] -->
## STATUS: [IN_PROGRESS|BLOCKED|COMPLETE|NEEDS_REVIEW]
## Accomplished
- [x] [thing done] — [file:line]
## Current state
- Tests: [N/M] — pytest tests/ -v | Type: [0 errors] | Lint: [0 violations]
- Runs right now: [exact command]
- Git: [branch], [N] ahead of main, [un]committed
## In progress
- [ ] [task]: [done] → [remains] [file:line]
## Blockers
- [or "None"]
## Decisions made
- Decision: [what] | Reason: [why] | Rejected: [alt and why]
## Gotchas discovered
- [file]: [what, why it matters]
## Subagent delegation state
- [type] — Task: [what] | Result: [summary] | Files: [list]
## Next session: start here
1. [action — file:line]
2. [action]
3. [action]
## Files modified
- [path] — [what changed]
```

STEP 3 — HEALTH CHECK (run real commands, never estimate)
pytest tests/ -v --tb=short 2>&1 | tail -20
mypy src/ --strict 2>&1 | tail -10
ruff check src/ 2>&1 | tail -10
Update CODEBASE_HEALTH.md summary with real numbers.

STEP 4 — README SYNC
Test install command. Test quickstart example. Fix lies. Mark unbuilt as 🚧.

STEP 5 — RESUMPTION TEST (required before closing)
Can a fresh session answer all 10 from CLAUDE.md + SESSION_HANDOFF.md alone?
1. ≥31 tests passing? (verified, not assumed)
2. mypy --strict 0 errors? (verified)
3. All 10 verifiers in chain? (from last /veridian:verifier-audit)
4. Every output through OutputSanitizer?
5. VERIDIAN_MODEL only model selector?
6. Lowest verifier pass_rate from VERIDIAN_BENCH.json?
7. Experiment results pending?
8. Next concrete action with file:line?
9. ARIA findings this session?
10. Skill self-eval score?
If any fails: add missing info before committing.

STEP 6 — COMMIT HANDOFF
git add SESSION_HANDOFF.md CODEBASE_HEALTH.md CHANGELOG.md ARCHITECTURE.md \
        .claude/context-essentials.md 2>/dev/null || true
git commit -m "session handoff: [one-line summary]" 2>/dev/null || true

Print: "Handoff complete ✓ — /project:start-session to resume"
CMD

w .claude/commands/intel-briefing.md << 'CMD'
---
description: >
  Full 6-phase intelligence briefing. ~8K tokens, ~12 min.
  USE AT: first session of week, after 3+ days away, before planning.
  Trigger on: "full briefing", "weekly brief", "competitive landscape",
  "intel briefing", "strategic briefing". Do NOT run when context > 20%.
mode: read-only
---

Read PROJECT_INTEL.md for competitors, search queries, arXiv categories, ARIA note.

P1 ORIENT: SESSION_HANDOFF.md + CLAUDE.md + CODEBASE_HEALTH.md summary
P2 COMPETITOR SCAN: web search each competitor + "update/changelog" (last 30 days)
   Search for new entrants. Search for stack dep updates.
P3 BUG TRIAGE: run pytest + mypy + ruff. Build P0/P1/P2/P3 matrix with file:line.
P4 OPTIMIZATION: scan N+1, unbounded queries, missing async, dead code.
P5 INNOVATION RADAR: arXiv search + blog sources from PROJECT_INTEL.md.
   For each finding: concrete first step, not just "this exists".
P6 SESSION PLAN: 3 options A/B/C. Each: what + why + effort + risk + files.
   Make a recommendation. Wait for choice before working.
CMD

w .claude/commands/quick-intel.md << 'CMD'
---
description: >
  3-phase triage briefing. ~3K tokens, ~5 min. Skip competitor + innovation.
  USE AT: daily start, resuming after <3 days away.
  Trigger on: "quick brief", "daily brief", "triage", "priorities today".
mode: read-only
---

P1: Read SESSION_HANDOFF.md + CLAUDE.md + CODEBASE_HEALTH.md summary.
P3: Run pytest + mypy + ruff — real numbers, never estimate.
    Build P0/P1/P2 matrix with file:line references.
P4: Top 3 optimization opportunities ranked by ROI.
Present top 3 actions with effort. Wait for confirmation before working.
CMD

w .claude/commands/health-check.md << 'CMD'
---
description: >
  Full codebase health audit. USE: weekly, before releases, after refactors.
  Trigger on: "health check", "audit codebase", "code quality".
mode: read-only
---

1. Dead code: vulture src/ --min-confidence 80
2. Complexity: radon cc src/ -a -nc (flag CC > 10)
3. Deps: pip list --outdated && pip-audit
4. Duplication: pylint --disable=all --enable=duplicate-code src/
5. Markers: grep -rn "TODO\|FIXME\|HACK\|XXX" src/
Update CODEBASE_HEALTH.md with findings and trend arrows (↑↓→).
Print: "[N] dead | [M] complex | [K] dep issues | [J] markers"
CMD

w .claude/commands/competitor-scan.md << 'CMD'
---
description: >
  Competitor scan only. No codebase analysis. ~4K tokens.
  Trigger on: "competitor scan", "what's competition doing", "competitive intel".
mode: read-only
---
Read PROJECT_INTEL.md for competitor list and search queries.
Search each direct competitor + "update/changelog" (30 days). Use real web search.
Search for new entrants and market signals.
Generate Competitor Intel table + Feature Gap Analysis.
Flag any finding that should change current priorities.
CMD

w .claude/commands/innovation-radar.md << 'CMD'
---
description: >
  Innovation and research scan only. ~5K tokens.
  Trigger on: "innovation radar", "research scan", "what's new in agent verification".
mode: read-only
---
Read PROJECT_INTEL.md arXiv categories and blog sources.
Search arXiv last 30 days + new tools in stack.
For each finding: applicability + concrete first step (not just "this exists").
Identify single highest-impact innovation. Propose first action: file to create or experiment.
CMD

# ════════════════════════════════════════════════════════════
# VERIDIAN-SPECIFIC COMMANDS (7)
# ════════════════════════════════════════════════════════════
info "Writing Veridian-specific commands..."

w .claude/commands/veridian-verifier-audit.md << 'CMD'
---
description: >
  Audit completeness and connectivity of all 10 Veridian verifiers.
  USE WHEN: after adding a verifier, before any release, when a verifier
  test fails unexpectedly. Trigger on: "verifier audit", "check verifiers",
  "verifier chain health", "are all verifiers connected", "check the chain".
mode: read-only
idempotent: true
---

A verifier that exists but is not connected is as dangerous as a missing one.
It creates the illusion of safety.

STEP 1 — IMPORTABILITY
python3 -c "
from veridian.verifiers import (
    SemanticGroundingVerifier, SelfConsistencyVerifier,
    CrossRunConsistencyHook, TaskQualityGate, ConfidenceScore,
)
print('Core 5: OK')
import importlib, pathlib
found = 0
for f in sorted(pathlib.Path('src/veridian/verifiers').glob('*.py')):
    if f.name.startswith('_'): continue
    try:
        importlib.import_module(f'veridian.verifiers.{f.stem}')
        print(f'  OK {f.stem}')
        found += 1
    except ImportError as e:
        print(f'  FAIL {f.stem}: {e}')
print(f'Total importable: {found} (target: 10)')
"

STEP 2 — CHAIN REGISTRATION
grep -rn "register_verifier\|add_verifier\|VerifierChain\|verifier_chain" \
  src/ --include="*.py" | grep -v "test_\|#" | sort
echo "Every verifier must appear above — absence = not connected"

STEP 3 — TASKQUALITYGATE AGGREGATION
grep -rn "TaskQualityGate" src/ --include="*.py" | grep -v "test_\|#"
echo "Verify: aggregates all N verifiers before emitting result block"

STEP 4 — CONFIDENCESCORE ORDERING (must run BEFORE result block)
grep -n "ConfidenceScore\|veridian:result" src/ -r --include="*.py" | grep -v "test_\|#" | sort -t: -k2 -n
echo "ConfidenceScore line number must be LOWER than veridian:result line"

STEP 5 — TEST COVERAGE (≥3 per verifier: pass, fail, edge)
pytest tests/verifiers/ -v --tb=short 2>&1 | tail -40
python3 -c "
import subprocess, re
from collections import Counter
r = subprocess.run(['pytest','tests/verifiers/','-v'], capture_output=True, text=True)
counts = Counter(re.findall(r'tests/verifiers/test_(\w+)\.py', r.stdout))
for v, n in sorted(counts.items()):
    print(f'  {\"OK\" if n >= 3 else \"WARN\"} {v}: {n} tests (need ≥3)')
"

Output: PASS (all 10 connected + ≥3 tests each) or report with gaps and remediation.
CMD

w .claude/commands/veridian-release-check.md << 'CMD'
---
description: >
  8-gate pre-release validation. Zero tolerance — must pass before PyPI publish.
  USE BEFORE every release, RC, or major merge to main.
  Trigger on: "release check", "ready to release", "pre-release",
  "publish check", "is this releasable", "prepare release".
mode: read-only
idempotent: true
---

Run all 8 gates. Print PASS or FAIL with evidence per gate.
Do not publish if ANY gate fails.

GATE 1 — TESTS (≥31 passing, 0 failures)
python3 -c "
import subprocess, re
r = subprocess.run(['pytest','tests/','-v','--tb=short'], capture_output=True, text=True)
passed = int((re.search(r'(\d+) passed', r.stdout) or type('',(),{'group':lambda*_:'0'})()).group(1))
failed = int((re.search(r'(\d+) failed', r.stdout) or type('',(),{'group':lambda*_:'0'})()).group(1))
print(f'Tests: {passed} passed, {failed} failed')
print('  PASS' if passed >= 31 and failed == 0 else f'  FAIL: {passed}/31 passing, {failed} failing')
"

GATE 2 — TYPE (0 mypy errors)
python3 -c "
import subprocess
r = subprocess.run(['mypy','src/','--strict'], capture_output=True, text=True)
errors = r.stdout.count('error:')
print(f'Type errors: {errors}')
print('  PASS' if errors == 0 else f'  FAIL: {errors} type errors')
"

GATE 3 — LINT (0 violations)
python3 -c "
import subprocess
r = subprocess.run(['ruff','check','src/'], capture_output=True, text=True)
lines = [l for l in r.stdout.splitlines() if l.strip() and not l.startswith('Found')]
print(f'Lint violations: {len(lines)}')
print('  PASS' if not lines else f'  FAIL: {len(lines)} violations')
"

GATE 4 — LICENSE (0 GPL/AGPL)
pip-licenses --fail-on="GPL;AGPL" 2>&1 | tail -3 \
  && echo "  PASS: no GPL/AGPL" \
  || echo "  FAIL: GPL/AGPL found — MIT at risk"

GATE 5 — DOCS COVERAGE (all public APIs documented)
python3 << 'EOF'
import ast, pathlib
undoc = []
for f in pathlib.Path('src/veridian').rglob('*.py'):
    try: tree = ast.parse(f.read_text())
    except SyntaxError: continue
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith('_') and not ast.get_docstring(node):
                undoc.append(f'{f}:{node.lineno} {node.name}')
print(f'Undocumented public APIs: {len(undoc)}')
[print(f'  {u}') for u in undoc[:10]]
print('  PASS' if not undoc else f'  WARN: {len(undoc)} undocumented')
EOF

GATE 6 — BUILD (wheel + sdist)
rm -rf dist/ && python -m build 2>&1 | tail -5
python3 -c "
import pathlib
w = list(pathlib.Path('dist').glob('*.whl'))
s = list(pathlib.Path('dist').glob('*.tar.gz'))
print(f'Artifacts: {len(w)} wheel, {len(s)} sdist')
print('  PASS' if w and s else '  FAIL: build incomplete')
"

GATE 7 — VERSION CONSISTENCY (__init__.py == pyproject.toml)
python3 << 'EOF'
import pathlib, re, tomllib
init = pathlib.Path('src/veridian/__init__.py').read_text()
toml = tomllib.loads(pathlib.Path('pyproject.toml').read_bytes())
iv = (re.search(r"__version__\s*=\s*[\"']([\d.a-z]+)", init) or type('',(),{'group':lambda*_:'NOT FOUND'})()).group(1)
tv = toml.get('project', {}).get('version', 'NOT FOUND')
print(f'__init__.py: {iv} | pyproject.toml: {tv}')
print('  PASS' if iv == tv else f'  FAIL: mismatch {iv} vs {tv}')
EOF

GATE 8 — VERIFIER BENCH (≥0.95 pass rate)
python3 .claude/scripts/bench.py --eval-set evals/veridian-evals.json --gate 0.95 \
  && echo "  PASS: all verifiers ≥0.95" \
  || echo "  FAIL: verifier below threshold — run /veridian:bench-verifiers"

echo ""
echo "Release check complete. Fix all FAILs before: twine upload dist/*"
CMD

w .claude/commands/veridian-pr-review.md << 'CMD'
---
description: >
  Veridian PR review — verifier contracts, type safety, ACI defense, license integrity.
  USE BEFORE every merge to main. Trigger on: "review PR", "review changes",
  "check before merge", "veridian review", "is this ready to merge".
mode: read-only
idempotent: true
---

Diff: git diff main..HEAD

CLAIM EXTRACTION (before reporting violations):
Extract implicit claims from the diff:
- Factual: "adds a new verifier" → verify it's in chain
- Process: "fixes retry logic" → verify it goes through LiteLLMProvider
- Quality: "improves type coverage" → verify mypy shows no new errors

CRITICAL — block merge, zero tolerance:
- Any type anywhere in src/
- Model name string literal not via env var
- Tool call not through TrustedExecutor
- State mutation before TaskLedger records intent
- Output path not through OutputSanitizer
- LLM call not through LiteLLMProvider
- New GPL or AGPL in pyproject.toml
- Change to string "<veridian:result"
- pytest count would drop below 31

WARNING — fix before merge:
- Verifier defined but not registered in chain
- ConfidenceScore not run before result block
- New public API without docstring
- match replaced with if/elif for dispatch
- Python < 3.11 syntax used

INSTRUCTION-FOLLOWING SCORE (1-10):
"How well does this diff comply with CLAUDE.md invariants?"
9-10: all respected | 7-8: warnings only | <7: critical violations

Output:
## Critical (block merge)
[list with file:line, or "None"]
## Warning (fix before merge)
[list or "None"]
## Instruction-following score: [N]/10
[one sentence rationale]
CMD

w .claude/commands/veridian-experiment.md << 'CMD'
---
description: >
  Run a 4-phase Veridian research experiment: plan → implement → run → grade.
  USE WHEN: testing a verifier hypothesis, running arxiv-loop experiments,
  benchmarking verifier accuracy. Trigger on: "run experiment", "test hypothesis",
  "arxiv-loop", "benchmark verifier", "validate this technique".
mode: writes-files
---

4-phase: PLAN (precise hypothesis) → IMPLEMENT (scaffold + evals) →
         RUN (baseline first, always) → EVALUATE + GRADE (claim extraction)

PHASE 1 — PLAN (must be precise, not vague)
State hypothesis:
  "I believe [verifier X] will [measurable behaviour Y] when [condition Z],
   improving [metric M] from [baseline estimate] to [target]."
If vague (no measurable outcome): stop and ask for clarification.
Commit to ONE metric: completion rate | consistency score | false-positive rate | latency

PHASE 2 — IMPLEMENT
EXP="experiments/$(date +%Y%m%d)-[slug]"
mkdir -p "$EXP"/{results/baseline,results/experimental,evals}

Write $EXP/run.py with:
- Module docstring: hypothesis verbatim
- VERIFIER_UNDER_TEST: list of verifiers changed
- METRIC: the single measurement
- BASELINE_CONFIG: default Veridian, unmodified
- EXPERIMENTAL_CONFIG: only changed parameters
- 20 test tasks (Independent, Realistic, Verifiable, Stable)
- compute_metric(results) → float: deterministic, no LLM calls

PHASE 3 — RUN (baseline ALWAYS first)
python "$EXP/run.py" --mode baseline > "$EXP/results/baseline/run.json"
# Verify baseline ran cleanly before proceeding
python "$EXP/run.py" --mode experimental > "$EXP/results/experimental/run.json"

PHASE 4 — EVALUATE + GRADE
Compute delta, significance (>5% threshold), extract and verify claims.
Write $EXP/RESULTS.md: hypothesis + result (CONFIRMED/REJECTED/INCONCLUSIVE) +
claims verified + implication for Veridian (concrete next step or "no change").
Update SESSION_HANDOFF.md and benchmarks/VERIDIAN_BENCH.json.
CMD

w .claude/commands/veridian-bench-verifiers.md << 'CMD'
---
description: >
  Benchmark all 10 verifiers: pass_rate mean/stddev, flaky detection, trend.
  USE: after modifying any verifier, before releases, weekly health check.
  Trigger on: "bench verifiers", "verifier benchmark", "verifier performance",
  "how are verifiers performing", "verifier health".
mode: read-only
idempotent: true
---

python3 .claude/scripts/bench.py \
  --eval-set evals/veridian-evals.json \
  --runs 3 \
  --output "benchmarks/bench-$(date +%Y%m%d-%H%M).json" \
  --compare benchmarks/VERIDIAN_BENCH.json

Status interpretation:
- pass_rate ≥ 0.95, stddev < 0.10 → STABLE (no action)
- pass_rate 0.90-0.95 → DEGRADED (P2, investigate)
- pass_rate < 0.90 → P0 — stop everything
- stddev > 0.15 → FLAKY (P1, investigate non-determinism)

Flag any verifier below threshold as P1. Add to SESSION_HANDOFF.md.
CMD

w .claude/commands/veridian-aria-ingest.md << 'CMD'
---
description: >
  Ingest ARIA daily briefing and extract Veridian-relevant research.
  Proposes experiments from findings. USE AT start of every intel session,
  before Phase 5 innovation radar. Trigger on: "aria ingest", "check aria",
  "aria briefing", "daily research", "what did aria find".
mode: read-only
---

ARIA delivers curated daily briefings: LLMs, Agentic AI, Legal AI, Compliance, Open Source.
Delivered via GitHub Actions + Claude API + Gmail (cron, ~$1.20/month).

STEP 1 — LOCATE ARIA OUTPUT
ls -t ~/aria-output/*.md 2>/dev/null | head -1 | xargs cat 2>/dev/null \
  || echo "No local ARIA output — check Gmail for today's ARIA Daily Brief"

STEP 2 — EXTRACT AND TAG (from briefing content)
[VERIFIER]    → new technique applicable to a specific verifier
[ARCHITECTURE]→ framework-level pattern that could change Veridian's design
[COMPETITOR]  → competitor move affecting Veridian's positioning
[EXPERIMENT]  → research mapping to a concrete Veridian experiment
[MOAT]        → strengthens or threatens the "deterministic Python" moat

STEP 3 — PROPOSE EXPERIMENTS (from [EXPERIMENT] tags)
For each: Finding | Maps to verifier | Hypothesis (canonical form) | Effort | Impact

STEP 4 — COMPETITOR UPDATES (from [COMPETITOR] tags)
Update PROJECT_INTEL.md if threat level changes.

STEP 5 — DIGEST
=== ARIA → VERIDIAN [date] ===
[N] findings | [V] verifier-relevant | [E] experiment proposals | moat: [stable|watch]
TOP EXPERIMENT PROPOSAL: [hypothesis] → run with /veridian:experiment
CMD

w .claude/commands/veridian-self-eval.md << 'CMD'
---
description: >
  Run the skill-creator evaluation loop on Veridian's own skills and commands.
  Detects instruction-following gaps, proposes improvements.
  USE WHEN: after modifying a skill/command, quarterly audit.
  Trigger on: "self eval", "skill health", "are my skills working",
  "check command quality", "evaluate skills".
mode: read-only
---

Applies the skill-creator grader + analyzer pattern to Veridian's own .claude/ files.

STEP 1: Load eval set (evals/veridian-evals.json)
STEP 2: Baseline — simulate Claude WITHOUT reading .claude/skills/ (what base model does)
STEP 3: With-skills — follow each skill's instructions exactly, record what changes
STEP 4: Grade per skill — score instruction-following 1-10:
  9-10: all instructions followed, output matches intent
  7-8:  minor deviations, correct outcome
  <7:   significant gaps or skipped steps

STEP 5: Analyze — for skills scoring < 8:
  Which instruction was ambiguous? Which step was skipped and why?
  Propose fix: imperative, specific, not vague. Priority: high/medium/low.

STEP 6: Improve — apply high-priority fixes now. Re-grade. Stop when all ≥8/10.
Track versions in evals/skill-history.json.
CMD

# ════════════════════════════════════════════════════════════
# VERIDIAN SKILLS (3)
# ════════════════════════════════════════════════════════════
info "Writing Veridian skills..."

w .claude/skills/veridian-verifiers.md << 'SKILL'
---
name: veridian-verifiers
description: >
  Architecture and contracts for Veridian's 10 deterministic Python verifiers.
  USE WHEN: adding a verifier, modifying the verification chain, debugging
  verification failures, writing verifier tests, working on TaskQualityGate
  or ConfidenceScore. Also trigger on: "verifier", "verification chain",
  "deterministic check", "agent output validation", "quality gate",
  "confidence score", "why is result block not emitting".
---

## The contract (not guards — contracts)

Veridian's verifiers enforce a contract: the agent promises to do a task,
the verifiers check whether the promise was kept — independently of the agent.
This is the difference between a self-signed certificate and a trusted CA.
Do not weaken this. It is the entire product.

## The 10 verifiers — chain order is load-bearing

| # | Verifier | Contract | Fail behaviour |
|---|---------|----------|----------------|
| 1 | SemanticGroundingVerifier | Output grounded in task input, no hallucination | Reject + log grounding score + offending fact |
| 2 | SelfConsistencyVerifier | Output internally consistent | Reject + log inconsistency location |
| 3 | CrossRunConsistencyHook | Consistent with prior runs on same input | Warn + log drift score (threshold: 0.15) |
| 4 | TaskQualityGate | Aggregates all scores, applies threshold | Reject if aggregate < threshold |
| 5 | ConfidenceScore | Calibrated confidence on output quality | Attach to result block: confidence="[0-1]" |
| 6-10 | Domain verifiers | Project-specific checks | Per-verifier fail policy |

Chain rule: failure at position N stops the chain. Result block is NEVER emitted.

## Adding a verifier (canonical protocol — 9 steps)

1. src/veridian/verifiers/[name]_verifier.py — inherit BaseVerifier
2. verify(task: AgentTask, output: AgentOutput) -> VerificationResult — no Any
3. Register in src/veridian/verifiers/__init__.py (__all__)
4. Wire in src/veridian/core/verifier_chain.py at correct position
5. Write ≥3 tests: pass case, fail case, edge case
6. Add to evals/veridian-evals.json (≥1 realistic eval)
7. Run /veridian:bench-verifiers — verify ≥0.95 pass rate
8. Run /veridian:verifier-audit — confirm connectivity
9. Append to CHANGELOG.md [Unreleased]

## Verifier test quality (genuine completion, not surface compliance)

BAD test: assert result.passed is False
GOOD test: assert result.passed is False AND "grass" in result.reason
  The bad test passes for any rejection. The good test proves the REASON is correct.

```python
def test_semantic_grounding_rejects_hallucination():
    """Verifier must reject output containing facts absent from task input."""
    task = AgentTask(input="The sky is blue.", instructions="Summarize.")
    output = AgentOutput(result="The sky is blue and the grass is green.")
    result = SemanticGroundingVerifier().verify(task, output)
    assert result.passed is False
    assert "grass" in result.reason      # must NAME the hallucinated fact
    assert result.grounding_score < 0.5  # quantitative threshold

def test_semantic_grounding_passes_grounded_output():
    task = AgentTask(input="The sky is blue.", instructions="Summarize.")
    output = AgentOutput(result="The sky has a blue color.")
    result = SemanticGroundingVerifier().verify(task, output)
    assert result.passed is True
    assert result.grounding_score >= 0.9

def test_semantic_grounding_edge_paraphrase():
    """Paraphrasing input facts must be accepted (not require literal copy)."""
    task = AgentTask(input="The sky is blue.", instructions="Summarize.")
    output = AgentOutput(result="The sky has a blue color.")
    result = SemanticGroundingVerifier().verify(task, output)
    assert result.passed is True
```

## ConfidenceScore — required before result block

Format: <veridian:result confidence="0.87">…</veridian:result>
Consumers reject blocks without confidence. A result without confidence is a claim without evidence.
ConfidenceScore must appear at a lower line number than veridian:result in every file.
SKILL

w .claude/skills/veridian-providers.md << 'SKILL'
---
name: veridian-providers
description: >
  LiteLLMProvider, circuit breaker, retry, fallback, VERIDIAN_MODEL.
  USE WHEN: adding model support, debugging provider failures, modifying retry
  or circuit breaker behaviour, troubleshooting VERIDIAN_MODEL.
  Trigger on: "LiteLLM", "circuit breaker", "VERIDIAN_MODEL", "retry",
  "fallback model", "provider error", "model selection", "rate limit".
---

## Why LiteLLMProvider is mandatory

Three contracts break simultaneously if bypassed:
1. Circuit breaker: without it, a failing LLM floods the provider infinitely
2. Retry with backoff + jitter: without jitter, all retriers synchronise (thundering herd)
3. Fallback: primary degraded without fallback = entire agent down

Any direct LLM call violates all three contracts simultaneously.
The failure mode: works fine until it doesn't, catastrophically.

## VERIDIAN_MODEL — fail loud, never silently default

```python
# WRONG — silently uses wrong model
client = LiteLLM(model="claude-sonnet-4-6")

# WRONG — silently defaults, hides missing config
model = os.environ.get("VERIDIAN_MODEL", "claude-sonnet-4-6")

# RIGHT — fails immediately if not configured (KeyError = explicit signal)
model = os.environ["VERIDIAN_MODEL"]
client = LiteLLMProvider(model=model)
```

Why `.get()` is wrong: CI passes, prod fails on first deploy — 6 months of false confidence.

## Circuit breaker states

CLOSED → (N failures) → OPEN → (timeout) → HALF-OPEN → (probe passes) → CLOSED

Env vars (never hardcode — breaking change risk):
- VERIDIAN_CIRCUIT_THRESHOLD: failures before opening (default: 5)
- VERIDIAN_CIRCUIT_WINDOW: window in seconds (default: 60)
- VERIDIAN_CIRCUIT_TIMEOUT: seconds before half-open probe (default: 60)
- VERIDIAN_MAX_RETRIES: retry count (default: 3)
- VERIDIAN_FALLBACK_MODEL: must be a DIFFERENT provider from primary

## Bench implication

High bench stddev (>0.15) for a verifier? Check circuit breaker logs first.
An open circuit during bench skews pass rates downward — this is the most common
false alarm before concluding a verifier is broken.
SKILL

w .claude/skills/veridian-bench.md << 'SKILL'
---
name: veridian-bench
description: >
  Benchmark infrastructure for verifier performance tracking.
  USE WHEN: interpreting benchmark results, updating VERIDIAN_BENCH.json,
  investigating verifier degradation, running bench-verifiers.
  Trigger on: "benchmark", "verifier performance", "VERIDIAN_BENCH",
  "pass rate", "flaky verifier", "performance regression", "stddev".
---

## benchmark.json schema (skill-creator standard)

Per-verifier: 3 runs × pass_rate, with mean/stddev/min/max in run_summary.
Flaky: stddev > 0.15. Degraded: mean < 0.95. Critical: mean < 0.90.

## VERIDIAN_BENCH.json — running log

Located: benchmarks/VERIDIAN_BENCH.json
Append after every bench run. Never truncate. This is the audit trail.
Format: array of benchmark entries with run_date added.

## Trend interpretation

| Mean | Trend | Action |
|------|-------|--------|
| ≥0.95 | → stable | None |
| ≥0.95 | ↑ improving | Note in CHANGELOG |
| 0.90-0.95 | → stable | P2 — investigate |
| 0.90-0.95 | ↓ degrading | P1 — fix before release |
| <0.90 | any | P0 — stop everything |
| stddev >0.15 | any | P1 — flaky, find non-determinism |

## Flaky verifier investigation

1. Is the verifier making LLM calls? (must be Python only — bug if yes)
2. Is the eval task underdetermined? (update eval to be more specific)
3. Is there shared state between runs? (concurrency bug)
SKILL

# ════════════════════════════════════════════════════════════
# bench.py
# ════════════════════════════════════════════════════════════
info "Writing bench.py..."

w .claude/scripts/bench.py << 'BENCH'
#!/usr/bin/env python3
"""
Veridian verifier benchmark script.
Usage: python .claude/scripts/bench.py --eval-set evals/veridian-evals.json [--runs 3] [--gate 0.95]
Produces: benchmark.json (skill-creator schema) + appends to VERIDIAN_BENCH.json
"""
import argparse, json, pathlib, statistics, sys, time
from datetime import datetime, timezone


def run_bench(eval_set_path: str, n_runs: int = 3, gate: float = 0.95) -> tuple[dict, bool]:
    evals_data = json.loads(pathlib.Path(eval_set_path).read_text())
    evals = evals_data.get("evals", [])

    results: dict = {
        "metadata": {
            "skill_name": "veridian",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "runs_per_configuration": n_runs,
            "eval_set": eval_set_path,
        },
        "per_verifier": {},
        "run_summary": {},
        "notes": [],
    }

    try:
        from veridian.verifiers import (
            SemanticGroundingVerifier, SelfConsistencyVerifier,
            CrossRunConsistencyHook, TaskQualityGate, ConfidenceScore,
        )
        verifiers: dict = {
            "SemanticGroundingVerifier": SemanticGroundingVerifier(),
            "SelfConsistencyVerifier": SelfConsistencyVerifier(),
            "CrossRunConsistencyHook": CrossRunConsistencyHook(),
            "TaskQualityGate": TaskQualityGate(),
            "ConfidenceScore": ConfidenceScore(),
        }
    except ImportError as e:
        print(f"Import failed: {e}\nInstall with: pip install -e '.[dev]'", file=sys.stderr)
        sys.exit(1)

    all_pass = True
    for name, verifier in verifiers.items():
        runs = []
        for run_n in range(1, n_runs + 1):
            passed = 0; total = 0; t0 = time.monotonic()
            for case in evals:
                total += 1
                try:
                    r = verifier.verify(
                        case.get("prompt", ""),
                        case.get("expected_output", "")
                    )
                    if getattr(r, "passed", True):
                        passed += 1
                except Exception:
                    pass
            elapsed = time.monotonic() - t0
            rate = passed / max(total, 1)
            runs.append({"run": run_n, "pass_rate": round(rate, 4),
                         "time_seconds": round(elapsed, 2), "errors": 0})

        results["per_verifier"][name] = runs
        rates = [r["pass_rate"] for r in runs]
        mean = statistics.mean(rates)
        stddev = statistics.stdev(rates) if len(rates) > 1 else 0.0

        results["run_summary"][name] = {
            "pass_rate": {
                "mean": round(mean, 3), "stddev": round(stddev, 3),
                "min": min(rates), "max": max(rates),
            }
        }

        status = "OK" if mean >= gate else "FAIL"
        flaky = " FLAKY" if stddev > 0.15 else ""
        print(f"  [{status}] {name}: {mean:.3f} ± {stddev:.3f}{flaky}")

        if mean < gate:
            all_pass = False
        if stddev > 0.15:
            results["notes"].append(f"{name} is flaky (stddev={stddev:.3f}) — P1")

    return results, all_pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Veridian verifier benchmark")
    ap.add_argument("--eval-set", required=True, help="Path to evals/veridian-evals.json")
    ap.add_argument("--runs", type=int, default=3, help="Runs per verifier (default: 3)")
    ap.add_argument("--gate", type=float, default=0.95, help="Pass rate gate (default: 0.95)")
    ap.add_argument("--output", default=None, help="Output benchmark JSON path")
    ap.add_argument("--compare", default=None, help="Running log to append to (VERIDIAN_BENCH.json)")
    args = ap.parse_args()

    print(f"Veridian verifier benchmark — {args.runs} runs per verifier, gate={args.gate}")
    print()

    bench, all_pass = run_bench(args.eval_set, args.runs, args.gate)

    ts = datetime.now().strftime("%Y%m%d-%H%M")
    out_path = pathlib.Path(args.output or f"benchmarks/bench-{ts}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(bench, indent=2))
    print(f"\nReport: {out_path}")

    # Append to running log
    log_path = pathlib.Path(args.compare or "benchmarks/VERIDIAN_BENCH.json")
    log: list = []
    if log_path.exists() and log_path.stat().st_size > 0:
        try:
            log = json.loads(log_path.read_text())
        except json.JSONDecodeError:
            log = []
    log.append({**bench, "run_date": datetime.now(timezone.utc).isoformat()})
    log_path.write_text(json.dumps(log, indent=2))
    print(f"Log updated: {log_path}")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
BENCH

chmod +x .claude/scripts/bench.py

# ════════════════════════════════════════════════════════════
# evals/veridian-evals.json
# ════════════════════════════════════════════════════════════
w evals/veridian-evals.json << 'EVALS'
{
  "skill_name": "veridian",
  "description": "Evaluation set for Veridian's 10 verifiers. Independent, Realistic, Verifiable, Stable.",
  "evals": [
    {
      "id": 1,
      "prompt": "Summarize the following: The sky is blue.",
      "expected_output": "A summary that only mentions the sky being blue — no added facts.",
      "expectations": [
        "SemanticGroundingVerifier passes: output contains no facts absent from input",
        "Result block emitted with confidence >= 0.7",
        "No hallucinated entities in output"
      ]
    },
    {
      "id": 2,
      "prompt": "Summarize: The sky is blue. The grass is green. Then describe the weather.",
      "expected_output": "Reject: output introduces facts about weather not in input.",
      "expectations": [
        "SemanticGroundingVerifier rejects: hallucinated weather fact",
        "Result block NOT emitted",
        "reason field names the hallucinated fact specifically"
      ]
    },
    {
      "id": 3,
      "prompt": "Count the words in: hello world foo bar",
      "expected_output": "4",
      "expectations": [
        "SelfConsistencyVerifier passes: output is internally consistent",
        "TaskQualityGate passes: deterministic factual task",
        "ConfidenceScore >= 0.85 for deterministic output"
      ]
    },
    {
      "id": 4,
      "prompt": "Extract all email addresses from: contact foo@example.com or bar@test.org",
      "expected_output": "[\"foo@example.com\", \"bar@test.org\"]",
      "expectations": [
        "All 10 verifiers run before result block emitted",
        "OutputSanitizer ran before result block",
        "Result block format: <veridian:result confidence=\"...\">..."
      ]
    },
    {
      "id": 5,
      "prompt": "Classify sentiment: I love this product!",
      "expected_output": "positive",
      "expectations": [
        "CrossRunConsistencyHook: same output on re-run (stable classification)",
        "TaskQualityGate aggregates verifier scores correctly",
        "ConfidenceScore reflects verifier agreement"
      ]
    }
  ]
}
EVALS

# ════════════════════════════════════════════════════════════
# FINAL VERIFICATION
# ════════════════════════════════════════════════════════════
echo ""
echo -e "${B}════════════════════════════════════════════════${N}"
echo -e "${B}  VERIFICATION                                  ${N}"
echo -e "${B}════════════════════════════════════════════════${N}"

ERRORS=0

# Tier 1
for f in CLAUDE.md AGENTS.md SESSION_HANDOFF.md PROJECT_INTEL.md; do
  [ -f "$f" ] && ok "$f" || { warn "MISSING: $f"; ERRORS=$((ERRORS+1)); }
done

# settings.json
python3 -m json.tool .claude/settings.json > /dev/null 2>&1 \
  && ok "settings.json (valid JSON)" \
  || { warn "settings.json invalid JSON"; ERRORS=$((ERRORS+1)); }

# Generic commands
for c in start-session end-session intel-briefing quick-intel health-check competitor-scan innovation-radar; do
  [ -f ".claude/commands/${c}.md" ] && ok "/project:${c}" || { warn "MISSING: /project:${c}"; ERRORS=$((ERRORS+1)); }
done

# Veridian commands
for c in veridian-verifier-audit veridian-release-check veridian-pr-review \
          veridian-experiment veridian-bench-verifiers veridian-aria-ingest veridian-self-eval; do
  [ -f ".claude/commands/${c}.md" ] && ok "/veridian:${c#veridian-}" || { warn "MISSING: /${c}"; ERRORS=$((ERRORS+1)); }
done

# Skills
for s in veridian-verifiers veridian-providers veridian-bench session-intelligence; do
  [ -f ".claude/skills/${s}.md" ] && ok "skill: ${s}" || { warn "MISSING skill: ${s}"; ERRORS=$((ERRORS+1)); }
done

# Infrastructure
[ -f ".claude/scripts/bench.py" ] && ok "bench.py" || { warn "MISSING bench.py"; ERRORS=$((ERRORS+1)); }
[ -f "evals/veridian-evals.json" ] && ok "evals/veridian-evals.json" || { warn "MISSING evals"; ERRORS=$((ERRORS+1)); }
[ -f ".claude/context-essentials.md" ] && ok "context-essentials.md" || { warn "MISSING essentials"; ERRORS=$((ERRORS+1)); }
[ -f "benchmarks/VERIDIAN_BENCH.json" ] && ok "benchmarks/VERIDIAN_BENCH.json" || { warn "MISSING bench log"; ERRORS=$((ERRORS+1)); }

echo ""
if [ "$ERRORS" -eq 0 ]; then
  echo -e "${G}════════════════════════════════════════════════${N}"
  echo -e "${G}  Installation complete — 0 errors              ${N}"
  echo -e "${G}════════════════════════════════════════════════${N}"
else
  echo -e "${Y}Installation complete with ${ERRORS} warning(s)${N}"
fi

echo ""
echo -e "${B}Next steps:${N}"
echo "  1. Review CLAUDE.md — adjust stack and invariants for your actual paths"
echo "  2. Review PROJECT_INTEL.md — add real competitor URLs and arXiv categories"
echo "  3. Open Claude Code: claude ."
echo "  4. First command: /project:start-session"
echo ""
echo -e "${B}Command reference:${N}"
echo "  /project:start-session       → every session start"
echo "  /project:end-session         → every session end"
echo "  /project:quick-intel         → daily triage (5 min)"
echo "  /project:intel-briefing      → weekly briefing (12 min)"
echo "  /veridian:verifier-audit     → before every PR"
echo "  /veridian:release-check      → before every PyPI publish"
echo "  /veridian:bench-verifiers    → weekly performance check"
echo "  /veridian:experiment         → research hypothesis → grade"
echo "  /veridian:aria-ingest        → ARIA briefing → experiments"
echo ""
echo -e "${G}Done. Run: claude .${N}"
