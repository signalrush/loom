"""Autoresearch: autonomous experiment loop using loom.

The model writes this program. loom-run executes it.
Each step() is a turn in the model's own session — context accumulates.

Usage:
    loom-run autoresearch.py
"""


async def main(step):
    # Get baseline
    baseline = await step(
        "Run `python train.py > run.log 2>&1`, then `grep '^val_bpb:' run.log`. Report the val_bpb.",
        schema={"val_bpb": "float"},
    )
    best = baseline["val_bpb"]

    for i in range(20):
        result = await step(
            f"Experiment {i+1}: propose one change to improve val_bpb. "
            f"Current best is {best}. Edit train.py, git commit, run it, report results.",
            schema={"val_bpb": "float", "description": "str", "status": "str"},
        )

        if result["val_bpb"] < best:
            best = result["val_bpb"]
            await step(f"Good, val_bpb improved to {best}. Keep this change.")
        else:
            await step("This didn't improve. Revert: git reset --hard HEAD~1")

        if (i + 1) % 5 == 0:
            await step(
                f"Pause and reflect. Best val_bpb so far: {best}. "
                "Read results history. What patterns are working? "
                "What should we try next?"
            )
