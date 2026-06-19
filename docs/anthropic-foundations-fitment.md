# Veridian × Anthropic orchestration foundations — fitment analysis

## Provenance

This note maps a private architecture evaluation —
*Anthropic / Claude Orchestration & Foundation Capabilities — Architecture
Evaluation, dated 2026-06-18, "internal evaluation / decision-support draft"
for a food-labeling compliance platform* — onto Veridian's current surface.

Two things the reader should know up front, because they affect how much of
this to trust:

1. The source document is itself a Claude-produced artifact ("This evaluation
   was generated with a deterministic multi-agent workflow and then
   human-reviewed", Appendix B, p.42). It is not an Anthropic press release
   and not an independent benchmark. Treat its product-availability claims
   exactly as the document itself instructs — "vendor/source claims to validate"
   (p.1).
2. This note is written by Claude as well. I have no special insight into
   Anthropic's preview roadmap. Where I quote the source, I cite the page.
   Where I draw conclusions about Veridian, I cite `file:line`.

If those caveats matter to a downstream decision, the reader should re-read
the source PDF directly.

## TL;DR

The source document spends 42 pages arguing that the right Anthropic
adoption posture is "build the harness that the Claude Agent SDK
deliberately does not ship, then wrap it around every loop before
production traffic" (p.2, repeated in Section 6, p.32–35, and the Risk
Matrix p.40). The list of things that harness must include is, almost
line-for-line, the surface area Veridian was built to occupy:

- iteration cap (Veridian: `Task.max_retries`, runner caps)
- wall-clock timeout (Veridian: **gap at run-level**)
- per-run token/$ budget (Veridian: `CostGuardHook` — **broken**, see below)
- repetition / oscillation detection (Veridian: **gap**)
- working kill switch (Veridian: `SIGINT` → `_RunController._shutdown` —
  `veridian/loop/run_controller.py:38`)
- hooks as the single audit/policy seam (Veridian: `HookRegistry`,
  `veridian/hooks/registry.py`)
- immutable audit log per tool call (Veridian: `TaskLedger` + `JsonlTraceHook`)
- independent grader in a fresh context (Veridian: `BaseVerifier`)
- versioned rubrics with their own eval set (Veridian: `verifier_id` +
  `verifier_config` — versioning **implicit**, not enforced)
- HMAC-signed, idempotent, tenant-scoped webhooks (Veridian: `WebhookAlertHook`
  signs nothing — **gap**)
- replayable control flow (Veridian: `build_run_replay_snapshot` +
  `check_replay_compatibility` — `veridian/loop/replay_compat.py`)

So the headline is positive: Veridian's design point is more or less
exactly what the document says every serious operator must build for
themselves. The unflattering follow-up is that several of these primitives
in Veridian are either broken or missing in ways the document specifically
flags as production-gating (e.g. `CostGuardHook`, repetition detection,
signed webhooks). Closing those is high-leverage work; the rest of this
note enumerates them.

What Veridian should **not** chase from this document: hosted Managed
Agents (a customer-side ops decision), Dreaming (memory curation belongs
to the application layer), and multi-agent subagent fan-out (the document
itself calls this a one-level ceiling on the SDK, and Veridian's worker
model is single-agent-per-task by design). Resist the temptation to
expand scope into these areas just because the document covers them.

---

## Section-by-section mapping

### 1. Managed Agents (Preview) — pp.3-5

The document's case against Managed Agents for the regulated path is the
data-residency / DPA / private-MCP egress overhead (p.4 table, p.5).
None of this is Veridian's concern at the library level — we run wherever
the caller's process runs. Two indirect implications:

- The document explicitly recommends "Runtime behind an interface" (p.36)
  so SDK ↔ Managed Agents are swappable. Veridian's `LLMProvider` ABC
  (`veridian/providers/base.py`) is already that interface for inference.
  Worth surfacing in our README: yes, swapping in a hosted runtime is one
  subclass.
