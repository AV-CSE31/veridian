# Production Deployment

This page is the single operator entry-point for the production-hardening
features added across Phases 1–4. Use it to wire Veridian into a
container orchestrator (Kubernetes, ECS, Nomad, …) with the right
security, observability, and lifecycle defaults.

If you only read one thing here, read **[Quick start](#quick-start)**.

## Quick start

```bash
# 1. Build the image
docker build -t veridian:0.3 .

# 2. Run it with persistent state on a mounted volume
docker run --rm \
  -e VERIDIAN_DATA_DIR=/var/lib/veridian \
  -e VERIDIAN_MODEL=gemini/gemini-2.5-flash \
  -e VERIDIAN_LOG_FORMAT=json \
  -e VERIDIAN_MAX_PARALLEL=4 \
  -e VERIDIAN_MAX_COST_USD=25 \
  -v veridian-data:/var/lib/veridian \
  veridian:0.3 run --ledger /var/lib/veridian/ledger.json
```

The supplied [`Dockerfile`](../../Dockerfile) ships a multi-stage build
that already sets the recommended defaults: non-root user, `tini` as
PID 1 (SIGTERM propagates into the Python process), data dir on a
mountable volume, and JSON-formatted stdout for log aggregators.

## Configuration: every knob via `VERIDIAN_*`

Every field on `VeridianConfig` resolves from an environment variable
of the form `VERIDIAN_<FIELD_NAME_UPPER>`. Use the CLI flag for the few
overrides you want explicit; everything else can live in a ConfigMap.

| Env var | Type | Default | Notes |
| --- | --- | --- | --- |
| `VERIDIAN_DATA_DIR` | path | PWD | Container-friendly state root. Mounts here. |
| `VERIDIAN_MODEL` | str | `gemini/gemini-2.5-flash` | Provider/model string. Allowlist applies (see [Security](#security)). |
| `VERIDIAN_MAX_PARALLEL` | int | `1` | `ParallelRunner` semaphore bound. |
| `VERIDIAN_MAX_COST_USD` | float | `50.0` | Hard cost cap per run. |
| `VERIDIAN_MAX_RETRIES` | int | `3` | Per-task retry budget. |
| `VERIDIAN_MAX_TURNS_PER_TASK` | int | `10` | Worker agent loop limit. |
| `VERIDIAN_PROVIDER_TIMEOUT` | int | `120` | Per-LLM-call timeout in seconds. |
| `VERIDIAN_MAX_TOKENS` | int | `4096` | Per-call token cap. |
| `VERIDIAN_LEDGER_FILE` | path | `${DATA_DIR}/ledger.json` | JSON ledger location. |
| `VERIDIAN_PROGRESS_FILE` | path | `${DATA_DIR}/progress.md` | Human-readable progress log. |
| `VERIDIAN_LEDGER_LOCK_TIMEOUT` | float | `15.0` | FileLock acquire timeout — keep tight so stale locks surface. |
| `VERIDIAN_TRACE_FILE` | str\|none | unset | JSONL trace output path. `"none"` clears. |
| `VERIDIAN_DASHBOARD_PORT` | int | `7474` | Dashboard listen port. |
| `VERIDIAN_LOG_LEVEL` | str | `INFO` | `DEBUG`/`INFO`/`WARN`/`ERROR`. |
| `VERIDIAN_LOG_FORMAT` | str | `text` | `json` enables `JsonLogFormatter`. |
| `VERIDIAN_ALLOWED_MODELS` | str | (built-in list) | Comma-separated prefix allowlist. `*` disables. |
| `VERIDIAN_OTLP_ALLOW_HTTP` | `1`/unset | unset | Opt into plain-HTTP OTLP to a non-loopback host. |
| `VERIDIAN_HTTP_ALLOW_PRIVATE` | `1`/unset | unset | Opt into private-IP targets in `HttpStatusVerifier`. |
| `VERIDIAN_LEDGER_INDENT` | `1`/unset | unset | Pretty-print `ledger.json` (slower; debug only). |
| `VERIDIAN_DRY_RUN` | bool | `false` | Assemble context but never call the LLM. |
| `VERIDIAN_STRICT_REPLAY` | bool | `true` | Fail-closed on replay snapshot mismatch. |
| `VERIDIAN_ACTIVITY_JOURNAL_ENABLED` | bool | `true` | Side-effect boundary for retries. |
| `VERIDIAN_RESUME_PAUSED_ON_START` | bool | `true` | Resume PAUSED tasks before fetching new PENDING work. |
| `VERIDIAN_SKILL_LIBRARY_PATH` | str\|none | unset | Skill library persistence path. |
| `VERIDIAN_DRIFT_HISTORY_FILE` | str\|none | unset | Drift detection history. |
| `VERIDIAN_IDENTITY_GUARD_ENABLED` | bool | `true` | Enable identity hook. |
| … | | | All other dataclass fields follow the same `VERIDIAN_<NAME_UPPER>` pattern. |

Booleans accept `1/0`, `true/false`, `yes/no`, `on/off` case-insensitively.
Optional fields accept `none` / `null` / empty string to clear.

Invalid values fail at config construction with the offending env key in
the error message, e.g.:

```
VeridianConfigError: Env var VERIDIAN_MAX_PARALLEL='-1' …
VeridianConfig.max_parallel must be > 0, got -1
```

## Security

Defaults are restrictive. Override only when you understand the trade-off.

| Surface | Default | Override |
| --- | --- | --- |
| Subprocess env | scrubbed to `DEFAULT_ENV_ALLOWLIST` | `TrustedExecutor(inherit_env=True)` or pass `env_allowlist=` |
| LLM model URL | provider-prefix allowlist | `VERIDIAN_ALLOWED_MODELS=*` or add a prefix to the list |
| OTLP HTTP | loopback only | `allow_http=True` / `VERIDIAN_OTLP_ALLOW_HTTP=1` |
| `BashExitCodeVerifier` command | `DEFAULT_BLOCKLIST` checked at construction | `blocklist=[]` (or a custom list) |
| `HttpStatusVerifier` URL | loopback / private / link-local blocked | `allow_private_targets=True` / `VERIDIAN_HTTP_ALLOW_PRIVATE=1` |
| Report paths (drift, evolution, fingerprint) | anchored to `default_data_dir()` | use `safe_report_path(user_path, default_dir=…)` |

See [`guides/production/threat-model.md`](threat-model.md) and
[`guides/production/trusted-executor.md`](trusted-executor.md) for the
attack-vector mapping behind these defaults.

## Kubernetes wiring

A minimal `Deployment` + `Service` skeleton:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: veridian
spec:
  replicas: 1
  selector:
    matchLabels: { app: veridian }
  template:
    metadata:
      labels: { app: veridian }
    spec:
      terminationGracePeriodSeconds: 60   # let the SIGTERM drain finish
      containers:
        - name: veridian
          image: veridian:0.3
          args: ["run", "--ledger", "/var/lib/veridian/ledger.json"]
          env:
            - name: VERIDIAN_DATA_DIR
              value: /var/lib/veridian
            - name: VERIDIAN_LOG_FORMAT
              value: json
            - name: VERIDIAN_MAX_PARALLEL
              value: "4"
            - name: VERIDIAN_MAX_COST_USD
              value: "25"
            - name: VERIDIAN_OTLP_ENDPOINT
              value: https://otel-collector.observability.svc:4318/v1/traces
          envFrom:
            - secretRef:
                name: veridian-llm-credentials    # OPENAI_API_KEY etc. live here
          volumeMounts:
            - name: state
              mountPath: /var/lib/veridian
          ports:
            - name: dashboard
              containerPort: 7474
          readinessProbe:
            httpGet: { path: /ready, port: dashboard }
            initialDelaySeconds: 2
            periodSeconds: 5
          livenessProbe:
            httpGet: { path: /health, port: dashboard }
            initialDelaySeconds: 10
            periodSeconds: 10
      volumes:
        - name: state
          persistentVolumeClaim: { claimName: veridian-data }
```

Important details:

* **`terminationGracePeriodSeconds: 60`** lets `ParallelRunner` finish
  the in-flight batch on SIGTERM. The drain handler returns once the
  current batch completes; the runner then exits cleanly.
* **`readinessProbe: /ready`** returns 503 until the verifier registry
  has loaded built-ins *and* the ledger file is reachable. Pods stay
  out of rotation until both are true.
* **`livenessProbe: /health`** is the shallow "process is up" probe.
  Keep this lighter than `/ready` so a transient ledger-lock issue
  does not restart the pod.
* **`envFrom: secretRef`** keeps API keys out of the manifest. The
  child-process env scrub means agent-issued bash commands cannot leak
  these to the shell.

## Observability

* **Logs**: set `VERIDIAN_LOG_FORMAT=json` so every log record is a
  single JSON object with `ts`, `level`, `logger`, `message`, plus the
  structured `run_id` / `task_id` extras the runner stamps. Pair with
  Loki / Datadog / ELK by tailing container stdout.
* **Metrics**: `GET /metrics` on the dashboard returns an
  OpenMetrics-format exposition with:
  - `veridian_runs_total{phase}` (counter)
  - `veridian_tasks_done_total{phase}` / `_failed_total` / `_abandoned_total`
  - `veridian_run_duration_seconds{phase}` (histogram, 5 ms → 60 s buckets)

  Point a Prometheus scrape at the dashboard service:

  ```yaml
  apiVersion: monitoring.coreos.com/v1
  kind: ServiceMonitor
  metadata: { name: veridian }
  spec:
    selector: { matchLabels: { app: veridian } }
    endpoints:
      - port: dashboard
        path: /metrics
        interval: 15s
  ```
* **Traces**: configure the OTLP exporter via
  `configure_otlp_tracer(OTLPConfig(endpoint=…))`. Spans carry
  verifier ids, pass/fail outcomes, and provenance hashes — the
  endpoint allowlist (HTTPS or loopback HTTP) prevents accidental
  exfiltration.

## Failure modes & recovery

| Failure | What you'll see | Action |
| --- | --- | --- |
| Crashed peer left ledger lock behind | `Timeout: lock 'ledger.json.lock' could not be acquired within 15 s` | Confirm no other Veridian process is alive, then `rm /var/lib/veridian/ledger.json.lock`. |
| Corrupted ledger JSON | `LedgerCorrupted: ledger.json is malformed: …` | Restore from a backup, or `mv ledger.json ledger.json.corrupt && veridian init`. |
| PRM backend outage | `runner.prm_error … runner.prm_circuit_probe_attempt` after 60 s cooldown | No action — the circuit breaker probes after the cooldown; success closes it automatically. |
| Provider rate-limited | `ProviderRateLimited` retried with backoff; circuit may open after 5 failures | Investigate quota; circuit recovers on the LiteLLM provider's own cooldown. |
| Dashboard `/ready` stays 503 | `{"not_ready": [{"check": "ledger", "reason": "missing: …"}]}` | Mount the PVC, check the path matches `VERIDIAN_LEDGER_FILE`. |
| OTLP endpoint rejected at startup | `OTLP endpoint 'http://collector.example' uses plain HTTP to a non-loopback host` | Switch to HTTPS, or set `VERIDIAN_OTLP_ALLOW_HTTP=1` for the rare cases that need it (e.g. service-mesh-encrypted transport). |
| Model string rejected | `model='https://attacker.example' looks like a URL; refusing to use it` | Use a provider/model identifier; add a prefix to `VERIDIAN_ALLOWED_MODELS` if intentional. |

## SIGTERM drain contract

Both `VeridianRunner.run()` (sync) and `ParallelRunner.run_async()`
honour the SIGTERM/SIGINT drain contract:

1. Signal sets an internal `_shutdown` flag.
2. The current task (sync) or current batch (async) finishes.
3. Hooks fire `after_run` / `RunCompleted` with the partial summary.
4. The runner restores the previous SIGINT handler (no host-process
   side effects) and returns.

Pair this with a `terminationGracePeriodSeconds` larger than your worst
single-task duration so the orchestrator doesn't kill the pod mid-task.

## Related runbooks

* [Verification contract](verification-contract.md)
* [Threat model](threat-model.md)
* [TrustedExecutor](trusted-executor.md)
* [Drift detection](drift-guide.md)
* [Skill library](skill-guide.md)
* [Subsystem status](../subsystem-status.md)
