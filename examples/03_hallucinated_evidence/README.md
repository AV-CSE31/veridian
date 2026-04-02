# Problem 3: Hallucinated Legal Evidence

## The Incident

**Mata v. Avianca, Inc.** (S.D.N.Y. 2023) — the landmark case on AI misuse in legal pleadings.

Roberto Mata sued Avianca Airlines for personal injury. His attorneys — Peter LoDuca and Steven Schwartz of Levidow, Levidow & Oberman — used ChatGPT for legal research. ChatGPT fabricated **6 case citations** that never existed, complete with fake judges, fake courts, and fake rulings.

When opposing counsel couldn't find the cases, the attorneys asked ChatGPT to confirm. ChatGPT assured them the cases **"indeed exist"** and **"can be found in reputable legal databases such as LexisNexis and Westlaw."** The attorneys submitted this assurance to the court.

Judge P. Kevin Castel described the fabricated legal analysis as **"gibberish."** The attorneys were sanctioned with a $5,000 fine and ordered to write letters to every judge whose name appeared on the fake opinions.

**Key ruling:** Using ChatGPT wasn't the sanctionable act. Continuing to defend fake citations after being questioned was.

**Significance:** Leading case on AI misuse in legal pleadings. Cited in 100+ subsequent rulings.

Sources: [Wikipedia](https://en.wikipedia.org/wiki/Mata_v._Avianca,_Inc.), [ACC](https://www.acc.com/resource-library/practical-lessons-attorney-ai-missteps-mata-v-avianca), [Seyfarth Shaw](https://www.seyfarth.com/news-insights/update-on-the-chatgpt-case-counsel-who-submitted-fake-cases-are-sanctioned.html)

## Root Cause

```
LLM generates plausible case name ("Martinez v. GlobalCorp, 892 F.3d 1156")
  -> Attorney doesn't verify against legal database
  -> Citation submitted in court filing
  -> Opposing counsel can't find the case
  -> Court can't find the case
  -> Attorney asks ChatGPT — ChatGPT confirms it exists
  -> Attorney submits ChatGPT's confirmation to the court
  -> Sanctions imposed
```

The failure: no verification step between generation and submission.

## Veridian's Fix

`CitationGroundingVerifier` — a real `BaseVerifier` extension that:
1. Extracts "X v. Y" citation patterns from agent output via regex
2. Normalizes each citation (case-insensitive)
3. Checks against a known corpus (20 US Supreme Court cases in demo; LexisNexis/Westlaw in production)
4. Returns `passed=False` with the specific fabricated citations listed

This is deterministic text matching. The LLM cannot fabricate a citation that passes a corpus lookup.

## Run

```bash
cd examples/03_hallucinated_evidence
python solution.py
python solution.py "In Martinez v. GlobalCorp, 892 F.3d 1156, the court held..."
pytest test_solution.py -v
```

## What This Proves

The $5,000 sanction and the reputational damage to the attorneys in Mata v. Avianca would not have occurred if every AI-generated legal brief passed through a citation grounding check before submission. The verification takes < 1ms and requires zero LLM calls.
