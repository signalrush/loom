"""Simple test program using the v2 Auto API."""

async def main(auto):
    answer = await auto.remind("What is 2 + 2? Reply with just the number.")
    print(f"Claude said: {answer}")

    answer2 = await auto.remind(f"You said {answer}. Now what is 10 * 10? Reply with just the number.")
    print(f"Claude said: {answer2}")

    print("Program complete!")
