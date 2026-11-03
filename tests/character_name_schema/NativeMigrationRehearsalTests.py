import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "character_name_schema"))
sys.path.insert(0, str(ROOT / "tests" / "character_lifecycle"))

import NativeMigrationRehearsal as rehearsal


class FakeSchema:
    def __init__(self):
        self.schema = "fn05h_v20_test_0123456789ab"
        self.created = False
        self.dropped = False

    def create(self):
        self.created = True

    def drop(self):
        self.dropped = True


class StatefulSchema:
    """Small orchestration fake; it does not simulate SQL execution."""

    def __init__(self):
        self.schema = "fn05h_v20_test_0123456789ab"
        self.calls = []
        self.history = set()
        self.marker1 = 0
        self.marker2 = 0
        self.chars = 0
        self.ownership_rows = 0
        self.ownership_table = False
        self.identity = False
        self.name_width = 12
        self.auction_engine = "MyISAM"
        self.missing_account_table = False
        self.sentinel_name = ""
        self.mig2_error = None

    def run_script(self, sql):
        self.calls.append(("script", sql))
        if sql == "LEGACY":
            self.name_width = 12
            self.auction_engine = "MyISAM"
        elif sql == "CURRENT":
            self.name_width = 25
        elif sql.startswith("H"):
            self.history.add(sql)
        elif sql == "MIG1":
            self.marker1 = 1
            self.name_width = 25
        elif sql == "MIG2":
            if self.marker1 == 0:
                raise RuntimeError("FN-05F requires migration 20261030222040")
            if len(self.history) != 8:
                raise RuntimeError(
                    "FN-05F requires all eight historical Character migrations")
            if self.chars:
                raise RuntimeError("FN-05F requires an empty characters table")
            if self.ownership_rows:
                raise RuntimeError("FN-05F ownership table must be empty")
            if self.mig2_error is not None:
                raise RuntimeError(self.mig2_error)
            self.auction_engine = "InnoDB"
            if self.missing_account_table:
                raise RuntimeError(
                    "ERROR 1146 (42S02): Table 'db.character_account_data' "
                    "doesn't exist")
            self.marker2 = 1
        else:
            raise AssertionError("unexpected script " + sql)

    def sql(self, sql):
        self.calls.append(("sql", sql))
        if "INSERT INTO `characters`" in sql:
            self.chars = 1
            self.sentinel_name = "Legacy Name"
        elif "CREATE TABLE `character_name_ownership`" in sql:
            self.ownership_table = True
        elif "INSERT INTO `character_name_ownership`" in sql:
            self.ownership_rows = 1
        elif "RENAME TABLE `character_account_data` TO" in sql:
            self.missing_account_table = True
        elif "RENAME TABLE `character_account_data_hold` TO" in sql:
            self.missing_account_table = False
        else:
            raise AssertionError("unexpected SQL " + sql)

    def one(self, sql):
        if "GROUP_CONCAT" in sql:
            ids = []
            if self.marker1:
                ids.append("20261030222040")
            if self.marker2:
                ids.append("20261031010000")
            return ",".join(ids)
        if "FROM `migrations`" in sql:
            return str(self.marker1 if "20261030222040" in sql else self.marker2)
        if "CHARACTER_MAXIMUM_LENGTH" in sql:
            return str(self.name_width)
        if "ENGINE FROM information_schema.TABLES" in sql:
            return self.auction_engine
        if "COLUMN_NAME='identity_generation'" in sql:
            return "1" if self.identity else "0"
        if "TABLE_NAME='character_name_ownership'" in sql and \
                "COUNT(*) FROM information_schema.TABLES" in sql:
            return "1" if self.ownership_table else "0"
        if "TABLE_NAME='character_account_data'" in sql and \
                "COUNT(*) FROM information_schema.TABLES" in sql:
            return "0" if self.missing_account_table else "1"
        if "COUNT(*) FROM `characters`" in sql:
            return str(self.chars)
        if "COUNT(*) FROM `character_name_ownership`" in sql:
            return str(self.ownership_rows)
        if "SELECT name FROM `characters`" in sql:
            return self.sentinel_name
        raise AssertionError("unexpected query " + sql)


