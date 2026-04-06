# Problem 8: The Deleted Database — Enterprise Code Safety

## The Incidents

| Date | Tool | What Happened | Data Lost |
|------|------|--------------|-----------|
| Jul 2025 | Replit Agent | Deleted production DB during code freeze, created 4K fake users, lied about it | 1,200 records |
| Dec 2025 | Amazon Kiro | Autonomously deleted live production environment | 13-hour outage |
| 2025 | Claude Code | Terraform "destroy" wiped infrastructure + backups | 2.5 years |
| 2025 | Claude CLI | `rm -rf` on user's home directory | Family photos |
| Jan 2026 | Claude Cowork | "Organize desktop" → deleted 15 years of photos | 15 years |

Sources: [Fortune](https://fortune.com/2025/07/23/ai-coding-tool-replit-wiped-database-called-it-a-catastrophic-failure/) | [Tom's Hardware](https://www.tomshardware.com/tech-industry/artificial-intelligence/claude-code-deletes-developers-production-setup-including-its-database-and-snapshots-2-5-years-of-records-were-nuked-in-an-instant) | [AI Incident Database](https://incidentdatabase.ai/cite/1152/)

## Architecture

```
Agent-generated code
  |
  v
Layer 1: Veridian ToolSafetyVerifier (AST)
  |  Parse → Walk AST → Check calls/imports/attrs
  |  Catches: eval, exec, shutil, os.system, pickle, socket
  v
Layer 2: Threat Classifier
  |  Maps AST violations → threat categories + severities
  |  Links each finding to the real-world incident it prevents
  |  Finds shell commands in string literals (rm -rf, DROP TABLE)
  v
Layer 3: Security Report
     Per-finding detail with incident references
     CRITICAL/HIGH/MEDIUM/LOW classification
     Actionable for security review teams
```

## File Structure

```
08_deleted_databases/
├── pipeline.py                    # Main entry + Veridian BaseVerifier
├── analyzers/
│   ├── threat_classifier.py       # AST violations → classified threats
│   └── models.py                  # ThreatFinding, AnalysisReport, enums
├── reporters/
│   └── security_report.py         # Human-readable security report
├── data/
│   └── incident_samples.py        # Real incident code patterns
├── test_pipeline.py               # Parametrized tests against all incidents
└── README.md
```

## Run

```bash
cd examples/08_deleted_databases
python pipeline.py
python pipeline.py "import shutil; shutil.rmtree('/data')"
pytest test_pipeline.py -v
```
