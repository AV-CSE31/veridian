# Problem 1: AI Code Deploys Without Verification

## The Incident

**Amazon, March 2026.** Two outages in four days:
- **March 2:** 6-hour disruption. 120,000 lost orders. 1.6 million website errors.
- **March 5:** 6-hour outage. 99% drop in US order volume. ~6.3 million lost orders.

Both traced to AI-assisted code changes deployed to production without proper approval. Amazon's internal Kiro agent autonomously modified configurations and pushed to production. An internal meeting on March 10 cited a "trend of incidents" with "high blast radius" and "Gen-AI assisted changes."

**Amazon's response:** Senior engineer sign-offs now required for ALL AI-assisted code from junior staff. A human gate.

**Industry data:** AI code creates 1.7x more bugs, 1.5-2x more security vulnerabilities. Incidents per PR increased 23.5% with AI assistance.

Sources: [Fortune](https://fortune.com/2026/03/18/ai-coding-risks-amazon-agents-enterprise/), [Tom's Hardware](https://www.tomshardware.com/tech-industry/artificial-intelligence/amazon-calls-engineers-to-address-issues-caused-by-use-of-ai-tools-report-claims-company-says-recent-incidents-had-high-blast-radius-and-were-allegedly-related-to-gen-ai-assisted-changes), [CodeRabbit](https://www.coderabbit.ai/blog/state-of-ai-vs-human-code-generation-report)

## Root Cause

```
AI agent generates code change
  -> No pre-deployment AST analysis
  -> No output schema validation
  -> Code deploys to production
  -> Erroneous behavior cascades
  -> 6-hour outage, millions of orders lost
```

Amazon's fix (human sign-off) is a process control. Veridian's fix (deterministic verification) is an engineering control. Process controls fail when humans are tired, distracted, or trusting. Engineering controls don't.

## Veridian's Two-Gate Pattern

```
Gate 1: ToolSafetyVerifier (AST)
  -> Parses code into abstract syntax tree
  -> Blocks: eval(), exec(), os.system(), dangerous imports
  -> Cannot be fooled by obfuscation (operates on AST, not text)

Gate 2: SchemaVerifier
  -> Validates output has required deployment fields
  -> e.g., "status" and "migration_complete" must be present
  -> Catches incomplete outputs that would cause silent failures
```

Both must pass. If either returns `passed=False`, the deployment is blocked.

## Run

```bash
cd examples/01_ai_code_deploy
python solution.py       # Full demo
pytest test_solution.py -v  # Tests
```
