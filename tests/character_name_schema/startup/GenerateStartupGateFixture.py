#!/usr/bin/env python3
"""Extract the reviewed Master startup methods into a test-only include.

The generated methods are the actual production bodies.  Only services,
configuration, logging and the two validators are replaced by fixtures.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
MASTER = ROOT / "src" / "mangosd" / "Master.cpp"


def extract(signature):
    text = MASTER.read_text(encoding="utf-8")
    start = text.index(signature)
    brace = text.index("{", start)
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[brace:index + 1]
    raise SystemExit("unterminated production method: " + signature)


def main(argv):
    if len(argv) != 2:
        raise SystemExit("usage: GenerateStartupGateFixture.py OUTPUT")
    output = pathlib.Path(argv[1])
    start_db = extract("bool Master::_StartDB()")
    clear = extract("void Master::clearOnlineAccounts()")
    if "CheckCharacterNameSchema" not in start_db or \
            "CheckCharacterNameOwnership" not in start_db:
        raise SystemExit("production startup gate is missing from _StartDB")
    output.write_text(
        "// Generated from src/mangosd/Master.cpp. Do not edit.\n"
        "int realmID = 0;\n"
        "bool _StartDB()\n" + start_db + "\n"
        "void clearOnlineAccounts()\n" + clear + "\n",
        encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
