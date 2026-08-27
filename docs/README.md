# Veridian documentation

Public documentation for the Veridian assurance runtime. Everything here
describes behaviour that exists in this checkout; where a capability is bounded,
the boundary is stated rather than omitted.

| Document | What it answers |
|---|---|
| [quickstart.md](quickstart.md) | How do I get from `pip install` to a verified receipt? |
| [threat-model.md](threat-model.md) | What is Veridian trusted to do, and what can it not protect against? |
| [proof-format.md](proof-format.md) | What exactly is in a proof bundle, and how does an independent party check it? |
| [mapping-eu-ai-act-article-12.md](mapping-eu-ai-act-article-12.md) | Which Article 12 record-keeping obligations do Veridian artifacts support — and which do they not? |
| [mapping-open-agent-passport.md](mapping-open-agent-passport.md) | How do Veridian's objects line up with the Open Agent Passport and AP2 vocabularies? |

Reference for individual APIs lives in the docstrings; `veridian.gate`,
`veridian.assurance` and `veridian.effects` each carry a module-level overview.

## What is public and what is not

The `docs/` tree is private by default and files are made public one at a time
in `.gitignore`. Planning notes, research material and drafts stay private.
That split is deliberate: documentation a user needs in order to evaluate
whether to trust the library should be public, and the working notes behind it
need not be.

## Reading order

If you are evaluating Veridian, read [threat-model.md](threat-model.md) first.
It is the shortest honest answer to "should I depend on this?" — including the
cases where the answer is no.
