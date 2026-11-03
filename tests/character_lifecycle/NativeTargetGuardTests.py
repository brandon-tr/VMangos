"""No-connect tests for the native target guard."""

import os
import pathlib
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from NativeSchemaFixture import MySql
from NativeCharacterPersistenceTests import (DEFAULT_EXE, drop_owned_schema,
                                             parse_args)
from NativeTargetGuard import (
    CONTAINER,
    HOST,
    IMAGE,
    PORT,
    SCHEMA_RE,
    docker_target,
    parse_published_endpoint,
    validate_target,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXE = ROOT / "build" / "character-lifecycle-native" / "Release" / \
    "native_character_persistence_tests.exe"


class NativeTargetGuardTests(unittest.TestCase):
    def test_schema_boundary(self):
        accepted = "fn05h_v20_test_0123456789ab"
        self.assertTrue(SCHEMA_RE.fullmatch(accepted))
        for rejected in (
            "characters",
            "vmangos",
            "fn05h_v20_test_0123456789AB",
            "fn05h_v20_test_0123456789a",
            "fn05h_v20_test_0123456789abc",
            "fn05h_v20_test_0123456789ag",
            "xfn05h_v20_test_0123456789ab",
            "",
        ):
            self.assertIsNone(SCHEMA_RE.fullmatch(rejected), rejected)

    def test_target_validation(self):
        self.assertIsNone(validate_target(
            IMAGE, HOST, PORT, "fn05h_v20_test_abcdef012345", "root", "secret"))
        self.assertIn("image", validate_target(
            "mysql:5.7", HOST, PORT, "fn05h_v20_test_abcdef012345", "root", "secret"))
        self.assertIn("endpoint", validate_target(
            IMAGE, "0.0.0.0", PORT, "fn05h_v20_test_abcdef012345", "root", "secret"))
        self.assertIn("endpoint", validate_target(
            IMAGE, HOST, "3307", "fn05h_v20_test_abcdef012345", "root", "secret"))
        self.assertIn("schema", validate_target(
            IMAGE, HOST, PORT, "characters", "root", "secret"))
        self.assertIn("user", validate_target(
            IMAGE, HOST, PORT, "fn05h_v20_test_abcdef012345", "", "secret"))
        self.assertIn("password", validate_target(
            IMAGE, HOST, PORT, "fn05h_v20_test_abcdef012345", "root", ""))

    def test_published_endpoint_parser(self):
        self.assertEqual(parse_published_endpoint("127.0.0.1:3306\n"),
                         ("127.0.0.1", "3306"))
        for output in ("", "0.0.0.0:3306", "[::]:3306",
                       "127.0.0.1:3306\n127.0.0.1:3307", "127.0.0.1"):
            with self.assertRaises(ValueError, msg=output):
                parse_published_endpoint(output)

    def test_docker_target_is_query_only(self):
        inspect = mock.Mock(stdout="mysql:5.6\n", returncode=0)
        port = mock.Mock(stdout="127.0.0.1:3306\n", returncode=0)
        with mock.patch("NativeTargetGuard.subprocess.run",
                        side_effect=[inspect, port]) as run:
            self.assertEqual(docker_target(), ("127.0.0.1", "3306"))
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0],
                         ["docker", "inspect", "-f", "{{.Config.Image}}", CONTAINER])
        self.assertEqual(run.call_args_list[1].args[0],
                         ["docker", "port", CONTAINER, "3306"])

    def test_schema_create_use_drop_lifecycle(self):
        schema = MySql()
        calls = []

        def run(sql, database=None):
            calls.append((sql, database))
            return ""

        schema._run = mock.Mock(side_effect=run)
        schema.create()
        self.assertTrue(schema.created)
        self.assertEqual(schema.owned_schema, schema.schema)
        self.assertIn("CREATE DATABASE", calls[0][0])
        self.assertIsNone(calls[0][1])
        self.assertIn("CREATE TABLE characters", calls[1][0])
        self.assertEqual(calls[1][1], schema.schema)

        self.assertEqual(schema.sql("SELECT 1"), "")
        self.assertEqual(calls[-1][1], schema.schema)
        self.assertIn("SELECT 1", calls[-1][0])
        schema.one("SELECT 2")
        self.assertEqual(calls[-1][1], schema.schema)
        schema.drop()
        self.assertFalse(schema.created)
        self.assertIsNone(calls[-1][1])
        self.assertIn(f"DROP DATABASE IF EXISTS `{schema.schema}`", calls[-1][0])
        before = len(calls)
        schema.drop()
        self.assertEqual(len(calls), before, "repeated cleanup is a no-op")

    def test_create_collision_never_adopts_or_cleans_existing(self):
        schema = MySql()
        schema._run = mock.Mock(side_effect=RuntimeError("collision"))
        with self.assertRaises(RuntimeError):
            schema.create()
        self.assertFalse(schema.created)
        self.assertIsNone(schema.owned_schema)
        self.assertEqual(schema._run.call_count, 1)
        schema.drop()
        self.assertEqual(schema._run.call_count, 1, "collision is not dropped")

    def test_schema_substitution_rejected_before_sql(self):
        schema = MySql()
        schema._run = mock.Mock(return_value="")
        schema.create()
        before = schema._run.call_count
        schema.schema = "fn05h_v20_test_abcdef012345"
        with self.assertRaises(ValueError):
            schema.sql("SELECT 1")
        with self.assertRaises(ValueError):
            schema.one("SELECT 2")
        with self.assertRaises(ValueError):
            schema.drop()
        self.assertEqual(schema._run.call_count, before,
                         "substituted target never reaches a subprocess")

        schema.schema = "characters"
        with self.assertRaises(ValueError):
            schema.sql("SELECT 1")
        schema.schema = "fn05h_v20_test_nothex00000"
        with self.assertRaises(ValueError):
            schema.one("SELECT 1")

    def test_partial_setup_drops_only_owned_schema(self):
        schema = MySql()
        candidate = schema.schema
        calls = []

        def run(sql, database=None):
            calls.append((sql, database))
            if "CREATE TABLE characters" in sql:
                raise RuntimeError("ddl failed")
            return ""

        schema._run = mock.Mock(side_effect=run)
        with self.assertRaises(RuntimeError):
            schema.create()
        self.assertFalse(schema.created)
        self.assertIsNone(schema.owned_schema)
        drops = [(sql, db) for sql, db in calls if "DROP DATABASE" in sql]
        self.assertEqual(len(drops), 1)
        self.assertIsNone(drops[0][1])
        self.assertIn(f"DROP DATABASE IF EXISTS `{candidate}`", drops[0][0])

    def test_sql_before_creation_rejected_without_calls(self):
        schema = MySql()
        schema._run = mock.Mock(return_value="")
        with self.assertRaises(RuntimeError):
            schema.sql("SELECT 1")
        with self.assertRaises(RuntimeError):
            schema.one("SELECT 1")
        self.assertEqual(schema._run.call_count, 0)

    def test_runner_validates_target_before_creation(self):
        runner = (ROOT / "tests" / "character_lifecycle" /
                  "NativeCharacterPersistenceTests.py").read_text(encoding="utf-8")
        create_at = runner.index("schema.create()")
        validate_at = runner.index("validate_target(IMAGE, host, port, schema_name")
        self.assertLess(validate_at, create_at)
        self.assertIn("published_endpoint()", runner)

        lifecycle = (ROOT / "tests" / "character_lifecycle" /
                     "NativeLifecycleTests.py").read_text(encoding="utf-8")
        guard_at = lifecycle.index("docker_target()")
        create_at = lifecycle.index("cls.db.create()")
        self.assertLess(guard_at, create_at)

    def test_native_cli_parses_exe_option(self):
        self.assertEqual(parse_args(["--exe", "custom.exe"]).exe, "custom.exe")
        self.assertEqual(parse_args([]).exe, str(DEFAULT_EXE))
        with self.assertRaises(SystemExit):
            parse_args(["custom.exe"])

    def test_cleanup_failure_has_no_success_marker(self):
        schema = mock.Mock()
        schema.drop.side_effect = RuntimeError("cleanup failed")
        with mock.patch("builtins.print") as printed:
            with self.assertRaises(RuntimeError):
                drop_owned_schema(schema, "fn05h_v20_test_000000000001")
        lines = [call.args[0] for call in printed.call_args_list if call.args]
        self.assertTrue(any("NATIVE_SCHEMA_CLEANUP_FAILED" in line
                            for line in lines))
        self.assertFalse(any("NATIVE_SCHEMA_DROPPED" in line for line in lines))

    def test_cleanup_success_marker_after_drop(self):
        schema = mock.Mock()
        with mock.patch("builtins.print") as printed:
            drop_owned_schema(schema, "fn05h_v20_test_000000000001")
        lines = [call.args[0] for call in printed.call_args_list if call.args]
        self.assertTrue(any("NATIVE_SCHEMA_DROPPED" in line for line in lines))
        self.assertFalse(any("NATIVE_SCHEMA_CLEANUP_FAILED" in line
                             for line in lines))

    def test_cpp_no_connect_self_test(self):
        if not EXE.exists():
            self.skipTest("NOT BUILT: native guard executable")
        run = subprocess.run([str(EXE), "--guard-self-test"],
                             text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("native target guard: PASS", run.stdout)
        self.assertNotIn("NATIVE_MYSQL_UNAVAILABLE", run.stderr)

    def test_cpp_rejects_invalid_target_before_connect(self):
        if not EXE.exists():
            self.skipTest("NOT BUILT: native guard executable")
        env = os.environ.copy()
        env.update({
            "FN05H_MYSQL_HOST": "localhost",
            "FN05H_MYSQL_PORT": "3307",
            "FN05H_MYSQL_USER": "root",
            "FN05H_MYSQL_PASSWORD": "not-a-real-secret",
            "FN05H_MYSQL_DATABASE": "characters",
        })
        run = subprocess.run([str(EXE)], env=env, text=True,
                             capture_output=True, timeout=30)
        self.assertEqual(run.returncode, 3, run.stdout + run.stderr)
        self.assertIn("NATIVE_TARGET_REJECTED", run.stderr)
        self.assertNotIn("NATIVE_MYSQL_UNAVAILABLE", run.stderr)
        self.assertNotIn("not-a-real-secret", run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
