#!/usr/bin/env python3
"""Compare PlayerNamePatternList with the inspected client translation.

This is a limited secondary oracle for the verified DBC grammar, not a general
PCRE implementation. The DBCs are read in place and are never copied.
"""

import argparse
import re
import struct
import subprocess
import sys
from pathlib import Path

EXPECTED = {
    "NamesProfanity.dbc": 1535,
    "NamesReserved.dbc": 2288,
    "ChatProfanity.dbc": 2289,
}

WORD = r"0-9A-Z_a-z\u00C0-\u00D6\u00D8-\u00DD\u00DF-\u00F6\u00F8-\u00FF"
BOUND = (
    r"(?:(?<=[" + WORD + r"])(?![" + WORD + r"])|"
    r"(?<![" + WORD + r"])(?=[" + WORD + r"]))"
)
SPACE = "\t\n\f\r \u00A0"

SYNTHETIC = [
    (r"\<bar\>", "Bar", True, "bar_start_end"),
    (r"\<bar\>", "Foo Bar", True, "bar_after_space"),
    (r"\<bar\>", "Foobar", False, "bar_inside_word"),
    (r"\<bar\>", "Barista", False, "bar_prefix"),
    (r"\<bar\>", "Ébar", False, "bar_after_latin1"),
    (r"\<bar\>", "barÉ", False, "bar_before_latin1"),
    (r"\<bar\>", "中bar", True, "bar_after_cjk"),
    (r"\<bar\>", "bar中", True, "bar_before_cjk"),
    (r"\<中\>", "中", False, "cjk_alone"),
    (r"\<中\>", "a中a", True, "cjk_between"),
    (r"aBc", "AbC", True, "ascii_case"),
    (r"Éclair", "éclair", False, "non_ascii_case"),
    (r"bar", "Foobar", True, "unanchored"),
    (r"\<\<bar\>\>", "Bar", True, "repeated_outer"),
    (r"a.*b", "axxxb", True, "dot"),
    (r"colou?r", "color", True, "question"),
    (r"\<a.*\>", "ab中", True, "backtrack_shorter"),
    (r"\<a.*b\>", "Ab Bx", True, "backtrack_later_word"),
    (r"\<^.*b\>", "中中Ab", False, "absolute_bol"),
    (r"\<a$", "ab", False, "absolute_eol"),
    (r"\<bar\>", "foobar bar", True, "later_start"),
    (r"\<a.*", "ab中", True, "leading_only"),
    (r"a.*\>", "ab中", True, "trailing_only"),
    (r"\<a*\>", "a", True, "zero_length"),
    (r"\<a*\>", "", False, "empty_subject"),
]


def ascii_lower(text):
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in text)


def translate(pattern):
    """Mirror the inspected client translation for the verified subset."""
    out = []
    i = 0
    in_class = False
    while i < len(pattern):
        c = pattern[i]
        if c != "\\":
            if c == "[" and not in_class:
                in_class = True
            elif c == "]":
                in_class = False
            out.append(c.lower() if "A" <= c <= "Z" else c)
            i += 1
            continue
        if i + 1 >= len(pattern):
            out.append("\\")
            break
        n = pattern[i + 1]
        if not in_class and n in "<>b":
            out.append(BOUND)
        elif n == "s":
            out.append(("[" + SPACE + "]") if not in_class else SPACE)
        else:
            out.append("\\" + n)
        i += 2
    return "".join(out)


def compile_oracle(pattern):
    return re.compile(translate(pattern))


def oracle_match(compiled, subject):
    return compiled.search(ascii_lower(subject)) is not None


def read_wdbc(path, expected):
    data = path.read_bytes()
    if len(data) < 20:
        raise ValueError(f"{path.name}: short WDBC header")
    magic, rows, fields, record_size, string_size = struct.unpack_from("<4sIIII", data, 0)
    if magic != b"WDBC":
        raise ValueError(f"{path.name}: not WDBC")
    if rows != expected:
        raise ValueError(f"{path.name}: rows {rows}, expected {expected}")
    if fields != 2 or record_size != 8:
        raise ValueError(f"{path.name}: unexpected fields/record size")
    string_start = 20 + rows * record_size
    if string_start + string_size > len(data):
        raise ValueError(f"{path.name}: string block out of bounds")

    result = []
    for i in range(rows):
        record_at = 20 + i * record_size
        rid, offset = struct.unpack_from("<II", data, record_at)
        if offset >= string_size:
            raise ValueError(f"{path.name}: string offset out of bounds at row {rid}")
        begin = string_start + offset
        end_limit = string_start + string_size
        end = data.find(b"\0", begin, end_limit)
        if end < 0:
            raise ValueError(f"{path.name}: unterminated string at row {rid}")
        pattern = data[begin:end].decode("utf-8", "strict")
        if not pattern:
            raise ValueError(f"{path.name}: empty pattern at row {rid}")
        result.append((rid, pattern))
    return result


