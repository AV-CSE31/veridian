# Example 09: PRM Policy Repair/Block

This example shows how PRM policy can:

- request a repair attempt when reasoning quality is below threshold
- block when confidence/score stays below policy gates

It uses:

- `PRMVerifier` extension point (custom plugin class)
- `Task.metadata["prm"]` policy configuration
- runner-level checkpoint/policy wiring

## Run

```bash
python demo.py
```

Expected output:

- a task that repairs and finishes `DONE`
- a task that fails with policy action `block`