- The document does **not** treat Managed Agents as a viable substrate
  for "the binding compliance verdict path" (p.5). Veridian's verifier
  framework lives on the caller's side and is not affected; this is a
  point in our favour against operators who would otherwise be tempted
  to outsource the loop entirely.

**Verdict:** no Veridian code change. Document the LLMProvider abstraction
explicitly in the README as the SDK↔hosted seam.

### 2. Multi-Agent Orchestration — pp.6-10

The document is clear that the GA part of "multi-agent" is just the SDK
running subagents in-process (p.6), and that the right adoption shape is
"agents emit candidate findings, never final verdicts" (p.10). The
document explicitly warns about the SDK's one-level subagent ceiling
(p.2, "do not architect recursive trees on the SDK alone").

Veridian today is single-worker-per-task by design
(`veridian/loop/task_dispatcher.py:173-177` — one `WorkerAgent` per call).
Adding subagent fan-out at the Veridian level would be **scope creep**
against the document's own advice: the document is using subagents to
parallelise context-heavy research, not to add verification layers. The
verification layer is precisely what Veridian already provides.

The doc's "shared-filesystem coordination" idea (p.7) — workers writing
typed artifacts that a lead reconciles — is closer to Veridian's existing
`TaskLedger` shape than to anything we'd need to build new. A multi-agent
caller can write artifacts to per-task dirs already; Veridian just verifies
each.

The doc's per-tenant working-directory point (p.10 risks table, "tenant
leakage") *is* a real gap relative to Veridian's stated audit posture:
we do not currently scope `Task` payloads or hook context by tenant.
This was deferred in PR #9's Section C ("TenantScope deferred") and
remains deferred — the document's analysis reinforces "wait for a
concrete customer" rather than "build it speculatively".

**Verdict:** no new code. Cite the per-task-dir pattern in our docs as a
caller-side practice; do not bake tenancy into Veridian until asked.

### 3. Dynamic Workflows in Claude Code — pp.11-17

This is the section most directly relevant to Veridian's threat model.
Two claims from the document are worth quoting:

> "Hard token/dollar budgets per run are non-negotiable" (p.17).
> "Cost explosion. Self-chosen fan-out of hundreds of subagents, each
> consuming tokens plus verification compute, is the headline financial
> risk." (p.17).

Veridian has `CostGuardHook` (`veridian/hooks/builtin/cost_guard.py`),
which **does not work**: it raises `CostLimitExceeded`, which is not a
`ControlFlowSignal` subclass, so `HookRegistry.fire` swallows it
(`veridian/hooks/registry.py:46-54`). This was bug #1 in PR #9's audit.
Against the document's "non-negotiable" framing this is a
production-blocking defect; it needs a fix in any roadmap claiming
"agent loop guardrails".

The doc also calls for "repetition/oscillation detection to halt stuck
loops before they burn budget" (p.2, Technical Summary). Veridian has
no such primitive. The natural shape is a `RepetitionGuardHook` that
hashes successive `TaskResult.summary`s or worker actions, halts when a
window of N consecutive results match, and routes the halt through the
same control-flow exception path as a fixed cost-cap.

