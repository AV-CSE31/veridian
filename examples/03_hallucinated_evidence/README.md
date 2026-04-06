# Problem 3: Hallucinated Legal Evidence

## The Incident

**Mata v. Avianca, Inc.** (S.D.N.Y., June 22, 2023)

Roberto Mata sued Avianca Airlines for a knee injury from a serving cart. His attorneys used ChatGPT for legal research. ChatGPT fabricated **6 case citations** — complete with fake judges, courts, and rulings. When opposing counsel flagged the missing cases, the attorneys asked ChatGPT to confirm. It assured them the cases **"indeed exist"** and **"can be found in LexisNexis and Westlaw."**

Judge P. Kevin Castel: the fabricated analysis was **"gibberish."** $5,000 sanction. Letters of apology to every judge whose name appeared on fake opinions. Leading case on AI misuse in legal pleadings — cited in 100+ subsequent rulings.

**Key insight:** The sanctionable act wasn't using ChatGPT. It was continuing to defend fake citations after being questioned. A verification pipeline would have caught all 6 fabrications before the brief was ever filed.

Sources: [Wikipedia](https://en.wikipedia.org/wiki/Mata_v._Avianca,_Inc.) | [ACC](https://www.acc.com/resource-library/practical-lessons-attorney-ai-missteps-mata-v-avianca) | [Seyfarth Shaw](https://www.seyfarth.com/news-insights/update-on-the-chatgpt-case-counsel-who-submitted-fake-cases-are-sanctioned.html)

## Architecture

```
                    ┌─────────────────────┐
                    │  AI-Generated Brief  │
                    └──────────┬──────────┘
                               │
                ┌──────────────▼──────────────┐
                │  LAYER 1: EXTRACTION        │
                │  eyecite (Free Law Project)  │
                │  Bluebook-aware parser       │
                │  Hundreds of reporter formats│
                │  Volume / Reporter / Page    │
                │  + party name extraction     │
                └──────────────┬──────────────┘
                               │
                ┌──────────────▼──────────────┐
                │  LAYER 2: RESOLUTION        │
                │                             │
                │  CourtListener API (live)    │
                │  or Local Corpus (offline)   │
                │                             │
                │  Step 1: Does citation       │
                │          address exist?      │
                │  Step 2: Do party names      │
                │          match the real case?│
                │  Step 3: Fuzzy matching for  │
                │          abbreviation diffs  │
                └──────────────┬──────────────┘
                               │
                ┌──────────────▼──────────────┐
                │  LAYER 3: VERIDIAN VERIFIER │
                │  CitationPipelineVerifier    │
                │  (extends BaseVerifier)      │
                │                             │
                │  passed=True  → continue     │
                │  passed=False → block + error│
                └──────────────┬──────────────┘
                               │
                ┌──────────────▼──────────────┐
                │  LAYER 4: AUDIT REPORT      │
                │  Per-citation evidence       │
                │  CourtListener URLs          │
                │  Similarity scores           │
                │  PASS / FAIL verdict         │
                └─────────────────────────────┘
```

## What This Catches

| Attack Pattern | Detection Method |
|---------------|-----------------|
| Completely fabricated citation (fake vol/reporter/page) | Citation address lookup returns 0 results |
| Real citation + fake party names (Mata v. Avianca pattern) | Party name fuzzy match against actual case name |
| Abbreviated name mismatch ("N.F.I.B." vs "Nat'l Fed'n") | SequenceMatcher similarity scoring |
| Mixed real + fake in same paragraph | Per-citation independent verification |

## File Structure

```
03_hallucinated_evidence/
├── pipeline.py                    # Main entry point + Veridian BaseVerifier
├── extractors/
│   ├── citation_parser.py         # eyecite wrapper (with regex fallback)
│   └── models.py                  # Typed data models (Citation, Report)
├── resolvers/
│   ├── courtlistener.py           # CourtListener API client with caching
│   └── local_corpus.py            # Offline fallback (20 SCOTUS landmarks)
├── reporters/
│   └── audit_report.py            # Human-readable audit report generator
├── data/
│   └── sample_brief.txt           # Real-looking brief: 2 real + 4 fabricated
├── test_pipeline.py               # Failure-first integration tests
└── README.md
```

## Run

```bash
cd examples/03_hallucinated_evidence
pip install eyecite  # Optional but recommended — regex fallback otherwise

# Verify the sample brief (2 real + 4 fabricated citations):
python pipeline.py

# Verify any text file:
python pipeline.py path/to/your/brief.txt

# Run tests (offline, no API calls):
pytest test_pipeline.py -v
```

## Sample Output

```
===========================================================================
  CITATION VERIFICATION AUDIT REPORT
  Document: sample_brief.txt
===========================================================================

  SUMMARY
  Total citations found:    6
  Verified:                 2
  Hallucinated (citation):  4
  Hallucinated (name):      0
  VERDICT:                  FAIL — DO NOT FILE

  CITATION DETAIL
  [1] VERIFIED
      Citation:  347 U.S. 483
      Claimed:   Brown v. Board of Education
      Actual:    Brown v. Board of Education
      Reason:    Verified against local corpus (similarity=1.00)

  [2] !! HALLUCINATED
      Citation:  892 F.3d 1156
      Claimed:   Martinez v. GlobalCorp Airlines
      Reason:    Citation '892 F.3d 1156' not found in local corpus

  ...

  !! DOCUMENT CONTAINS FABRICATED CITATIONS — DO NOT FILE !!
```

## Production Integration

```python
# In your Veridian pipeline:
from pipeline import CitationPipelineVerifier

# Use as a verifier in VeridianRunner
task = Task(
    title="Draft legal brief",
    verifier_id="citation_pipeline",
    verifier_config={"mode": "both"},  # API + local fallback
)

# Or use the @verified decorator:
from veridian import verified

@verified(verifier="citation_pipeline", config={"mode": "api"})
def draft_brief(facts: str) -> dict:
    return llm.generate(f"Draft a legal brief about: {facts}")
```

## Extending for Production

| Layer | Demo | Production |
|-------|------|-----------|
| Extraction | eyecite (shipped) | eyecite + custom reporters |
| Resolution | 20 SCOTUS cases | CourtListener API + Redis cache |
| Name matching | SequenceMatcher | Sentence embeddings (all-MiniLM-L6-v2) |
| Reporting | Text audit report | HTML redlining + PDF export |
| Integration | CLI + BaseVerifier | REST API (FastAPI) + LangChain Tool |

The architecture is layered specifically so each component can be upgraded independently. Replace `local_corpus.py` with `westlaw.py` and the pipeline, verifier, and reporter layers don't change.
