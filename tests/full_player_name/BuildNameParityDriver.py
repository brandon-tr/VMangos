#!/usr/bin/env python3
"""Build the durable Rust name-parity driver offline.

The client workspace is read-only.  This writes the generated manifest, lock,
target and executable only under the server's gitignored build directory.
"""

import argparse
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_CLIENT = pathlib.Path(
    r"C:\Users\dev machine\Desktop\benilla-everwood_graphics-names")
DEFAULT_BUILD = ROOT / "build" / "name-parity"
TEMPLATE = pathlib.Path(__file__).resolve().parent / "NameParityDriver"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-root", default=str(DEFAULT_CLIENT))
    parser.add_argument("--build-dir", default=str(DEFAULT_BUILD))
    parser.add_argument("--bootstrap", action="store_true",
                        help="run cargo generate-lockfile before the locked build")
    args = parser.parse_args(argv)

    client = pathlib.Path(args.client_root)
    ui = client / "crates" / "benilla-ui"
    if not (ui / "Cargo.toml").exists():
        raise SystemExit(f"client benilla-ui crate not found: {ui}")
    build = pathlib.Path(args.build_dir)
    src = build / "src"
    src.mkdir(parents=True, exist_ok=True)
    manifest = (TEMPLATE / "Cargo.toml.template").read_text(encoding="utf-8")
    (build / "Cargo.toml").write_text(
        manifest.replace("@CLIENT_UI@", str(ui).replace("\\", "/")),
        encoding="utf-8")
    shutil.copyfile(TEMPLATE / "src" / "main.rs", src / "main.rs")

    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(build / "cargo-target")
    if args.bootstrap:
        subprocess.run(["cargo", "generate-lockfile", "--offline"],
                       cwd=build, env=env, check=True)
    subprocess.run(["cargo", "build", "--offline", "--locked"],
                   cwd=build, env=env, check=True)
    exe = build / "cargo-target" / "debug" / "name-parity-driver.exe"
    if not exe.exists():
        exe = build / "cargo-target" / "debug" / "name-parity-driver"
    print(exe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