class MetadataSchema:
    """Returns the production-shaped metadata values for contract tests."""

    def __init__(self, bad=""):
        self.bad = bad

    def one(self, sql):
        if "TABLE_NAME='account_data'" in sql and \
                "COUNT(*) FROM information_schema.TABLES" in sql:
            return "0" if self.bad == "account" else "1"
        if "TABLE_NAME='character_account_data'" in sql and \
                "COUNT(*) FROM information_schema.TABLES" in sql:
            return "1"
        if "TABLE_NAME='worldstates'" in sql or \
                "TABLE_NAME='character_duplicate_account'" in sql:
            if "COUNT(*) FROM information_schema.TABLES" in sql:
                return "1" if self.bad == "obsolete" else "0"
        if "ENGINE FROM information_schema.TABLES" in sql:
            return "InnoDB"
        if "COLUMN_NAME='instance'" in sql and "TABLE_NAME='characters'" in sql:
            return "0" if self.bad == "instance" else "1"
        if "COLUMN_NAME='instance_id'" in sql and "TABLE_NAME='characters'" in sql:
            return "0"
        if "COLUMN_NAME='character_flags'" in sql:
            return "0" if self.bad == "flags" else "1"
        if "COLUMN_NAME='player_flags'" in sql or \
                "COLUMN_NAME='at_login_flags'" in sql or \
                "COLUMN_NAME='is_logout_resting'" in sql:
            return "0"
        if "TABLE_NAME='character_stats'" in sql and "COLUMN_NAME=" in sql:
            return "1"
        if "COLUMN_NAME='account'" in sql and "TABLE_NAME='account_data'" in sql:
            return "1"
        if "COLUMN_NAME='guid'" in sql and \
                "TABLE_NAME='character_account_data'" in sql:
            return "1"
        if "INDEX_NAME='idx_instance'" in sql or \
                "INDEX_NAME='idx_playerGuid'" in sql:
            return "0" if self.bad == "index" else "1"
        if "TABLE_NAME IN" in sql:
            if self.bad == "tables":
                return "55"
            if self.bad == "engine" and "ENGINE='InnoDB'" in sql:
                return "55"
            return "56"
        if "TABLE_NAME='character_name_ownership'" in sql and \
                "COLUMN_NAME='canonical_name'" in sql and \
                "information_schema.COLUMNS" in sql:
            return "1" if self.bad == "columns" else "2"
        if "COLUMN_NAME='identity_generation'" in sql:
            return "0" if self.bad == "identity" else "1"
        if "CHARACTER_MAXIMUM_LENGTH" in sql:
            if "COLUMN_NAME IN" in sql:
                return "2" if self.bad == "naming" else "3"
            return "1"
        if "INDEX_NAME='PRIMARY'" in sql:
            return "0" if self.bad == "primary" else "1"
        if "uq_character_name_guid" in sql:
            return "0" if self.bad == "unique" else "1"
        if "KEY_COLUMN_USAGE" in sql:
            return "0" if self.bad == "fk" else "1"
        if "REFERENTIAL_CONSTRAINTS" in sql:
            return "0" if self.bad == "rules" else "1"
        return "0"


