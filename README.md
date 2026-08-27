# Veridian

**Deterministic assurance and replay-safe effects for AI agents.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/veridian-ai.svg)](https://pypi.org/project/veridian-ai/)

Veridian is a Python library for enforcing a narrow boundary around agent work:
an agent may propose an output or action, but deterministic checks decide whether
it may be accepted or executed.

For ordinary task completion, the rule is simple:

> A task is not marked `DONE` unless an independent verifier passes.

For side effects, Veridian goes further. It binds the exact action, authorization
context, policy and state snapshot into a signed single-use permit. A trusted
executor—not the agent—holds credentials, redeems that permit, dispatches through
a durable outbox, and returns a signed effect receipt.

## What Is Included

- `veridian.gate`: a composed front door that turns the flow below into a decorator
- fail-closed `ALLOW` / `DENY` / `HOLD` decision algebra
- a deterministic task runner and local crash-recoverable WAL ledger
- canonical action, authorization, snapshot, evidence and transport models
- Ed25519-signed portable proof bundles with offline verification
- signed single-use execution permits, revocation and a transactional SQLite outbox
- OpenAI Responses, MCP, LangGraph, Pydantic AI profile and generic adapters
- invariant, aggregate, perturbation, metamorphic, barrier, stability and trajectory math
- synthetic banking and deployment/change-control verification packs
- CLI, composite GitHub Action and container delivery surfaces
- a versioned adversarial, concurrency and crash-recovery benchmark harness

The core trust flow is:

```text
Agent proposal
    -> protocol normalization (business semantics separate from transport)
    -> authenticated evidence + immutable state/policy snapshot
    -> deterministic clauses and bounded mathematics
    -> ALLOW / DENY / HOLD
    -> signed one-use permit (ALLOW only)
    -> trusted credential-holding executor + durable outbox
    -> signed effect receipt + domain postcondition verification
```

## Quick Start

Authorize one action behind a signed, single-use permit and get a receipt an
auditor can verify without you:

```python
from veridian import Gate, check

@check("amount_within_limit", config={"limit_minor": 100_000})
def amount_within_limit(ctx):
    return int(ctx.parameters["amount_minor"]) <= 100_000

@check("recipient_allowlisted")
def recipient_allowlisted(ctx):
    return str(ctx.parameters["to"]).startswith("acct:")

gate = Gate.for_development(
    audience="treasury-rail",
    checks=[amount_within_limit, recipient_allowlisted],
    store_path="permits.db",
)

@gate.guard("payment.transfer", target=lambda to, **_: to)
def transfer(*, to: str, amount_minor: int) -> str:
    """The credential holder. Runs only behind a verified ALLOW permit."""
    return f"rail-ref-{to}-{amount_minor}"

outcome = transfer(to="acct:1234", amount_minor=25_000)
```

`outcome.receipt` is signed, `outcome.proof_bundle` verifies offline, and
re-presenting the same permit replays the receipt instead of producing a second
effect. An over-limit call raises `GateDeniedError` and never reaches the
function body; a check that cannot decide yields `HOLD`, never `ALLOW`.

`veridian.gate` is porcelain over `veridian.assurance` and `veridian.effects`.
It adds no trust properties — every artifact it emits is an ordinary assurance
value that the same offline verifier accepts. Build them by hand when you need
control; use the gate when you need the common case.

Runnable: [`examples/gate_quickstart.py`](examples/gate_quickstart.py).
Full walkthrough: [`docs/quickstart.md`](docs/quickstart.md).

`Gate.for_development` generates an ephemeral key that nothing outside the
process can verify. Production gates take an operator-owned signer (KMS/HSM) and
a durable store — see [`docs/threat-model.md`](docs/threat-model.md).

## Documentation

| Document | Answers |
|---|---|
| [`docs/quickstart.md`](docs/quickstart.md) | How do I get from install to a verified receipt? |
| [`docs/threat-model.md`](docs/threat-model.md) | What is this trusted to do, and what defeats it? |
| [`docs/proof-format.md`](docs/proof-format.md) | What is in a proof bundle, and how does an independent party check it? |
| [`docs/mapping-eu-ai-act-article-12.md`](docs/mapping-eu-ai-act-article-12.md) | Which Article 12 obligations do these records support — and which do they not? |
| [`docs/mapping-open-agent-passport.md`](docs/mapping-open-agent-passport.md) | How do these objects relate to OAP and AP2? |
| [`CHANGELOG.md`](CHANGELOG.md) | What changed, and what was actually published |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | How to propose a change |

## Install

```bash
pip install veridian-ai
```

The base install requires `cryptography`, `filelock`, and `jsonschema`.
`cryptography` supplies exact-byte Ed25519 signing and verification; Veridian has
no embedded fallback signing key.

Optional extras:

```bash
pip install "veridian-ai[llm]"       # LiteLLM provider support
pip install "veridian-ai[http]"      # HTTP verifier support
pip install "veridian-ai[pdf]"       # PDF quote matching support
pip install "veridian-ai[pydantic]"  # Pydantic model validation
pip install "veridian-ai[all]"       # All runtime extras
```

## Release Status

Repository source and published release artifacts can differ. The version in
`pyproject.toml` identifies this checkout; the PyPI badge and GitHub Releases
page identify what is publicly installable. Pin a full commit SHA when using
source-only Action or container capabilities.

The `0.4.0` source line remains alpha software. Passing tests and finite
benchmarks are evidence only for the disclosed cases, not proof of zero residual
risk or a substitute for domain, security and cryptography review.

## Command Line

Run one registered verifier:

```bash
veridian verify \
  --verifier schema \
  --verifier-config '{"required_fields":["decision"]}' \
  --agent-output '{"decision":"ship"}' \
  --task "Release decision" \
  --output-path veridian-result.json
```

Exit status is `0` for a pass, `1` for a deterministic denial and `2` for a
configuration error. `--no-fail-on-error` changes the process status only; the
JSON result still records `"passed": false`.

Verify a portable proof bundle in a separate process with explicit public trust
roots:

```bash
veridian verify-receipt \
  --bundle proof.bundle.json \
  --keys verification-keys.json \
  --output-path proof-result.json
```

Offline verification checks the signature and disclosed byte/digest bindings.
Without authoritative nonce/time/state context it reports replay as
`not-checked`; without an independently retained head or witnesses it reports
history as `unanchored`.

### GitHub Action

Pin the Action to a reviewed commit and pass explicit verifier configuration:

```yaml
- uses: actions/checkout@v4
- uses: AV-CSE31/veridian/.github/actions/verify@<commit-sha>
  with:
    verifier: schema
    verifier-config: '{"required_fields":["decision"]}'
    agent-output: '{"decision":"ship"}'
```

The Action installs the source at that pinned revision rather than silently
substituting an older package.

### Container

```bash
docker build -t veridian:local .
docker run --rm veridian:local verify --agent-output "completion evidence"
```

The image uses the same installed `veridian` entrypoint as the wheel.

## Industrial Banking Showcase

Run the offline USD 12.5M RTGS scenario:

```bash
python examples/banking_agent_verification_demo.py
```

It demonstrates:

- an OpenAI Responses tool call normalized into exact business semantics
- signed control evidence, beneficiary/sanctions/funds/limit checks
- maker/checker quorum and separation of duties
- conservation, liquidity-barrier, perturbation and temporal mathematics
- an `ALLOW`-only signed permit and credential-isolated executor
- an idempotent retry producing one economic effect
- independently verified effect and settlement receipts
- rejection of an amount mutation after approval

The rail is a deterministic synthetic RTGS adapter. It uses no bank credentials,
network or production data and does not assert that authorization equals final
settlement.

## Coding-Agent Merge Gate

```bash
python examples/coding_agent_verification_demo.py
```

This local demo creates passing and blocked Git repositories. It checks tests,
coverage regression, Python compilation, changed-path policy, protected paths
and obvious secret patterns, then emits a signed completion proof and a Markdown
PR comment. It is not a hosted GitHub App.

## Task Runner

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from veridian import MockProvider, Task, TaskLedger, VeridianRunner

schema = {
    "required": ["decision", "risk", "reason"],
    "properties": {
        "decision": {"type": "string", "enum": ["ship", "hold"]},
        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "reason": {"type": "string", "minLength": 8},
    },
}

with TemporaryDirectory() as tmp:
    ledger = TaskLedger(
        Path(tmp) / "ledger.json",
        progress_file=str(Path(tmp) / "progress.md"),
    )
    ledger.add([
        Task(
            title="Release gate",
            description="Decide whether the build can ship.",
            verifier_id="schema",
            verifier_config={"schema": schema},
        )
    ])
    provider = MockProvider().script_veridian_result(
        structured={
            "decision": "ship",
            "risk": "low",
            "reason": "tests, lint, and verification passed",
        }
    )
    summary = VeridianRunner(ledger=ledger, provider=provider).run()
    assert summary.done_count == 1
```

The WAL is the default task-ledger mode. Each acknowledged transition is
checksummed, hash-chained and anchored locally before success is returned;
snapshots are compacted by generation. This is a single-host durability model,
not a distributed consensus or cross-region database.

The persistence boundary is explicit: snapshots and the durable WAL-head
sidecar use same-directory temporary files followed by `os.replace`; transition
records use append, flush and `fsync` before the atomic head advances. Recovery
accepts only the checksummed prefix named by that head and may discard only an
unacknowledged partial tail. An append-only WAL is therefore a durable journal,
not an atomic snapshot file.

## Completion Contracts and Legacy Reports

`verify_completion(...)` supports framework-neutral completion gates. The
legacy JSONL path uses an operator-supplied HMAC key and redacts raw payloads and
verifier diagnostics by default:

```python
import os

from veridian import VerificationContract, VerifierStep, verify_completion

contract = VerificationContract(
    contract_id="release_gate",
    verifiers=[
        VerifierStep(
            verifier_id="schema",
            verifier_config={"schema": {"required": ["decision"]}},
        )
    ],
)

decision = verify_completion(
    contract=contract,
    input_payload={"task": "decide release"},
    output_payload={"decision": "ship"},
    proof_file="veridian-proof.jsonl",
    signing_key=os.environ["VERIDIAN_PROOF_SIGNING_KEY"],
)
assert decision.passed
```

Runner report export is opt-in and also requires an explicit key:

```python
import os

from veridian import VeridianConfig
from veridian.core.report import validate_report_chain

key = os.environ["VERIDIAN_REPORT_SIGNING_KEY"]
config = VeridianConfig(
    report_file="verification-reports.jsonl",
    report_signing_key=key,
    report_signing_key_id="kms-key-version-7",
)
validation = validate_report_chain(config.report_file, signing_key=key)
assert validation.valid
```

Historical `verification-report.v1` archives were hash-chained but unsigned.
They remain read-only and fail closed unless an auditor opts in explicitly:

```python
legacy = validate_report_chain(
    "archived-v1-reports.jsonl",
    allow_legacy_v1=True,
    trusted_head="<independently-retained-sha256-head>",
)
assert legacy.valid
assert legacy.legacy_unsigned_count > 0
```

Opt-in validates the historical bytes and links; it does not sign, upgrade or
append to a v1 chain. The returned limitations always identify unsigned legacy
records.

HMAC is symmetric: any holder of the key can create or rewrite a valid chain.
An unanchored local chain cannot detect a valid rollback, fork selection or
suffix truncation. Prefer the Ed25519 assurance kernel for independently
checkable receipts, and retain a head/witness outside the system being audited
when history integrity matters.

## Mathematics and Domain Packs

`veridian.math` exposes deterministic, stateless verifiers for:

- exact linear equality, conservation and bounds with explicit units/tolerance
- authenticated rolling aggregates and split-action detection
- supplied L1/L2/L-infinity perturbations and metamorphic control monotonicity
- finite-disturbance affine barriers and explicitly model-relative risk stability
- deterministic state machines, precedence, freshness, single-use and bounded outcomes

Every result includes stable status/reason codes, operands, a derivation,
margin/bound, assumptions, evidence references and an available counterexample.
Binary floating point is rejected in exact monetary paths. Sampled perturbations
and finite disturbances never become claims over an unexamined continuous or
universal domain.

The synthetic packs are:

- `veridian.math.banking`: critical payment accounting, liquidity and trajectory checks
- `veridian.math.deployment`: quorum, separation of duties, canary, change window,
  rollback readiness, error-budget barrier and deployment trajectory checks
- `repo_guard` plus the coding-agent demo: repository acceptance controls whose
  verdict evidence binds the observed changed paths and bytes with a stable
  `repo_state_digest`

## Protocol and Telemetry Adapters

`veridian.adapters` normalizes proposed actions from direct/generic Python,
OpenAI Responses, MCP, LangGraph and a versioned Pydantic AI deferred-tool
profile. Adapters never execute tools and do not import framework SDKs. Business
semantics determine `ActionSemanticsV1.digest`; protocol IDs and raw-message
digests remain in a separate `TransportBinding`.

`veridian.assurance` also provides a dependency-free OpenTelemetry semantic
mapping for decision → permit → execution → receipt links. It exports only
bounded statuses and digests/identifier commitments through a minimal Span-like
protocol; it does not configure a global telemetry SDK or export raw payloads.

## Benchmarks

The versioned harness covers canonical byte mutation, permit context and replay,
SQLite concurrent redemption and abrupt-process recovery, cross-adapter semantic
determinism, banking invariants, metamorphic controls and trajectories:

```bash
uv run python benchmarks/sota_assurance_bench.py \
  --profile smoke --iterations 8 --seed 20260819 --concurrency 4

uv run python benchmarks/sota_assurance_bench.py \
  --profile campaign --iterations 100000 --seed 20260819 \
  --concurrency 16 --output assurance-campaign.json
```

Reports include the exact schedule/configuration, environment, outcome counts,
latency percentiles, reproducibility fingerprint, limitations and an explicit
non-zero-residual-risk statement. See
[`benchmarks/README.md`](benchmarks/README.md).

## Built-In Verifiers

- `schema`: Draft 2020-12 JSON Schema, required fields or optional Pydantic model
- `file_exists`: require an artifact path
- `bash_exit`: validate captured shell exit codes
- `http_status`: check an endpoint with the `http` extra
- `quote_match`: match quoted evidence, with PDF behind the `pdf` extra
- `composite`: require multiple checks
- `any_of`: pass when at least one check passes
- `repo_guard`: block unexpected coding-agent diffs and obvious secret patterns

## Current Boundaries

Veridian `0.4.0` does not provide a managed control plane, distributed ledger,
hosted PR reviewer, general policy DSL, identity issuer, production RTGS
connector or OS-level verifier sandbox. `IsolatedVerifierRunner` is a bounded
subprocess protocol, not a security sandbox. The SQLite permit/outbox reference
is single-host and exactly-once economic effects still require the downstream
adapter to honor the supplied idempotency key.

The library cannot protect against compromised trusted signers, credential
executors, authoritative evidence producers or independent anchors. Operators
must supply real policy thresholds, key management, evidence governance,
retention, monitoring and domain review. Do not market finite local results as
an externally validated 1.0 or universal safety proof.

## Examples

- [`examples/gate_quickstart.py`](examples/gate_quickstart.py) — permit, receipt and offline proof in one file
- [`examples/banking_agent_verification_demo.py`](examples/banking_agent_verification_demo.py)
- [`examples/coding_agent_verification_demo.py`](examples/coding_agent_verification_demo.py)
- [`examples/decorator_release_gate.py`](examples/decorator_release_gate.py)
- [`examples/runner_release_gate.py`](examples/runner_release_gate.py)
- [`examples/artifact_verification_gate.py`](examples/artifact_verification_gate.py)

## Development

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy veridian --strict
uv run pytest --cov=veridian --cov-fail-under=85 -q
```

Public documentation lives in [`docs/`](docs/README.md). Local planning and
research material stays private and is not part of the public package; files
under `docs/` are made public individually in `.gitignore`.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) before opening a pull request, and
[`RELEASING.md`](RELEASING.md) before cutting a release.

## License

MIT. See [LICENSE](LICENSE).