def strip_outer(core):
    while core.startswith(r"\<"):
        core = core[2:]
    while core.endswith(r"\>"):
        core = core[:-2]
    return core


def witness(pattern):
    """One deterministic input character per supported core atom."""
    core = strip_outer(pattern)
    out = []
    i = 0
    while i < len(core):
        c = core[i]
        if c == "[":
            close = core.find("]", i + 1)
            if close < 0:
                break
            body = core[i + 1:close]
            if body and body[0] != "^":
                out.append(body[0])
            i = close + 1
            continue
        if c == "\\":
            i += 2
            continue
        if c in "^$*+?":
            i += 1
            continue
        if c == ".":
            out.append("x")
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def has_anchor(pattern):
    in_class = False
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif not in_class and c in "^$":
            return True
        i += 1
    return False


def hex_case(pattern, subject):
    return pattern.encode("utf-8").hex() + "\t" + subject.encode("utf-8").hex()


def fail(message):
    print("FAIL: " + message, file=sys.stderr)
    return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--dbc-dir", required=True)
    args = parser.parse_args()

    exe = Path(args.exe)
    if not exe.is_file():
        print(f"NOT RUN: executable not found: {exe}", file=sys.stderr)
        return 2

    dbc_dir = Path(args.dbc_dir)
    missing = [name for name in EXPECTED if not (dbc_dir / name).is_file()]
    if missing:
        print("NOT RUN: missing DBC(s): " + ", ".join(missing), file=sys.stderr)
        return 2

    rows = []
    try:
        for name, expected in EXPECTED.items():
            rows.extend((name, rid, pattern)
                        for rid, pattern in read_wdbc(dbc_dir / name, expected))
    except (OSError, ValueError, UnicodeDecodeError) as e:
        return fail(str(e))

    anchor_rows = sum(1 for _, _, pattern in rows if has_anchor(pattern))
    cases = []
    for pattern, subject, expected, label in SYNTHETIC:
        cases.append((label, pattern, subject, expected, None))
    for name, rid, pattern in rows:
        for subject in (
            witness(pattern),
            "A" + witness(pattern) + "A",
            " " + witness(pattern) + " ",
            "中" + witness(pattern) + "中",
            "abcxyz",
        ):
            cases.append((f"{name}:{rid}", pattern, subject, None, (name, rid)))

    try:
        batch_lines = [hex_case(pattern, subject) for _, pattern, subject, _, _ in cases]
    except UnicodeEncodeError as e:
        return fail(str(e))

    child = subprocess.Popen(
        [str(exe), "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    out, err = child.communicate("\n".join(batch_lines))
    if child.returncode != 0:
        return fail(f"C++ batch exited {child.returncode}: {err.strip()}")
    if err.strip():
        print(err.strip(), file=sys.stderr)

    output = out.splitlines()
    if len(output) != len(cases):
        return fail(f"C++ output lines {len(output)}, expected {len(cases)}")

    mismatches = 0
    compiled = {}
    for index, (label, pattern, subject, expected, row) in enumerate(cases):
        line = output[index].strip()
        if line not in ("0", "1"):
            return fail(f"malformed C++ output at case {index}: {line!r}")
        try:
            if pattern not in compiled:
                compiled[pattern] = compile_oracle(pattern)
            oracle = oracle_match(compiled[pattern], subject)
        except re.error as e:
            return fail(f"oracle compile failed for {label}: {e}")

        actual = line == "1"
        want = expected if expected is not None else oracle
        if actual != want:
            mismatches += 1
            where = f"{label} pattern={pattern!r} subject={subject!r}" if row is None \
                else f"{label} row={row} pattern={pattern!r} subject={subject!r}"
            print(f"MISMATCH {where} cpp={actual} oracle={oracle}", file=sys.stderr)

    print(f"ROWS {len(rows)}")
    print(f"ANCHOR_ROWS {anchor_rows}")
    print(f"CASES {len(cases)}")
    print(f"SKIPPED 0")
    print(f"MISMATCHES {mismatches}")
    return 0 if mismatches == 0 and anchor_rows == 11 else 1


if __name__ == "__main__":
    raise SystemExit(main())
