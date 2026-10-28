"""Export a seeded synthetic match as JSONL WsMessages: a dataset (rule 4.1 v) and a replay file.

uv run python -m watchparty.sim --seed 7 --out match.jsonl
"""

import argparse
import json

from watchparty.sim import DEMO_SEED, messages, simulate


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=DEMO_SEED)
    parser.add_argument("--out", required=True, help="JSONL file to write")
    args = parser.parse_args(argv)
    with open(args.out, "w") as f:
        for message in messages(simulate(args.seed)):
            f.write(json.dumps(message, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
