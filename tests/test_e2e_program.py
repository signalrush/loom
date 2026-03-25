async def main(step):
    r1 = await step("Say exactly: STEP_ONE_DONE")
    print(f"Step 1: {r1}")
    r2 = await step("Say exactly: STEP_TWO_DONE")
    print(f"Step 2: {r2}")
    r3 = await step("What is 2+2?", schema={"answer": "int"})
    print(f"Step 3: {r3}")
