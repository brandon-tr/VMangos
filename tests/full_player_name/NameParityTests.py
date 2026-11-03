#!/usr/bin/env python3
"""Offline parity check between the real Rust client and C++ server naming code.

Usage (from the server repository root):
  python -B tests/full_player_name/BuildNameParityDriver.py --bootstrap
  python -B tests/full_player_name/NameParityTests.py \
      --cpp build/full-player-name/Release/name_parity_probe.exe

Both binaries consume the same line-oriented corpus and emit stable rows.  The
runner compares the complete output and additionally checks that C++ failed
preparations leave their input unchanged.
"""

import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Every name is valid UTF-8.  Byte-invalid UTF-8 is intentionally excluded:
# the Rust client API takes &str, so it cannot be a shared corpus case.
NAMES = [
    ("valid/mixed", "John Smith"),
    ("valid/upper", "JOHN SMITH"),
    ("valid/lower", "jOhN sMiTh"),
    ("valid/short", "Ab Cd"),
    ("valid/max12", "Abcdefghijkl Mnopqrstuvwx"),
    ("valid/same-parts", "John John"),
    ("valid/shared-component", "Smith Smith"),
    ("valid/latin1", "Zoë Ðaß"),
    ("valid/latin1-2", "Øyvind Êmile"),
    ("valid/latin1-3", "Émile Ñoño"),
    ("valid/eszett-internal", "Aß Bþ"),
    ("valid/thorn-y-internal", "Aþ Bÿ"),
    ("valid/eszett-initial", "ßeta Alpha"),
    ("valid/thorn-initial", "þorn Beta"),
    ("valid/y-initial", "ÿmir Gamma"),
    ("valid/latin12", "ÊÊÊÊÊÊÊÊÊÊÊÊ Aa"),
    ("valid/latin12-last", "Ab ÊÊÊÊÊÊÊÊÊÊÊÊ"),
    ("valid/hangul8", "가나다라마바사아 Abcdefgh"),
    ("valid/cjk6", "一二三四五六 七八九"),
    ("valid/mixed-script", "Abc 一"),
    ("valid/kokr-cross", "Abc 가나"),
    ("valid/latin-holes-boundary", "AÀ Bÿ"),
    ("invalid/latin13", "ÊÊÊÊÊÊÊÊÊÊÊÊÊ Aa"),
    ("invalid/hangul9", "가나다라마바사아자 Abcdefgh"),
    ("invalid/cjk7", "一二三四五六七 八"),
    ("invalid/empty", ""),
    ("invalid/space-only", " "),
    ("invalid/no-separator", "John"),
    ("invalid/leading-space", " John"),
    ("invalid/trailing-space", "John "),
    ("invalid/double-space", "John  Smith"),
    ("invalid/tab-separator", "John\tSmith"),
    ("invalid/newline-separator", "John\nSmith"),
    ("invalid/trailing-tab", "John\t"),
    ("invalid/short-first", "A B"),
    ("invalid/short-last", "Ab C"),
    ("invalid/multi-space", "Ab  Cd"),
    ("invalid/hyphen", "Ab-Cd"),
    ("invalid/underscore", "Ab_Cd"),
    ("invalid/digit", "Ab1 Cd"),
    ("invalid/leading-space-valid-parts", " Ab Cd"),
    ("invalid/trailing-space-valid-parts", "Ab Cd "),
    ("invalid/tab-inside", "Ab\tCd"),
    ("invalid/newline-inside", "Ab\nCd"),
    ("invalid/carriage-return", "Ab\rCd"),
    ("invalid/nbsp", "Ab\u00a0Cd"),
    ("invalid/em-space", "Ab\u2003Cd"),
    ("invalid/ideographic-space", "Ab\u3000Cd"),
    ("invalid/nul", "Ab\u0000Cd"),
    ("invalid/del", "Ab\u007fCd"),
    ("invalid/c1", "Ab\u0080Cd"),
    ("invalid/supplementary", "𐐀 Ab"),
    ("invalid/supplementary-pair", "𐐀𐐀 Ab"),
    ("invalid/latin-holes", "D×7 Ðe"),
    ("repetition/ascii-fold", "aAa Bb"),
    ("repetition/upper", "AAA Bbb"),
    ("repetition/latin-nonascii", "Ééé Aa"),
    ("restricted/name-first", "Badname Alpha"),
    ("restricted/chat-first", "Badchat Alpha"),
    ("restricted/reserved-first", "Badres Alpha"),
    ("restricted/name-last", "Alpha Badname"),
    ("restricted/chat-last", "Alpha Badchat"),
    ("restricted/reserved-last", "Alpha Badres"),
    ("restricted/joined-name", "Bad Name"),
    ("restricted/joined-chat", "Bad Chat"),
    ("restricted/joined-reserved", "Bad Res"),
    ("restricted/raw-lower", "badname Alpha"),
    ("restricted/two-spaces", "Bad Name Alpha"),
    ("restricted/no-space", "Badname"),
    ("restricted/first-before-short-last", "Badname A"),
    ("restricted/short-first-before-list", "A Badname"),
    ("restricted/double-space-before-list", "Badname  Alpha"),
    ("restricted/whole-spaced", "Alpha Bad Name"),
]

