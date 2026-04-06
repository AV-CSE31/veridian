# Problem 4: Healthcare Misdiagnosis

## The Incident

**ECRI 2026 Patient Safety Report:** "Navigating the AI diagnostic dilemma" is the **#1 patient safety threat of 2026.**

Key findings:
- AI diagnostic tools **failed to recognize 66% of critical health conditions** in synthesized test cases
- Accuracy drops **precipitously** when prompts use conversational patient descriptions vs textbook descriptions
- Rural population underrepresentation: **23% higher false-negative rate** for pneumonia
- Melanoma detection errors more prevalent among dark-skinned patients due to dataset imbalance
- **68%** of healthcare organizations deploy agentic AI; Gartner predicts **40% will cancel** by 2027

**The cascade problem:** A single AI misdiagnosis triggers a chain of incorrect autonomous actions — wrong treatment ordered, wrong medication prescribed, wrong specialist referred. By the time a human catches it, the patient has already been harmed.

Sources: [Radiology Business](https://radiologybusiness.com/topics/artificial-intelligence/navigating-ai-diagnostic-dilemma-healthcares-no-1-patient-safety-concern-2026), [Frontiers in Medicine](https://www.frontiersin.org/journals/medicine/articles/10.3389/fmed.2025.1594450/full), [Fierce Healthcare](https://www.fiercehealthcare.com/providers/ai-fueled-misdiagnoses-rural-care-barriers-are-2026s-top-patient-safety-threats-ecri)

## Root Cause

```
Single LLM call generates one diagnosis
  -> System accepts it at face value
  -> No multi-sample agreement check
  -> No consensus mechanism
  -> Hallucinated or biased diagnosis enters the clinical workflow
  -> Downstream actions (treatment, prescriptions) based on wrong diagnosis
  -> Patient harm
```

## Veridian's Fix

`DiagnosticConsensusVerifier` — a real `BaseVerifier` extension that requires **N independent diagnostic samples** to agree above a threshold before any diagnosis is accepted.

- Default: 80% agreement on 5 samples (4/5 must agree)
- If consensus not reached → **ESCALATE to human clinician** (never silently pass)
- Case-insensitive matching (handles "Pneumonia" vs "pneumonia")
- Returns consensus diagnosis + agreement percentage in evidence

In production, each sample would come from an independent LLM call (different temperature, different prompt variation, or different model). The verifier only accepts when statistical agreement exceeds the threshold.

## Run

```bash
cd examples/04_healthcare_misdiagnosis
python solution.py
pytest test_solution.py -v
```

## What This Proves

When 5 independent diagnostic samples all disagree (the exact pattern that causes 66% misdiagnosis), Veridian blocks the output and escalates to a human clinician. No diagnosis reaches the patient without multi-sample consensus. The verification is deterministic — counting agreement percentages doesn't require an LLM call.
