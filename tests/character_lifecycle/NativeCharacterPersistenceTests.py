#!/usr/bin/env python3
"""Runs the opt-in native C++ checked/prepared pipeline against disposable MySQL."""

import argparse
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "character_lifecycle"))
from NativeSchemaFixture import MySql  # noqa: E402
from NativeTargetGuard import (CONTAINER, IMAGE, parse_published_endpoint,
                               validate_target)  # noqa: E402

DEFAULT_EXE = ROOT / "build" / "character-lifecycle-native" / "Release" / \
    "native_character_persistence_tests.exe"
EXECUTABLE = DEFAULT_EXE


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--exe", default=str(DEFAULT_EXE),
        help="native checked/prepared test executable (default: %(default)s)")
    return parser.parse_args(argv)


def docker_available():
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.stdout.find(CONTAINER) >= 0


def published_endpoint():
    image = subprocess.run(
        ["docker", "inspect", "-f", "{{.Config.Image}}", CONTAINER],
        text=True, capture_output=True, check=True).stdout.strip()
    port_output = subprocess.run(
        ["docker", "port", CONTAINER, "3306"], text=True,
        capture_output=True, check=True).stdout
    host, port = parse_published_endpoint(port_output)
    if image != IMAGE:
        raise RuntimeError(f"container image must be {IMAGE}")
    if (host, port) != ("127.0.0.1", "3306"):
        raise RuntimeError("published endpoint must be 127.0.0.1:3306")
    return host, port


def root_password():
    result = subprocess.run(
        ["docker", "exec", CONTAINER, "sh", "-c",
         'exec printenv MYSQL_ROOT_PASSWORD'],
        text=True, capture_output=True, check=True)
    return result.stdout.strip()


def drop_owned_schema(schema, schema_name):
    try:
        schema.drop()
    except BaseException as error:
        print(f"NATIVE_SCHEMA_CLEANUP_FAILED: {schema_name}: "
              f"{type(error).__name__}", flush=True)
        raise
    print(f"NATIVE_SCHEMA_DROPPED: {schema_name}", flush=True)


class NativeCharacterPersistenceTests(unittest.TestCase):
    def test_actual_checked_prepared_pipeline(self):
        if not docker_available():
            self.skipTest("NOT RUN: vmangos-mysql unavailable")
        executable = EXECUTABLE
        if not executable.exists():
            self.skipTest("NOT RUN: native executable not built: " + str(executable))

        schema = MySql()
        schema_name = schema.schema
        host, port = published_endpoint()
        password = root_password()
        error = validate_target(IMAGE, host, port, schema_name, "root", password)
        if error:
            self.fail("native target guard: " + error)
        print(f"NATIVE_SCHEMA_ATTEMPTED: {schema_name}", flush=True)
        schema.create()
        print(f"NATIVE_SCHEMA_CREATED: {schema_name}", flush=True)
        body_error = None
        try:
            env = os.environ.copy()
            env.update({
                "FN05H_MYSQL_HOST": host,
                "FN05H_MYSQL_PORT": port,
                "FN05H_MYSQL_USER": "root",
                "FN05H_MYSQL_PASSWORD": password,
                "FN05H_MYSQL_DATABASE": schema_name,
            })
            result = subprocess.run(
                [str(executable)], env=env, text=True,
                capture_output=True, timeout=180)
            if result.returncode == 2 or "NATIVE_MYSQL_UNAVAILABLE" in result.stderr:
                self.skipTest("NOT RUN: " + result.stderr.strip())
            self.assertEqual(result.returncode, 0,
                             result.stdout + result.stderr)
            self.assertIn("native checked/prepared pipeline: PASS", result.stdout)
            print("NATIVE_EXECUTABLE_OUTPUT: " + result.stdout.strip(), flush=True)
        except BaseException as error:
            body_error = error
            raise
        finally:
            try:
                drop_owned_schema(schema, schema_name)
            except BaseException:
                # Preserve an existing test/skip exception; otherwise surface
                # cleanup failure. The failure marker was printed first.
                if body_error is None or isinstance(body_error, unittest.SkipTest):
                    raise


def main():
    global EXECUTABLE
    args = parse_args()
    EXECUTABLE = Path(args.exe)
    unittest.main(argv=[sys.argv[0]], verbosity=2)


if __name__ == "__main__":
    main()