RESTRICTED_INDICES = tuple(
    index for index, (label, _) in enumerate(NAMES) if label.startswith("restricted/")
)

PROFILES = [
    (0, 0),   # enUS Latin
    (0, 1),   # koKR Hangul/ASCII
    (0, 4),   # zhCN CJK/Latin
    (1, 0),   # Region 1
    (2, 0),   # Region 2
    (3, 0),   # Region 3
    (4, 0),   # Region 4
    (5, 0),   # Region 5
    (0, 8),   # out-of-table locale falls back to row 0
]


def command_lines():
    pairs = [(region, locale) for region in range(6) for locale in range(9)]
    lines = [f"P {region} {locale}" for region, locale in pairs]
    expected = {}
    for index, (_, name) in enumerate(NAMES):
        for region, locale in PROFILES:
            key = f"n{index}_p{region}_{locale}_m0"
            lines.append(
                f"N\t{key}\t{region}\t{locale}\t0\t{name.encode('utf-8').hex()}"
            )
            expected[key] = ("N", 0)
    for index in RESTRICTED_INDICES:
        _, name = NAMES[index]
        for region, locale in ((0, 0), (0, 1), (0, 4)):
            for mask in (1, 2, 4, 7):
                key = f"n{index}_p{region}_{locale}_m{mask}"
                lines.append(
                    f"N\t{key}\t{region}\t{locale}\t{mask}\t{name.encode('utf-8').hex()}"
                )
                expected[key] = ("N", mask)
    return lines, pairs, expected


def run(binary, lines):
    completed = subprocess.run(
        [str(binary)],
        input="\n".join(lines) + "\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise SystemExit(f"{binary} failed ({completed.returncode}):\n{completed.stderr}")
    return completed.stdout.splitlines()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpp", required=True)
    parser.add_argument(
        "--rust",
        default=str(ROOT / "build" / "name-parity" / "cargo-target" /
                    "debug" / "name-parity-driver.exe"),
        help="driver built by BuildNameParityDriver.py")
    args = parser.parse_args()

    lines, pairs, _ = command_lines()
    cpp = run(args.cpp, lines)
    rust = run(args.rust, lines)
    if len(cpp) != len(lines) or len(rust) != len(lines):
        raise SystemExit(
            f"row count mismatch: commands={len(lines)} cpp={len(cpp)} rust={len(rust)}"
        )

    mismatches = []
    checked_pairs = 0
    checked_names = 0
    for command, cpp_row, rust_row in zip(lines, cpp, rust):
        if cpp_row != rust_row:
            mismatches.append((command, cpp_row, rust_row))
            continue
        fields = cpp_row.split("\t")
        if fields[0] == "P":
            checked_pairs += 1
        else:
            checked_names += 1
            status = int(fields[3])
            unchanged = fields[4]
            if status != 0x52 and unchanged != "1":
                mismatches.append((command, cpp_row, "C++ changed a rejected input"))

    if mismatches:
        for command, cpp_row, rust_row in mismatches[:20]:
            print("COMMAND:", command)
            print("  C++:", cpp_row)
            print("  Rust:", rust_row)
        raise SystemExit(f"{len(mismatches)} parity mismatches")

    print(
        f"name-parity PASS: {checked_pairs} pairs, {checked_names} name cases, "
        f"{len(lines)} total rows"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