class NativeMigrationRehearsalCaseTests(unittest.TestCase):
    def _inputs(self):
        return {"current": {"sql": "CURRENT"}, "legacy": {"sql": "LEGACY"}}

    def _run(self, case):
        schema = StatefulSchema()
        def migration(number):
            if number == rehearsal.MIGRATION_1:
                return "MIG1"
            if number == rehearsal.MIGRATION_2:
                return "MIG2"
            return "H" + str(rehearsal.HISTORICAL_MIGRATIONS.index(number) + 1)
        with mock.patch.object(rehearsal, "load_inputs",
                               return_value=self._inputs()), \
                mock.patch.object(rehearsal, "migration_sql",
                                  side_effect=migration), \
                mock.patch.object(rehearsal, "assert_full_chain") as full:
            case(schema)
        return schema, full

    def test_fresh_orchestration(self):
        schema, full = self._run(rehearsal.run_fresh)
        scripts = [s for kind, s in schema.calls if kind == "script"]
        self.assertEqual(scripts[0], "CURRENT")
        self.assertEqual(len([s for s in scripts if s.startswith("H")]), 8)
        self.assertEqual(scripts[-2:], ["MIG1", "MIG2"])
        self.assertEqual(schema.marker2, 1)
        full.assert_called_once()

    def test_upgrade_checks_legacy_before_migrating(self):
        schema, full = self._run(rehearsal.run_upgrade)
        self.assertEqual(schema.calls[0], ("script", "LEGACY"))
        self.assertEqual(len(schema.history), 8)
        self.assertEqual(schema.name_width, 25)
        self.assertEqual(schema.marker2, 1)
        full.assert_called_once()

    def test_reapply_is_idempotent(self):
        schema, full = self._run(rehearsal.run_reapply)
        scripts = [s for kind, s in schema.calls if kind == "script"]
        self.assertEqual(scripts[:1], ["CURRENT"])
        self.assertEqual(scripts[-2:], ["MIG1", "MIG2"])
        self.assertEqual(len(scripts), 21)
        self.assertEqual(schema.marker2, 1)
        full.assert_called_once()

    def test_missing_width_prerequisite(self):
        schema, _ = self._run(rehearsal.run_missing_dependency)
        self.assertEqual(schema.marker1, 0)
        self.assertEqual(schema.marker2, 0)
        self.assertEqual(len(schema.history), 8)
        self.assertEqual(schema.auction_engine, "MyISAM")

    def test_missing_history_prerequisite(self):
        schema, _ = self._run(rehearsal.run_missing_history)
        self.assertEqual(len(schema.history), 7)
        self.assertEqual(schema.marker1, 1)
        self.assertEqual(schema.marker2, 0)
        self.assertEqual(schema.auction_engine, "MyISAM")

    def test_nonempty_characters_refusal(self):
        schema, _ = self._run(rehearsal.run_nonempty_characters)
        self.assertEqual(schema.chars, 1)
        self.assertEqual(len(schema.history), 8)
        self.assertEqual(schema.marker2, 0)
        self.assertEqual(schema.ownership_table, False)
        self.assertEqual(schema.identity, False)
        self.assertEqual(schema.auction_engine, "MyISAM")

    def test_nonempty_ownership_applies_full_chain_first(self):
        schema, _ = self._run(rehearsal.run_nonempty_ownership)
        scripts = [s for kind, s in schema.calls if kind == "script"]
        self.assertEqual(scripts[0], "LEGACY")
        self.assertEqual(len([s for s in scripts if s.startswith("H")]), 8)
        self.assertIn("MIG1", scripts)
        self.assertLess(scripts.index("MIG1"),
                        scripts.index("MIG2"))
        self.assertEqual(schema.marker1, 1)
        mig1_at = next(i for i, (kind, sql) in enumerate(schema.calls)
                       if kind == "script" and sql == "MIG1")
        setup_at = next(i for i, (kind, sql) in enumerate(schema.calls)
                        if kind == "sql" and
                        "CREATE TABLE `character_name_ownership`" in sql)
        self.assertLess(mig1_at, setup_at)
        self.assertEqual(schema.marker2, 0)
        self.assertEqual(schema.ownership_rows, 1)
        self.assertEqual(schema.identity, False)
        self.assertEqual(schema.auction_engine, "MyISAM")

    def test_partial_failure_retries_after_restore(self):
        schema, full = self._run(rehearsal.run_partial_failure)
        self.assertEqual(schema.marker2, 1)
        self.assertEqual(schema.auction_engine, "InnoDB")
        self.assertFalse(schema.missing_account_table)
        full.assert_called_once()

    def test_unexpected_partial_diagnostic_propagates(self):
        schema = StatefulSchema()
        schema.mig2_error = "ERROR 1064 (42000): syntax error"
        with mock.patch.object(rehearsal, "load_inputs",
                               return_value=self._inputs()), \
                mock.patch.object(rehearsal, "migration_sql",
                                  side_effect=lambda n: "MIG1"
                                  if n == rehearsal.MIGRATION_1 else "MIG2"
                                  if n == rehearsal.MIGRATION_2 else
                                  "H" + str(rehearsal.HISTORICAL_MIGRATIONS.index(n) + 1)):
            with self.assertRaises(AssertionError):
                rehearsal.run_partial_failure(schema)

    def test_historical_effects_contract(self):
        schema = MetadataSchema()
        rehearsal.assert_historical_effects(schema)
        for bad in ("account", "instance", "flags", "index", "obsolete"):
            with self.assertRaises(AssertionError, msg=bad):
                rehearsal.assert_historical_effects(MetadataSchema(bad))

    def test_final_contract_rejects_bad_metadata(self):
        rehearsal.assert_final_contract(MetadataSchema())
        for bad in ("tables", "engine", "naming", "columns", "identity",
                    "primary", "unique", "fk", "rules"):
            with self.assertRaises(AssertionError, msg=bad):
                rehearsal.assert_final_contract(MetadataSchema(bad))


    def test_default_refuses_without_target_calls(self):
        with mock.patch.object(rehearsal, "published_endpoint") as endpoint, \
                mock.patch.object(rehearsal, "root_password") as password, \
                mock.patch("builtins.print") as printed:
            code = rehearsal.main([])
        self.assertEqual(code, 2)
        endpoint.assert_not_called()
        password.assert_not_called()
        self.assertTrue(any("REFUSED" in str(call.args[0])
                            for call in printed.call_args_list if call.args))

    def test_help_does_not_connect(self):
        with mock.patch.object(rehearsal, "published_endpoint") as endpoint:
            with self.assertRaises(SystemExit) as raised:
                rehearsal.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        endpoint.assert_not_called()

    def test_target_validation_precedes_schema_create(self):
        fake = FakeSchema()
        with mock.patch.object(rehearsal, "MigrationSchema", return_value=fake), \
                mock.patch.object(rehearsal, "published_endpoint",
                                  return_value=("127.0.0.1", "3306")), \
                mock.patch.object(rehearsal, "root_password", return_value="secret"), \
                mock.patch.object(rehearsal, "validate_target",
                                  return_value="bad target"):
            with self.assertRaises(RuntimeError):
                rehearsal.run_case("fresh", lambda schema: None)
        self.assertFalse(fake.created)

    def test_collision_is_not_adopted_or_dropped(self):
        fake = FakeSchema()
        fake.create = mock.Mock(side_effect=RuntimeError("collision"))
        with mock.patch.object(rehearsal, "MigrationSchema", return_value=fake), \
                mock.patch.object(rehearsal, "published_endpoint",
                                  return_value=("127.0.0.1", "3306")), \
                mock.patch.object(rehearsal, "root_password", return_value="secret"), \
                mock.patch.object(rehearsal, "validate_target", return_value=""):
            with self.assertRaises(RuntimeError):
                rehearsal.run_case("fresh", lambda schema: None)
        self.assertFalse(fake.dropped)

    def test_owned_identity_rejects_substitution(self):
        schema = rehearsal.MigrationSchema()
        schema._run = mock.Mock(return_value="")
        schema.create()
        owned = schema.schema
        before = schema._run.call_count
        schema.schema = "fn05h_v20_test_ffffffffffff"
        with self.assertRaises(ValueError):
            schema.sql("SELECT 1")
        with self.assertRaises(ValueError):
            schema.drop()
        self.assertEqual(schema._run.call_count, before)
        schema.schema = owned
        schema.drop()
        self.assertFalse(schema.created)

    def test_cleanup_failure_has_no_success_marker(self):
        schema = FakeSchema()
        schema.drop = mock.Mock(side_effect=RuntimeError("drop failed"))
        with mock.patch("builtins.print") as printed:
            with self.assertRaises(RuntimeError):
                rehearsal.drop_owned_schema(schema, schema.schema)
        lines = [call.args[0] for call in printed.call_args_list if call.args]
        self.assertTrue(any("MIGRATION_SCHEMA_CLEANUP_FAILED" in line
                            for line in lines))
        self.assertFalse(any("MIGRATION_SCHEMA_DROPPED" in line
                             for line in lines))

    def test_cleanup_success_marker(self):
        schema = FakeSchema()
        with mock.patch("builtins.print") as printed:
            rehearsal.drop_owned_schema(schema, schema.schema)
        lines = [call.args[0] for call in printed.call_args_list if call.args]
        self.assertTrue(any("MIGRATION_SCHEMA_DROPPED" in line
                            for line in lines))
        self.assertFalse(any("MIGRATION_SCHEMA_CLEANUP_FAILED" in line
                             for line in lines))

    def test_case_failure_propagates_and_cleanup_runs(self):
        fake = FakeSchema()
        with mock.patch.object(rehearsal, "MigrationSchema", return_value=fake), \
                mock.patch.object(rehearsal, "published_endpoint",
                                  return_value=("127.0.0.1", "3306")), \
                mock.patch.object(rehearsal, "root_password", return_value="secret"), \
                mock.patch.object(rehearsal, "validate_target", return_value=""), \
                mock.patch("builtins.print") as printed:
            def fail(schema):
                raise AssertionError("intended failure")
            with self.assertRaises(AssertionError):
                rehearsal.run_case("fresh", fail)
        self.assertTrue(fake.dropped)
        self.assertFalse(any("MIGRATION_CASE_PASS" in str(call.args[0])
                             for call in printed.call_args_list if call.args))

    def test_cleanup_failure_surfaces_when_body_failed(self):
        fake = FakeSchema()
        fake.drop = mock.Mock(side_effect=RuntimeError("drop failed"))
        with mock.patch.object(rehearsal, "MigrationSchema", return_value=fake), \
                mock.patch.object(rehearsal, "published_endpoint",
                                  return_value=("127.0.0.1", "3306")), \
                mock.patch.object(rehearsal, "root_password", return_value="secret"), \
                mock.patch.object(rehearsal, "validate_target", return_value=""), \
                mock.patch("builtins.print"):
            with self.assertRaises(RuntimeError):
                rehearsal.run_case("fresh",
                                   lambda schema: (_ for _ in ()).throw(
                                       AssertionError("body failed")))
        self.assertTrue(fake.drop.called)

    def test_sql_guard_allows_actual_inputs_and_rejects_escapes(self):
        inputs = rehearsal.load_inputs()
        rehearsal.guard_sql(inputs["current"]["sql"], "fn05h_v20_test_0123456789ab")
        rehearsal.guard_sql(inputs["legacy"]["sql"], "fn05h_v20_test_0123456789ab")
        for path in rehearsal.MIGRATION_FILES:
            rehearsal.guard_sql(path.read_text(encoding="utf-8"),
                                "fn05h_v20_test_0123456789ab")
        for bad in (
            "USE other;",
            "CREATE DATABASE other;",
            "DROP DATABASE other;",
            "SELECT * FROM other.characters;",
            "ALTER TABLE other.characters ADD x INT;",
            "INSERT INTO other.characters VALUES(1);",
            "LOAD DATA INFILE 'x' INTO TABLE characters;",
            "SELECT * INTO OUTFILE 'x' FROM characters;",
            "SOURCE /tmp/x.sql",
        ):
            with self.assertRaises(ValueError, msg=bad):
                rehearsal.guard_sql(bad, "fn05h_v20_test_0123456789ab")

    def test_migration_delimiters_are_passed_whole(self):
        schema = rehearsal.MigrationSchema()
        schema._run = mock.Mock(return_value="")
        schema.create()
        schema.run_script(rehearsal.migration_sql(rehearsal.MIGRATION_1))
        passed = schema._run.call_args.args[0]
        self.assertIn("DELIMITER ??", passed)
        self.assertIn("CALL add_migration();", passed)
        self.assertIn("DELIMITER ;", passed)

    def test_input_provenance_and_hashes(self):
        inputs = rehearsal.load_inputs()
        self.assertEqual(inputs["current"]["provenance"], "working-tree")
        self.assertEqual(inputs["legacy"]["provenance"], "git-show")
        self.assertEqual(inputs["legacy"]["blob"], rehearsal.LEGACY_BLOB)
        self.assertIn("varchar(12)", inputs["legacy"]["sql"])
        self.assertIn("ENGINE=MyISAM", inputs["legacy"]["sql"])
        self.assertNotIn("identity_generation", inputs["legacy"]["sql"])
        self.assertEqual(len(inputs["current"]["sha256"]), 64)
        self.assertEqual(len(inputs["legacy"]["sha256"]), 64)

    def test_execute_dispatches_only_selected_case(self):
        with mock.patch.object(rehearsal, "run_case") as run_case, \
                mock.patch.object(rehearsal, "load_inputs",
                                  return_value={"current": {"sql": ""},
                                                "legacy": {"sql": ""}}), \
                mock.patch.object(rehearsal, "guard_sql"), \
                mock.patch.object(rehearsal, "assert_source_contract"), \
                mock.patch.object(rehearsal, "MIGRATION_FILES", []):
            code = rehearsal.main(["--execute", "--case", "fresh"])
        self.assertEqual(code, 0)
        run_case.assert_called_once()
        self.assertEqual(run_case.call_args.args[0], "fresh")


if __name__ == "__main__":
    unittest.main()