The doc treats "checkpoint as the governance hook" (p.16, "a
human-approval gate, a budget check, or an audit-log write should fire
here — not after the whole run completes"). Veridian's pause/resume
machinery (`TaskPauseRequested`, `HumanReviewRequired` — both in
`veridian/core/exceptions.py:77-86`) already fits this shape. Nothing
to add here, but worth surfacing in the README as the canonical pattern.

**Verdict:** two real fixes — (a) `CostLimitExceeded` → `ControlFlowSignal`
subclass + a `RunAbortRequested` signal the runner handles by breaking
the loop instead of pausing one task; (b) a `RepetitionGuardHook`.
Both small, both high-leverage.

### 4. Outcomes — pp.18-22

This is the section where the document's design and Veridian's design
converge most directly. The "Outcomes" pattern as the document describes
it (pp.18-19):

> "you define what 'right' looks like as an explicit rubric — a checklist
> of pass criteria — and a separate grader (its own model call, in its
> own clean context window) scores the agent's output against that rubric."

…is exactly `BaseVerifier` + `verifier_id` + `verifier_config`
(`veridian/verify/base.py`). Veridian's pre-existing `decorator_release_gate.py`
example is the literal "rubric-gated release decision" the document
recommends piloting first (p.40, "First proof-of-concept").

What the document demands that Veridian doesn't enforce:

| Doc requirement (p.21-22) | Veridian status |
|---|---|
| Independent grader context | ✓ verifier runs separately, sees only `(task, result)` |
| Versioned rubric store | △ `verifier_config` is a dict; no version pin |
| Bounded retry loop | ✓ `Task.max_retries`, abandonment path |
| Signed webhook delivery (HMAC) | ✗ `WebhookAlertHook` sends unsigned bodies |
| Idempotent webhook receivers | ✗ no idempotency key in payload |
| Tenant-scoped grader context | △ no tenancy primitive |
| Grader model ID logged per gate | ✗ `verifier_id` logged, model not |
| Grader drift monitoring | ✗ no built-in pass-rate tracking |
| Terminal-fail → human path | ✓ `TaskStatus.ABANDONED` + `HumanReviewRequired` |

The two cheap, high-leverage fixes are the webhook ones — they're a
trivial code change and they're the specific feature the doc says is
non-optional ("the webhook is an inbound write path into prod data, so
it inherits all our isolation and audit requirements: it must be HMAC
signature-verified, idempotent, and tenant-scoped", p.21). Veridian
ships only the sender, but signing on the sender side is the half the
operator can't add themselves.

The "log grader model id + rubric version + verdict for every gate"
ask (p.22) is also cheap: extend `TaskResult.extras` with a stamped
`{verifier_id, verifier_version_hash, grader_model_id}` tuple from the
provider.

The document's cited "~10 percentage-point task-success lift" (p.20)
is explicitly flagged as "a vendor/source claim to validate". Do
**not** repeat it anywhere we publish.

**Verdict:** add HMAC signing + idempotency key to `WebhookAlertHook`,
stamp grader-model-id into result metadata, document the rubric-versioning
pattern. The Outcomes section is essentially a marketing description of
what Veridian already does — that's a good place to be.

### 5. Dreaming — pp.23-28

Memory curation is **out of Veridian's scope** and should stay out.
The document itself characterises it as "an emerging concept assembled
from mature parts (Agent SDK memory, Batches API, MCP, scheduler)"
(p.28), and its risk list (memory poisoning, stale lessons,
cross-tenant leakage, opaque regressions, pp.26-27) is exactly the
shape of risk that lives at the application layer, not the loop-harness
layer. Veridian provides the audit log and pause-on-human-review primitives
the application would use to govern any memory store; building the store
itself would be unfocused.

**Verdict:** explicitly note in the README that Veridian is not a
memory framework, and that memory curation patterns should be built
against Veridian's hook + ledger surface rather than inside it.

### 6. Claude Agent SDK — pp.29-35

This is where the document is most useful: it enumerates exactly the
"what the SDK doesn't ship" gaps Veridian is positioned to fill (pp.32-34):

> "No loop guards — runaway loops, cost blow-ups, and oscillation are
> our responsibility: iteration caps, wall-clock timeouts, token/cost
> budget ceilings, repetition detection, and a kill switch. **None of
> these ships in the box.**" (p.34, emphasis mine.)

The integration-complexity diagram on p.33 is almost identical to
Veridian's runner shape after PR #9's split:

| p.33 box | Veridian module |
|---|---|
| Start task | `VeridianRunner.run()` `veridian/loop/runner.py:139` |
| Iteration under cap | `Task.max_retries` + `Task.retry_count` |
| Within time budget | **gap** (no run-level wall-clock) |
| Within token/$ budget | `CostGuardHook` (**broken**) |
| Repetition detected | **gap** |
| Run one agent step | `_TaskDispatcher._process_task` |
| Audit log: tool, args, tokens, cost | `JsonlTraceHook` + ledger |
| Kill switch: halt + alert | `_RunController.install_signal_handler` |

The honest read: Veridian's surface already matches 5 of the 7 SDK-shaped
boxes the document demands. The two missing boxes (wall-clock budget,
repetition detection) are small primitives that fit naturally into the
existing hook framework. The third defect (`CostGuardHook` is broken) is
not a missing primitive but a real bug in a primitive that exists.

**Verdict:** the two missing boxes plus the one bug = exactly the
unit-of-work for a follow-up PR. Together they bring Veridian to "all
seven boxes from p.33 implemented". That's a defensible position.

---

## Concrete follow-up backlog (recommend, but do not bake into this PR)

In rough leverage order. Each is a separate small PR.

1. **Fix `CostLimitExceeded` propagation.** Subclass it from
   `ControlFlowSignal`, add a `RunAbortRequested(ControlFlowSignal)` signal
   that the dispatcher treats as "break loop" rather than "pause task", and
   route both cost and wall-clock breaches through it. Add an integration
   test that asserts the runner actually halts. Without this, any "we have
   a budget cap" claim is theatre. (~80 LOC + test.)

2. **`WallClockBudgetHook`.** Hard run-level wall-clock cap. Same
   `RunAbortRequested` mechanism as (1). The doc's "wall-clock timeout"
   item from the non-negotiable list. (~30 LOC + test.)

3. **`RepetitionGuardHook`.** Detects oscillation across a window of
   recent results. Hash either the worker `summary` or the diff of
   `TaskResult.structured` across the window; halt when N consecutive
   results match. The doc's "repetition/oscillation detection" item.
   (~60 LOC + test.)

4. **HMAC + idempotency in `WebhookAlertHook`.** Optional `secret` kwarg
   that adds `X-Veridian-Signature: sha256=…` to outgoing POSTs; include
   a stable `idempotency_key` field in every payload so receivers can
   dedupe. The doc explicitly calls this non-optional for any inbound
   prod-write path (p.21). (~25 LOC + test.)

5. **Stamp `verifier_version_hash` + `grader_model_id` into
   `TaskResult.extras`.** Cheap audit improvement; lets operators
   detect rubric drift and pinpoint which verifier version produced a
   given verdict. (~15 LOC.)

Not in this list, on purpose: tenancy, subagent fan-out, memory
curation, hosted-runtime adapter. Each was considered and the document's
own analysis argues against doing them inside Veridian.

## Things to lift directly into the README

The doc is well-written; some of its framing is more accurate than ours.
Specifically:

- "Hooks as the single audit/policy seam" (p.2) — better than our
  current "extension points" framing.
- The seven-box runner diagram on p.33 — Veridian's runner is
  essentially this picture; we should reproduce the boxes and check off
  what we provide.
- The "candidate-then-confirm" pattern (p.37) — a clean way to describe
  the verifier framework to a regulated-industry caller.

## What to ignore from the doc

- The "~10 percentage-point task-success lift" number for Outcomes (p.20,
  p.39) — the doc itself flags this as unverified.
- The "v0.3.149" Workflow tool version (p.29, Appendix A item 4) — same.
- The dollar/percent figures in Cost-Benefit (p.39) — the doc derives
  them from a single vendor's stated pricing; not load-bearing for our
  positioning.
- The 6-family pipeline references (`assessment_pipeline.py`,
  `run_label_assessment_for_iteration`) — application-specific to the
  source organisation; useful as a concrete example of "the
  deterministic verdict path", but no code to lift.

---

## Disclosure

I drafted this note by reading the PDF page-by-page and grepping the
Veridian source for each primitive the doc mentions. Any claim about
Veridian's behaviour has a `file:line` next to it; check those before
acting on the conclusions. Any claim about Anthropic products
references a page in the source PDF; the source PDF itself flags
preview-stage items as unverified.
