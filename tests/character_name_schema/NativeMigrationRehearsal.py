#!/usr/bin/env python3
"""Guarded offline migration rehearsal runner.

No Docker/MySQL access occurs unless --execute is explicitly supplied.  The
future execution path uses one newly created fn05h_v20_test_ schema per case
and drops only the schema it successfully created.
"""

import argparse
import hashlib
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "character_lifecycle"))
sys.path.insert(0, str(ROOT / "tests" / "character_name_schema"))

from NativeSchemaFixture import MySql  # noqa: E402
from NativeTargetGuard import IMAGE, SCHEMA_RE, validate_target  # noqa: E402
from NativeCharacterPersistenceTests import (  # noqa: E402
    published_endpoint, root_password)

LEGACY_REV = "316e886869ed7fb3a747e2adbcc720e9ada995cf"
LEGACY_BLOB = "b2da3f85aaace21018fae05bc9730bfaf78fac74"
MIGRATION_1 = "20261030222040"
MIGRATION_2 = "20261031010000"
HISTORICAL_MIGRATIONS = [
    "20220813085336", "20221001202815", "20241002031703",
    "20241113131610", "20241114131912", "20250428111913",
    "20250712023524", "20260826140125",
]
MIGRATION_FILES = [
    ROOT / "sql" / "migrations" / f"{number}_characters.sql"
    for number in HISTORICAL_MIGRATIONS + [MIGRATION_1, MIGRATION_2]
]
CURRENT_BASE = ROOT / "sql" / "characters.sql"
EVIDENCE = ROOT / "build" / "fn05h-v84"
CHECKS_HEADER = ROOT / "src" / "game" / "Database" / "CharacterNameSchemaChecks.h"


def _load_required_tables():
    source = CHECKS_HEADER.read_text(encoding="utf-8")
    match = re.search(r"tableNames\s*=\s*(.*?);", source, re.S)
    if not match:
        raise AssertionError("cannot extract production required-table list")
    names = re.findall(r"'([^']+)'", match.group(1))
    if len(names) != 56 or len(set(names)) != 56:
        raise AssertionError("production required-table list is not 56 unique names")
    return names


REQUIRED_TABLES = _load_required_tables()

# SQL must remain inside the database selected by -D.  These statements can
# change the selected schema, target another schema, or use client/file escape
# commands.  information_schema.* is the one allowed qualification because the
# migration procedures use it for read-only metadata checks.
DANGEROUS = re.compile(
    r"(?is)\b(?:USE|CREATE\s+DATABASE|DROP\s+DATABASE|ALTER\s+DATABASE|"
    r"CREATE\s+SCHEMA|DROP\s+SCHEMA|GRANT|REVOKE|CREATE\s+USER|"
    r"DROP\s+USER|SET\s+GLOBAL|LOAD\s+DATA|INTO\s+OUTFILE|"
    r"INTO\s+DUMPFILE|RENAME\s+TABLE|TRUNCATE)\b")
QUALIFIED = re.compile(
    r"(?is)\b(?:FROM|JOIN|INTO|UPDATE|TABLE|REFERENCES)\s+"
    r"`?([A-Za-z_][A-Za-z0-9_]*)`?\s*\.\s*`?([A-Za-z_][A-Za-z0-9_]*)`?")
CLIENT_ESCAPE = re.compile(
    r"(?im)^\s*(?:source|system|connect|resetconnection|tee|pager|"
    r"\\\.|\\!)\b")


class MigrationSchema(MySql):
    """Exact run-owned schema without the native fixture tables."""

    def create(self):
        if self.created:
            raise RuntimeError("migration schema already created by this run")
        candidate = self.schema
        if not SCHEMA_RE.fullmatch(candidate):
            raise ValueError("refusing invalid migration schema identifier")
        self._run(f"CREATE DATABASE `{candidate}` "
                  "CHARACTER SET utf8 COLLATE utf8_bin;")
        self._owned_schema = candidate
        self.created = True

    def run_script(self, sql):
        guard_sql(sql, self.schema)
        self.sql(sql)


