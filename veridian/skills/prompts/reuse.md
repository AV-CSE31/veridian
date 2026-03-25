# Skill Reuse Prompt (InitializerAgent injection)

The following verified procedures have been used successfully on similar tasks in the past.
Each has a reliability score — the probability it produces a correct result based on
observed reuse outcomes (Bayesian Beta distribution).

## Verified Procedures

{skill_blocks}

## Instructions

1. **Prefer these procedures** over re-inventing solutions. If a procedure matches your
   current task, follow its steps as a starting point.
2. **Adapt, don't blindly copy.** Replace placeholders (`{input_file}`, `{pattern}`, etc.)
   with the actual values from your task.
3. **Verify as usual.** Following a procedure does not skip verification — the verifier
   still decides if the task is DONE.
4. **Report divergence.** If you deviate from a procedure, note why in your
   `<veridian:result>` summary so the skill can be updated.

## Skill Block Format

Each skill is presented as:

```
[SKILL: <name>] reliability=<score> domain=<domain>
Trigger: <when to apply>
Steps:
  1. <step description> [cmd: <bash command>]
  2. ...
```
