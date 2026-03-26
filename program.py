"""Simple test program: ask Claude a question, print the answer, done."""

async def main(step):
    answer = await step("What is 2 + 2? Reply with just the number.")
    print(f"Claude said: {answer}")

    answer2 = await step(f"You said {answer}. Now what is 10 * 10? Reply with just the number.")
    print(f"Claude said: {answer2}")

    print("Program complete!")