def _check_sql_text(sql):
    match = DANGEROUS.search(sql)
    if match:
        raise ValueError("forbidden SQL construct: " + match.group(0))
    for match in QUALIFIED.finditer(sql):
        if match.group(1).lower() != "information_schema":
            raise ValueError("cross-schema reference: " + match.group(0))
    match = CLIENT_ESCAPE.search(sql)
    if match:
        raise ValueError("forbidden client command: " + match.group(0))


def guard_sql(sql, selected_schema):
    """Reject schema escape and client/file commands before any SQL call."""
    # Executable version comments are still SQL; inspect their bodies before
    # stripping comments used for documentation.
    for body in re.findall(r"/\*!(.*?)\*/", sql, re.S):
        _check_sql_text(body)
    cleaned = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    cleaned = re.sub(r"--[^\n]*", " ", cleaned)
    cleaned = re.sub(r"#[^\n]*", " ", cleaned)
    _check_sql_text(cleaned)
    return True


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_inputs(write_evidence=False):
    current = CURRENT_BASE.read_text(encoding="utf-8")
    legacy_run = subprocess.run(
        ["git", "show", f"{LEGACY_REV}:sql/characters.sql"],
        cwd=ROOT, text=True, capture_output=True, check=True)
    legacy = legacy_run.stdout
    if "varchar(12)" not in legacy or "ENGINE=MyISAM" not in legacy:
        raise AssertionError("legacy Git object is not the pre-feature schema")
    if "identity_generation" in legacy or "character_name_ownership" in legacy:
        raise AssertionError("legacy Git object unexpectedly has feature structures")
    blob = subprocess.run(
        ["git", "rev-parse", f"{LEGACY_REV}:sql/characters.sql"],
        cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    if blob != LEGACY_BLOB:
        raise AssertionError("unexpected legacy blob: " + blob)
    result = {
        "current": {
            "path": str(CURRENT_BASE), "provenance": "working-tree",
            "sha256": sha256(current), "sql": current,
        },
        "legacy": {
            "path": f"{LEGACY_REV}:sql/characters.sql",
            "provenance": "git-show", "blob": blob,
            "sha256": sha256(legacy), "sql": legacy,
        },
    }
    if write_evidence:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        (EVIDENCE / "current.sql").write_bytes(current.encode("utf-8"))
        (EVIDENCE / "legacy.sql").write_bytes(legacy.encode("utf-8"))
        (EVIDENCE / "inputs.txt").write_bytes(
            (f"current {result['current']['sha256']} {CURRENT_BASE}\n"
             f"legacy {result['legacy']['sha256']} blob={blob} "
             f"{LEGACY_REV}:sql/characters.sql\n").encode("utf-8"))
    return result


def migration_sql(number):
    for path in MIGRATION_FILES:
        if path.name.startswith(number + "_"):
            return path.read_text(encoding="utf-8")
    raise AssertionError("missing migration " + number)


def assert_source_contract():
    """Fail loudly if the extracted production contract has drifted."""
    header = CHECKS_HEADER.read_text(encoding="utf-8")
    for token in (
        "required character-name schema tables are missing",
        "required character-name schema tables are not InnoDB",
        "characters.identity_generation has an incompatible definition",
        "character naming columns are missing or not varchar(25)",
        "character_name_ownership columns have incompatible definitions",
        "character_name_ownership primary key is not canonical_name",
        "character_name_ownership guid unique key is invalid",
        "character_name_ownership foreign key target is invalid",
        "character_name_ownership foreign key rules are invalid",
        "an active character has no matching canonical name claim",
        "a name claim does not exactly match an active character",
        "an active character has an invalid identity generation",
        "an active character name is empty or malformed",
        "UPDATE_RULE='RESTRICT' AND DELETE_RULE='RESTRICT'",
    ):
        if token not in header:
            raise AssertionError("production schema-check drift: " + token)
    if len(REQUIRED_TABLES) != 56:
        raise AssertionError("production required-table count drift")
    for path in MIGRATION_FILES:
        if not path.exists():
            raise AssertionError("missing migration source: " + str(path))
    migration_2 = migration_sql(MIGRATION_2)
    for number in HISTORICAL_MIGRATIONS:
        if number not in migration_2:
            raise AssertionError("feature migration omits historical prerequisite: " +
                                 number)
    for token in (
        "requires migration 20261030222040",
        "requires all eight historical Character migrations",
        "requires an empty characters table",
        "ownership table must be empty",
        "required tables are not all InnoDB",
        "incompatible identity_generation",
        "incompatible naming columns",
        "incompatible ownership columns",
        "incompatible ownership primary key",
        "incompatible ownership unique key",
        "incompatible ownership foreign key",
        "incompatible ownership foreign-key rules",
        "INSERT INTO `migrations` VALUES ('20261031010000')",
    ):
        if token not in migration_2:
            raise AssertionError("migration source drift: " + token)


def drop_owned_schema(schema, schema_name):
    try:
        schema.drop()
    except BaseException as error:
        print(f"MIGRATION_SCHEMA_CLEANUP_FAILED: {schema_name}: "
              f"{type(error).__name__}", flush=True)
        raise
    print(f"MIGRATION_SCHEMA_DROPPED: {schema_name}", flush=True)


def run_case(name, body):
    schema = MigrationSchema()
    schema_name = schema.schema
    host, port = published_endpoint()
    password = root_password()
    error = validate_target(IMAGE, host, port, schema_name, "root", password)
    if error:
        raise RuntimeError("migration target guard: " + error)
    print(f"MIGRATION_SCHEMA_ATTEMPTED: {schema_name}", flush=True)
    schema.create()
    print(f"MIGRATION_SCHEMA_CREATED: {schema_name}", flush=True)
    try:
        body(schema)
        print(f"MIGRATION_CASE_PASS: {name}", flush=True)
    finally:
        drop_owned_schema(schema, schema_name)


def eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: got {actual!r}, expected {expected!r}")


def one(schema, sql):
    return schema.one(sql).strip()


def marker(schema, number):
    return one(schema, f"SELECT COUNT(*) FROM `migrations` WHERE `id`='{number}'")


def column_length(schema, table, column):
    return one(schema, "SELECT CHARACTER_MAXIMUM_LENGTH FROM "
                      "information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                      f"AND TABLE_NAME='{table}' AND COLUMN_NAME='{column}'")


def identity_column_exists(schema):
    return one(schema, "SELECT COUNT(*) FROM information_schema.COLUMNS "
                       "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='characters' "
                       "AND COLUMN_NAME='identity_generation'")


def table_exists(schema, table):
    return one(schema, "SELECT COUNT(*) FROM information_schema.TABLES "
                      "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME="
                      f"'{table}'")


def engine(schema, table):
    return one(schema, "SELECT ENGINE FROM information_schema.TABLES "
                      "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME="
                      f"'{table}'")


def innodb_count(schema):
    return one(schema, "SELECT COUNT(*) FROM information_schema.TABLES "
                       "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE' "
                       "AND ENGINE='InnoDB'")


def assert_final_contract(schema):
    """Production-derived metadata/audit checks, executed as SQL."""
    assert_source_contract()
    names = ",".join("'" + name + "'" for name in REQUIRED_TABLES)
    named = ("SELECT COUNT(*) FROM information_schema.TABLES "
             "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE' "
             f"AND TABLE_NAME IN ({names})")
    eq(one(schema, named), "56", "all 56 required named tables")
    eq(one(schema, named + " AND ENGINE='InnoDB'"), "56",
       "all 56 required named tables InnoDB")

    naming = ("SELECT COUNT(*) FROM information_schema.COLUMNS "
              "WHERE TABLE_SCHEMA=DATABASE() AND DATA_TYPE='varchar' "
              "AND CHARACTER_MAXIMUM_LENGTH=25 AND "
              "((TABLE_NAME='characters' AND COLUMN_NAME IN ('name','deleted_name')) "
              "OR (TABLE_NAME='gm_tickets' AND COLUMN_NAME='name'))")
    eq(one(schema, naming), "3", "three naming columns varchar(25)")
    for table, column, nullable in (
            ("characters", "name", "NO"),
            ("characters", "deleted_name", "YES"),
            ("gm_tickets", "name", "NO")):
        eq(one(schema, "SELECT COUNT(*) FROM information_schema.COLUMNS "
                       "WHERE TABLE_SCHEMA=DATABASE() "
                       f"AND TABLE_NAME='{table}' AND COLUMN_NAME='{column}' "
                       "AND DATA_TYPE='varchar' AND CHARACTER_MAXIMUM_LENGTH=25 "
                       f"AND IS_NULLABLE='{nullable}'"), "1",
           f"{table}.{column} exact width/nullability")

    eq(one(schema, "SELECT COUNT(*) FROM information_schema.COLUMNS "
                   "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='characters' "
                   "AND COLUMN_NAME='identity_generation' AND DATA_TYPE='bigint' "
                   "AND COLUMN_TYPE LIKE '%unsigned%' AND IS_NULLABLE='NO' "
                   "AND COLUMN_DEFAULT='1'"), "1",
       "identity_generation bigint unsigned NOT NULL DEFAULT 1")

    eq(one(schema, "SELECT COUNT(*) FROM information_schema.COLUMNS "
                   "WHERE TABLE_SCHEMA=DATABASE() "
                   "AND TABLE_NAME='character_name_ownership' AND "
                   "((COLUMN_NAME='canonical_name' AND DATA_TYPE='varbinary' "
                   "AND CHARACTER_MAXIMUM_LENGTH=75 AND IS_NULLABLE='NO') "
                   "OR (COLUMN_NAME='guid' AND DATA_TYPE='int' "
                   "AND COLUMN_TYPE LIKE '%unsigned%' AND IS_NULLABLE='NO')) "
                   "AND (SELECT COUNT(*) FROM information_schema.COLUMNS c2 "
                   "WHERE c2.TABLE_SCHEMA=DATABASE() "
                   "AND c2.TABLE_NAME='character_name_ownership')=2"), "2",
       "ownership column definitions")

    eq(one(schema, "SELECT COUNT(*) FROM information_schema.STATISTICS s "
                   "WHERE s.TABLE_SCHEMA=DATABASE() "
                   "AND s.TABLE_NAME='character_name_ownership' "
                   "AND s.INDEX_NAME='PRIMARY' AND s.COLUMN_NAME='canonical_name' "
                   "AND s.SEQ_IN_INDEX=1 AND s.NON_UNIQUE=0 AND s.SUB_PART IS NULL "
                   "AND (SELECT COUNT(*) FROM information_schema.STATISTICS s2 "
                   "WHERE s2.TABLE_SCHEMA=DATABASE() "
                   "AND s2.TABLE_NAME='character_name_ownership' "
                   "AND s2.INDEX_NAME='PRIMARY')=1"), "1",
       "ownership primary key")

    eq(one(schema, "SELECT COUNT(*) FROM information_schema.STATISTICS s "
                   "WHERE s.TABLE_SCHEMA=DATABASE() "
                   "AND s.TABLE_NAME='character_name_ownership' "
                   "AND s.INDEX_NAME='uq_character_name_guid' AND s.COLUMN_NAME='guid' "
                   "AND s.SEQ_IN_INDEX=1 AND s.NON_UNIQUE=0 AND s.SUB_PART IS NULL "
                   "AND (SELECT COUNT(*) FROM information_schema.STATISTICS s2 "
                   "WHERE s2.TABLE_SCHEMA=DATABASE() "
                   "AND s2.TABLE_NAME='character_name_ownership' "
                   "AND s2.INDEX_NAME='uq_character_name_guid')=1"), "1",
       "ownership guid unique key")

    eq(one(schema, "SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE k "
                   "WHERE k.TABLE_SCHEMA=DATABASE() "
                   "AND k.TABLE_NAME='character_name_ownership' "
                   "AND k.CONSTRAINT_NAME='fk_character_name_owner' "
                   "AND k.COLUMN_NAME='guid' AND k.REFERENCED_TABLE_SCHEMA=DATABASE() "
                   "AND k.REFERENCED_TABLE_NAME='characters' "
                   "AND k.REFERENCED_COLUMN_NAME='guid' "
                   "AND (SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE k2 "
                   "WHERE k2.TABLE_SCHEMA=DATABASE() "
                   "AND k2.TABLE_NAME='character_name_ownership' "
                   "AND k2.CONSTRAINT_NAME='fk_character_name_owner')=1"), "1",
       "ownership foreign key target")

    eq(one(schema, "SELECT COUNT(*) FROM information_schema.REFERENTIAL_CONSTRAINTS "
                   "WHERE CONSTRAINT_SCHEMA=DATABASE() "
                   "AND TABLE_NAME='character_name_ownership' "
                   "AND CONSTRAINT_NAME='fk_character_name_owner' "
                   "AND UPDATE_RULE='RESTRICT' AND DELETE_RULE='RESTRICT'"), "1",
       "ownership foreign key rules")

    for label, sql in (
        ("active claim missing",
         "SELECT COUNT(*) FROM `characters` c "
         "LEFT JOIN `character_name_ownership` o "
         "ON o.guid=c.guid AND BINARY o.canonical_name=BINARY c.name "
         "WHERE c.deleted_time IS NULL AND c.account<>0 AND o.guid IS NULL"),
        ("claim does not match active character",
         "SELECT COUNT(*) FROM `character_name_ownership` o "
         "LEFT JOIN `characters` c ON c.guid=o.guid "
         "WHERE c.guid IS NULL OR c.deleted_time IS NOT NULL "
         "OR c.account=0 OR BINARY c.name<>BINARY o.canonical_name"),
        ("active generation invalid",
         "SELECT COUNT(*) FROM `characters` "
         "WHERE deleted_time IS NULL AND account<>0 "
         "AND (identity_generation IS NULL OR identity_generation=0)"),
        ("active name malformed",
         "SELECT COUNT(*) FROM `characters` "
         "WHERE deleted_time IS NULL AND account<>0 "
         "AND (name IS NULL OR name='' OR name LIKE '% % %')"),
    ):
        eq(one(schema, sql), "0", "ownership audit: " + label)


def assert_fresh_metadata(schema):
    assert_final_contract(schema)


def seed_legacy(schema, inputs):
    schema.run_script(inputs["legacy"]["sql"])


def apply_historical(schema, skip=None):
    for number in HISTORICAL_MIGRATIONS:
        if number == skip:
            continue
        schema.run_script(migration_sql(number))


def apply_full_chain(schema, base_sql):
    schema.run_script(base_sql)
    apply_historical(schema)
    schema.run_script(migration_sql(MIGRATION_1))
    schema.run_script(migration_sql(MIGRATION_2))


def column_exists(schema, table, column):
    return one(schema, "SELECT COUNT(*) FROM information_schema.COLUMNS "
                       f"WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='{table}' "
                       f"AND COLUMN_NAME='{column}'")


def index_exists(schema, table, index):
    return one(schema, "SELECT COUNT(*) FROM information_schema.STATISTICS "
                       f"WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='{table}' "
                       f"AND INDEX_NAME='{index}'")


def assert_historical_effects(schema):
    for table in ("account_data", "character_account_data"):
        eq(table_exists(schema, table), "1", table + " exists")
        eq(engine(schema, table), "InnoDB", table + " engine")
    eq(column_exists(schema, "account_data", "account"), "1", "account_data.account")
    eq(column_exists(schema, "character_account_data", "guid"), "1",
       "character_account_data.guid")
    for column in ("spell_crit_chance", "spell_damage", "spell_healing"):
        eq(column_exists(schema, "character_stats", column), "1",
           "character_stats." + column)
    eq(column_exists(schema, "characters", "instance"), "1",
       "characters.instance")
    eq(column_exists(schema, "characters", "instance_id"), "0",
       "characters.instance_id absent")
    eq(index_exists(schema, "characters", "idx_instance"), "1",
       "characters.idx_instance")
    eq(column_exists(schema, "characters", "character_flags"), "1",
       "characters.character_flags")
    for column in ("player_flags", "at_login_flags", "is_logout_resting"):
        eq(column_exists(schema, "characters", column), "0",
           "characters." + column + " absent")
    eq(index_exists(schema, "character_deleted_items", "idx_playerGuid"), "1",
       "character_deleted_items.idx_playerGuid")
    eq(table_exists(schema, "worldstates"), "0", "worldstates absent")
    eq(table_exists(schema, "character_duplicate_account"), "0",
       "character_duplicate_account absent")


def assert_full_chain(schema):
    assert_final_contract(schema)
    assert_historical_effects(schema)


def run_fresh(schema):
    apply_full_chain(schema, load_inputs()["current"]["sql"])
    assert_full_chain(schema)


def run_upgrade(schema):
    inputs = load_inputs()
    schema.run_script(inputs["legacy"]["sql"])
    eq(column_length(schema, "characters", "name"), "12", "legacy name width")
    eq(engine(schema, "auction"), "MyISAM", "legacy auction engine")
    apply_historical(schema)
    schema.run_script(migration_sql(MIGRATION_1))
    schema.run_script(migration_sql(MIGRATION_2))
    assert_full_chain(schema)


def run_reapply(schema):
    inputs = load_inputs()
    apply_full_chain(schema, inputs["current"]["sql"])
    before = schema.one("SELECT GROUP_CONCAT(`id` ORDER BY `id`) FROM `migrations`")
    for number in HISTORICAL_MIGRATIONS + [MIGRATION_1, MIGRATION_2]:
        schema.run_script(migration_sql(number))
    after = schema.one("SELECT GROUP_CONCAT(`id` ORDER BY `id`) FROM `migrations`")
    eq(after, before, "reapplication markers")
    assert_full_chain(schema)


def run_missing_dependency(schema):
    schema.run_script(load_inputs()["legacy"]["sql"])
    apply_historical(schema)
    try:
        schema.run_script(migration_sql(MIGRATION_2))
    except RuntimeError as error:
        if "requires migration 20261030222040" not in str(error):
            raise
    else:
        raise AssertionError("missing width dependency was not rejected")
    eq(marker(schema, MIGRATION_1), "0", "width dependency marker 1")
    eq(marker(schema, MIGRATION_2), "0", "width dependency marker 2")
    eq(table_exists(schema, "character_name_ownership"), "0",
       "width dependency ownership")
    eq(identity_column_exists(schema), "0",
       "width dependency identity column")
    eq(engine(schema, "auction"), "MyISAM", "width dependency engine")


def run_missing_history(schema):
    schema.run_script(load_inputs()["legacy"]["sql"])
    apply_historical(schema, skip=HISTORICAL_MIGRATIONS[-1])
    schema.run_script(migration_sql(MIGRATION_1))
    try:
        schema.run_script(migration_sql(MIGRATION_2))
    except RuntimeError as error:
        if "requires all eight historical Character migrations" not in str(error):
            raise
    else:
        raise AssertionError("missing historical prerequisite was not rejected")
    eq(marker(schema, MIGRATION_1), "1", "history refusal width marker")
    eq(marker(schema, MIGRATION_2), "0", "history refusal feature marker")
    eq(table_exists(schema, "character_name_ownership"), "0",
       "history refusal ownership")
    eq(identity_column_exists(schema), "0", "history refusal identity")
    eq(engine(schema, "auction"), "MyISAM", "history refusal engine")


def run_nonempty_characters(schema):
    schema.run_script(load_inputs()["legacy"]["sql"])
    apply_historical(schema)
    schema.run_script(migration_sql(MIGRATION_1))
    schema.sql("INSERT INTO `characters` (`guid`,`account`,`name`) "
               "VALUES (1,7,'Legacy Name')")
    try:
        schema.run_script(migration_sql(MIGRATION_2))
    except RuntimeError as error:
        if "requires an empty characters table" not in str(error):
            raise
    else:
        raise AssertionError("nonempty characters was not rejected")
    eq(one(schema, "SELECT name FROM `characters` WHERE guid=1"),
       "Legacy Name", "sentinel retained")
    eq(marker(schema, MIGRATION_2), "0", "nonempty marker absent")
    eq(table_exists(schema, "character_name_ownership"), "0",
       "nonempty ownership absent")
    eq(identity_column_exists(schema), "0", "nonempty identity absent")
    eq(engine(schema, "auction"), "MyISAM", "nonempty engine untouched")


def run_nonempty_ownership(schema):
    schema.run_script(load_inputs()["legacy"]["sql"])
    apply_historical(schema)
    schema.run_script(migration_sql(MIGRATION_1))
    eq(marker(schema, MIGRATION_1), "1", "ownership prerequisite marker")
    eq(one(schema, "SELECT COUNT(*) FROM `characters`"), "0",
       "ownership prerequisite empty characters")
    schema.sql("CREATE TABLE `character_name_ownership` ("
               "`canonical_name` VARBINARY(75) NOT NULL PRIMARY KEY,"
               "`guid` INT UNSIGNED NOT NULL) ENGINE=InnoDB")
    schema.sql("INSERT INTO `character_name_ownership` VALUES ('Legacy',1)")
    try:
        schema.run_script(migration_sql(MIGRATION_2))
    except RuntimeError as error:
        if "ownership table must be empty" not in str(error):
            raise
    else:
        raise AssertionError("nonempty ownership was not rejected")
    eq(marker(schema, MIGRATION_1), "1", "ownership refusal marker 1 retained")
    eq(marker(schema, MIGRATION_2), "0", "ownership refusal marker 2 absent")
    eq(identity_column_exists(schema), "0",
       "ownership refusal identity absent")
    eq(one(schema, "SELECT COUNT(*) FROM `character_name_ownership`"), "1",
       "ownership sentinel retained")
    eq(engine(schema, "auction"), "MyISAM",
       "ownership refusal engine untouched")


def run_partial_failure(schema):
    schema.run_script(load_inputs()["legacy"]["sql"])
    apply_historical(schema)
    schema.run_script(migration_sql(MIGRATION_1))
    schema.sql("RENAME TABLE `character_account_data` TO "
               "`character_account_data_hold`")
    try:
        schema.run_script(migration_sql(MIGRATION_2))
    except RuntimeError as error:
        text = str(error)
        if not re.search(r"ERROR 1146 \(42S02\).*character_account_data.*"
                         r"doesn't exist", text, re.IGNORECASE | re.DOTALL):
            raise AssertionError("unexpected partial-failure diagnostic: " + text)
    else:
        raise AssertionError("controlled missing table did not fail")
    eq(marker(schema, MIGRATION_2), "0", "partial marker absent")
    eq(engine(schema, "auction"), "InnoDB", "earlier conversion committed")
    eq(table_exists(schema, "character_account_data"), "0",
       "late table still missing")
    schema.sql("RENAME TABLE `character_account_data_hold` TO "
               "`character_account_data`")
    schema.run_script(migration_sql(MIGRATION_2))
    assert_full_chain(schema)


CASES = {
    "fresh": run_fresh,
    "upgrade": run_upgrade,
    "reapply": run_reapply,
    "missing-dependency": run_missing_dependency,
    "missing-history": run_missing_history,
    "nonempty-characters": run_nonempty_characters,
    "nonempty-ownership": run_nonempty_ownership,
    "partial-failure": run_partial_failure,
}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true",
                        help="explicitly allow disposable-schema DDL")
    parser.add_argument("--case", choices=["all", *CASES], default="all")
    parser.add_argument("--evidence-dir", default=str(EVIDENCE))
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not args.execute:
        print("REFUSED: migration rehearsal requires explicit --execute; "
              "no Docker or MySQL call was made.", flush=True)
        return 2
    global EVIDENCE
    EVIDENCE = pathlib.Path(args.evidence_dir)
    inputs = load_inputs(write_evidence=True)
    guard_sql(inputs["current"]["sql"], "<selected>")
    guard_sql(inputs["legacy"]["sql"], "<selected>")
    for path in MIGRATION_FILES:
        guard_sql(path.read_text(encoding="utf-8"), "<selected>")
    selected = list(CASES) if args.case == "all" else [args.case]
    name = "source-contract"
    try:
        assert_source_contract()
        for name in selected:
            run_case(name, CASES[name])
    except BaseException as error:
        print(f"MIGRATION_REHEARSAL_FAILED: {name}: "
              f"{type(error).__name__}: {error}", flush=True)
        return 1
    print("MIGRATION_REHEARSAL_PASS", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
