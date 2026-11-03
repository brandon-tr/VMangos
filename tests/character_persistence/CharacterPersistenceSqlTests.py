#!/usr/bin/env python3
"""Offline regression tests for the character-row INSERT/UPDATE statements.

The SQL is extracted from the actual Player.cpp source.  SQLite is used only as
an in-memory fixture; this does not test native MySQL collation or execution.
"""

import re
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAYER_CPP = ROOT / "src" / "game" / "Objects" / "Player.cpp"
SAVE_CPP = ROOT / "src" / "game" / "Database" / "CharacterSavePersistence.cpp"

CREATION_COLUMNS = [
    "guid", "account", "name", "race", "class", "gender", "level", "money",
    "skin", "face", "hair_style", "hair_color", "facial_hair", "map",
    "position_x", "position_y", "position_z", "orientation", "known_taxi_mask",
    "current_taxi_path", "online", "extra_flags", "health", "power1", "power2",
    "power3", "power4", "power5", "explored_zones", "equipment_cache",
    "ammo_id", "world_phase_mask", "create_time", "logout_time",
]

SAVE_ASSIGNMENTS = [
    "race", "class", "gender", "level", "xp", "money", "skin", "face",
    "hair_style", "hair_color", "facial_hair", "bank_bag_slots",
    "character_flags", "map", "instance", "position_x", "position_y",
    "position_z", "orientation", "transport_guid", "transport_x",
    "transport_y", "transport_z", "transport_o", "known_taxi_mask",
    "current_taxi_path", "online", "played_time_total", "played_time_level",
    "rest_bonus", "logout_time", "reset_talents_multiplier",
    "reset_talents_time", "extra_flags", "stable_slots", "zone",
    "death_expire_time", "honor_rank_points", "honor_highest_rank",
    "honor_standing", "honor_last_week_hk", "honor_last_week_cp",
    "honor_stored_hk", "honor_stored_dk", "watched_faction", "drunk",
    "health", "power1", "power2", "power3", "power4", "power5",
    "explored_zones", "equipment_cache", "ammo_id", "action_bars",
    "world_phase_mask", "create_time",
]


def function_body(source, signature):
    if source.count(signature) != 1:
        raise AssertionError("target signature is missing or ambiguous: " + signature)
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    in_string = False
    in_char = False
    in_line_comment = False
    in_block_comment = False
    escaped = False
    i = brace
    while i < len(source):
        ch = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
        elif in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 1
        elif in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif in_char:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == "'":
                in_char = False
        else:
            if ch == "/" and nxt == "/":
                in_line_comment = True
                i += 1
            elif ch == "/" and nxt == "*":
                in_block_comment = True
                i += 1
            elif ch == '"':
                in_string = True
            elif ch == "'":
                in_char = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return source[start:i + 1]
        i += 1
    raise AssertionError("unterminated function body: " + signature)


def decode_cpp_literal(literal):
    return bytes(literal, "utf-8").decode("unicode_escape")


def extract_statement(source, signature):
    body = function_body(source, signature)
    matches = list(re.finditer(r"CreateStatement\s*\(\s*insChar\s*,", body))
    if len(matches) != 1:
        raise AssertionError("expected one CreateStatement(insChar, ...) in " + signature)
    start = matches[0].end()
    end = body.find(");", start)
    if end == -1:
        raise AssertionError("unterminated CreateStatement in " + signature)
    literals = re.findall(r'"(?:\\.|[^"\\])*"', body[start:end])
    if not literals:
        raise AssertionError("no SQL literals in " + signature)
    return "".join(decode_cpp_literal(literal[1:-1]) for literal in literals)


def extract_sql():
    creation_source = PLAYER_CPP.read_text(encoding="utf-8")
    save_source = SAVE_CPP.read_text(encoding="utf-8")
    return (
        extract_statement(creation_source, "uint8 Player::SaveNewPlayer("),
        extract_statement(save_source, "bool Player::AppendCharacterRowToDB("),
    )


def adapt_save_sql(sql):
    # The only permitted dialect adaptation: MySQL byte comparison to SQLite
    # BLOB comparison.  This is an offline predicate check, not native evidence.
    clause = "BINARY `name` = BINARY ?"
    if sql.count(clause) != 1:
        raise AssertionError("expected exactly one byte-name guard clause")
    return sql.replace(clause, "CAST(`name` AS BLOB) = CAST(? AS BLOB)")


def columns_from_create(sql):
    match = re.search(r"\((.*?)\)\s*VALUES", sql, re.S)
    if not match:
        raise AssertionError("creation column list not found")
    return re.findall(r"`([^`]+)`", match.group(1))


def assignments_from_update(sql):
    match = re.search(r"\bSET\b(.*?)\bWHERE\b", sql, re.S | re.I)
    if not match:
        return []
    return re.findall(r"`([^`]+)`\s*=\s*\?", match.group(1))


def make_table(conn):
    columns = [
        "guid INTEGER PRIMARY KEY",
        "account INTEGER NOT NULL DEFAULT 0",
        "name TEXT COLLATE NOCASE NOT NULL DEFAULT ''",
        "race INTEGER DEFAULT 0",
        "class INTEGER DEFAULT 0",
        "gender INTEGER DEFAULT 0",
        "level INTEGER DEFAULT 0",
        "xp INTEGER DEFAULT 0",
        "money INTEGER DEFAULT 0",
        "skin INTEGER DEFAULT 0",
        "face INTEGER DEFAULT 0",
        "hair_style INTEGER DEFAULT 0",
        "hair_color INTEGER DEFAULT 0",
        "facial_hair INTEGER DEFAULT 0",
        "bank_bag_slots INTEGER DEFAULT 0",
        "character_flags INTEGER DEFAULT 0",
        "map INTEGER DEFAULT 0",
        "instance INTEGER DEFAULT 0",
        "position_x REAL DEFAULT 0",
        "position_y REAL DEFAULT 0",
        "position_z REAL DEFAULT 0",
        "orientation REAL DEFAULT 0",
        "transport_guid INTEGER DEFAULT 0",
        "transport_x REAL DEFAULT 0",
        "transport_y REAL DEFAULT 0",
        "transport_z REAL DEFAULT 0",
        "transport_o REAL DEFAULT 0",
        "known_taxi_mask TEXT DEFAULT ''",
        "current_taxi_path TEXT DEFAULT ''",
        "online INTEGER DEFAULT 0",
        "played_time_total INTEGER DEFAULT 0",
        "played_time_level INTEGER DEFAULT 0",
        "rest_bonus REAL DEFAULT 0",
        "logout_time INTEGER DEFAULT 0",
        "reset_talents_multiplier INTEGER DEFAULT 0",
        "reset_talents_time INTEGER DEFAULT 0",
        "extra_flags INTEGER DEFAULT 0",
        "stable_slots INTEGER DEFAULT 0",
        "zone INTEGER DEFAULT 0",
        "death_expire_time INTEGER DEFAULT 0",
        "honor_rank_points REAL DEFAULT 0",
        "honor_highest_rank INTEGER DEFAULT 0",
        "honor_standing INTEGER DEFAULT 0",
        "honor_last_week_hk INTEGER DEFAULT 0",
        "honor_last_week_cp REAL DEFAULT 0",
        "honor_stored_hk INTEGER DEFAULT 0",
        "honor_stored_dk INTEGER DEFAULT 0",
        "watched_faction INTEGER DEFAULT 0",
        "drunk INTEGER DEFAULT 0",
        "health INTEGER DEFAULT 0",
        "power1 INTEGER DEFAULT 0",
        "power2 INTEGER DEFAULT 0",
        "power3 INTEGER DEFAULT 0",
        "power4 INTEGER DEFAULT 0",
        "power5 INTEGER DEFAULT 0",
        "explored_zones TEXT DEFAULT ''",
        "equipment_cache TEXT DEFAULT ''",
        "ammo_id INTEGER DEFAULT 0",
        "action_bars INTEGER DEFAULT 0",
        "world_phase_mask INTEGER DEFAULT 0",
        "create_time INTEGER DEFAULT 0",
        "deleted_name TEXT DEFAULT NULL",
        "deleted_account INTEGER DEFAULT NULL",
        "deleted_time INTEGER DEFAULT NULL",
        "identity_generation INTEGER DEFAULT 10",
        "sentinel TEXT DEFAULT 'keep'",
    ]
    conn.execute("CREATE TABLE characters (" + ", ".join(columns) + ")")
    conn.execute("CREATE TABLE relationships (guid INTEGER, payload TEXT)")
    conn.commit()


def creation_params(guid=1, account=7, name="Asha Brightvale", race=2):
    return [
        guid, account, name, race, 4, 0, 60, 12345, 3, 4, 5, 6, 7,
        1, 2.5, 3.5, 4.5, 0.25, "taxi", "path", 0, 8, 900, 100, 200,
        300, 400, 500, "zones", "equipment", 42, 99, 111, 222,
    ]


def save_params(guid=1, account=7, name="Asha Brightvale", overrides=None):
    values = [
        2, 4, 0, 60, 555, 12345, 3, 4, 5, 6, 7, 1, 8, 1, 2,
        2.5, 3.5, 4.5, 0.25, 44, 5.5, 6.5, 7.5, 0.125,
        "taxi", "path", 0, 1000, 2000, 3.5, 3000, 2, 4000, 9, 2, 12,
        5000, 11.25, 3, 4, 5, 6.5, 7, 8, 9, 10, 900, 100, 200, 300,
        400, 500, "zones", "equipment", 42, 2, 99, 111,
    ]
    if overrides:
        for index, value in overrides.items():
            values[index] = value
    values.extend([guid, account, name, 10])
    return values


def seed(conn, guid, account, name, **fields):
    columns = ["guid", "account", "name"]
    values = [guid, account, name]
    for column, value in fields.items():
        columns.append(column)
        values.append(value)
    conn.execute(
        "INSERT INTO characters (" + ", ".join(columns) + ") VALUES (" +
        ", ".join("?" for _ in values) + ")", values)
    conn.commit()


def snapshot(conn, guid):
    row = conn.execute("SELECT * FROM characters WHERE guid = ?", (guid,)).fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in conn.execute(
        "SELECT * FROM characters WHERE guid = ?", (guid,)).description], row))


class CharacterPersistenceSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.creation_sql, cls.save_sql = extract_sql()
        cls.adapted_save_sql = adapt_save_sql(cls.save_sql)

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        make_table(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_creation_is_plain_insert_with_original_shape(self):
        sql = self.creation_sql.upper()
        self.assertIn("INSERT INTO `CHARACTERS`", sql)
        self.assertNotIn("REPLACE", sql)
        self.assertNotIn("IGNORE", sql)
        self.assertNotIn("ON DUPLICATE", sql)
        self.assertNotIn("DELETE", sql)
        self.assertEqual(columns_from_create(self.creation_sql), CREATION_COLUMNS)
        self.assertEqual(self.creation_sql.count("?"), 34)
        self.conn.execute(self.creation_sql, creation_params())
        self.conn.commit()
        row = snapshot(self.conn, 1)
        self.assertEqual((row["guid"], row["account"], row["name"]), (1, 7, "Asha Brightvale"))

    def test_creation_guid_collision_preserves_existing_character(self):
        seed(self.conn, 1, 99, "Existing Person", sentinel="original")
        self.conn.execute("INSERT INTO relationships VALUES (1, 'original-relation')")
        self.conn.commit()
        before = snapshot(self.conn, 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(self.creation_sql, creation_params())
        self.assertEqual(snapshot(self.conn, 1), before)
        self.assertEqual(
            self.conn.execute("SELECT * FROM relationships").fetchall(),
            [(1, "original-relation")])

    def test_save_updates_existing_character_without_replacing_it(self):
        seed(self.conn, 1, 7, "Asha Brightvale", sentinel="sentinel",
             deleted_name="old", deleted_account=99, deleted_time=None,
             identity_generation=10)
        self.conn.execute("INSERT INTO relationships VALUES (1, 'kept')")
        self.conn.commit()
        params = save_params(overrides={0: 9, 1: 5, 2: 1, 6: 8, 8: 2, 28: 0})
        self.conn.execute(self.adapted_save_sql, params)
        self.conn.commit()
        row = snapshot(self.conn, 1)
        self.assertEqual((row["guid"], row["account"], row["name"]), (1, 7, "Asha Brightvale"))
        self.assertEqual(row["race"], 9)
        self.assertEqual(row["class"], 5)
        self.assertEqual(row["gender"], 1)
        self.assertEqual(row["money"], 12345)
        self.assertEqual(row["sentinel"], "sentinel")
        self.assertEqual(row["deleted_name"], "old")
        self.assertEqual(row["deleted_account"], 99)
        self.assertIsNone(row["deleted_time"])
        self.assertEqual(
            self.conn.execute("SELECT * FROM relationships").fetchall(),
            [(1, "kept")])

    def test_save_sql_has_complete_ordered_assignment_and_guard(self):
        self.assertEqual(assignments_from_update(self.save_sql), SAVE_ASSIGNMENTS)
        self.assertEqual(len(SAVE_ASSIGNMENTS), 58)
        self.assertEqual(self.save_sql.count("?"), 62)
        self.assertEqual(self.save_sql.count("BINARY `name` = BINARY ?"), 1)
        self.assertIn("BINARY `name` = BINARY ?", self.save_sql)
        self.assertIn("`deleted_time` IS NULL", self.save_sql)
        assignments = assignments_from_update(self.save_sql)
        for identity in ("guid", "account", "name",
                         "deleted_name", "deleted_account", "deleted_time"):
            self.assertNotIn(identity, assignments)
        self.assertNotIn("REPLACE", self.save_sql.upper())
        predicate = self.save_sql.split("WHERE", 1)[1]
        self.assertLess(predicate.index("`guid` = ?"), predicate.index("`account` = ?"))
        self.assertLess(predicate.index("`account` = ?"), predicate.index("BINARY `name` = BINARY ?"))
        self.assertIn("`deleted_time` IS NULL", predicate)
        self.assertIn("`identity_generation` = ?", predicate)

    def test_missing_character_is_not_recreated(self):
        self.conn.execute(self.adapted_save_sql, save_params())
        self.conn.commit()
        self.assertIsNone(snapshot(self.conn, 1))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM characters").fetchone()[0], 0)

    def test_hard_delete_then_stale_save_does_not_resurrect(self):
        seed(self.conn, 1, 7, "Asha Brightvale", identity_generation=10)
        seed(self.conn, 2, 8, "Other Person", identity_generation=10)
        self.conn.execute("DELETE FROM characters WHERE guid = 1")
        self.conn.commit()
        self.conn.execute(self.adapted_save_sql, save_params())
        self.conn.commit()
        self.assertIsNone(snapshot(self.conn, 1))
        self.assertEqual(snapshot(self.conn, 2)["name"], "Other Person")

    def test_soft_delete_then_stale_save_preserves_tombstone(self):
        seed(self.conn, 1, 0, "", sentinel="tombstone",
             deleted_name="Asha Brightvale", deleted_account=7, deleted_time=123,
             identity_generation=10)
        before = snapshot(self.conn, 1)
        self.conn.execute(self.adapted_save_sql, save_params())
        self.conn.commit()
        self.assertEqual(snapshot(self.conn, 1), before)

    def test_rename_then_stale_save_preserves_new_identity(self):
        seed(self.conn, 1, 7, "New Name", character_flags=777, sentinel="new",
             identity_generation=10)
        before = snapshot(self.conn, 1)
        self.conn.execute(self.adapted_save_sql,
                          save_params(name="Old Name", overrides={12: 111}))
        self.conn.commit()
        self.assertEqual(snapshot(self.conn, 1), before)

    def test_transferred_account_rejects_old_account_save(self):
        seed(self.conn, 1, 8, "Asha Brightvale", sentinel="transferred")
        before = snapshot(self.conn, 1)
        self.conn.execute(self.adapted_save_sql, save_params(account=7))
        self.conn.commit()
        self.assertEqual(snapshot(self.conn, 1), before)

    def test_name_guard_is_byte_exact(self):
        max_name = "Abcdefghijkl Mnopqrstuvwx"
        seed(self.conn, 1, 7, "Asha Brightvale", sentinel="exact",
             identity_generation=10)
        before = snapshot(self.conn, 1)
        self.conn.execute(self.adapted_save_sql,
                          save_params(name="asha brightvale"))
        self.conn.commit()
        self.assertEqual(snapshot(self.conn, 1), before)

        seed(self.conn, 2, 7, max_name, sentinel="max",
             identity_generation=10)
        self.conn.execute(self.adapted_save_sql, save_params(guid=2, name=max_name))
        self.conn.commit()
        self.assertEqual(snapshot(self.conn, 2)["name"], max_name)
        self.assertEqual(snapshot(self.conn, 2)["sentinel"], "max")

    def test_save_guard_is_first_statement_of_same_checked_batch(self):
        source = SAVE_CPP.read_text(encoding="utf-8")
        save_body = function_body(source, "void Player::SaveToDB(")
        append_body = function_body(source, "bool Player::AppendSaveToDB(")
        # One composed unit: guard first, then the row/state appends and
        # exactly one Execute.  No nested legacy transaction or independent
        # checked execution may escape the operation.
        self.assertLess(save_body.index("AppendIdentityGuards"),
                        save_body.index("AppendSaveToDB("))
        self.assertLess(save_body.index("AppendSaveToDB("),
                        save_body.index("context.Execute()"))
        self.assertNotIn("BeginTransaction", save_body)
        self.assertNotIn("CommitTransaction", save_body)
        self.assertNotIn("DirectExecuteChecked", save_body)
        self.assertLess(append_body.index("AppendCharacterRowToDB"),
                        append_body.index("AppendIdentityGenerationPublication"))
        self.assertIn("SaveToDB(bool online", save_body)

    def test_stale_generation_cannot_mutate_newer_identity(self):
        seed(self.conn, 1, 7, "Asha Brightvale", sentinel="newer",
             identity_generation=8)
        before = snapshot(self.conn, 1)
        self.conn.execute(self.adapted_save_sql, save_params())
        self.conn.commit()
        self.assertEqual(snapshot(self.conn, 1), before)

    def test_name_reuse_does_not_redirect_old_guid_save(self):
        seed(self.conn, 1, 7, "Reused Name")
        self.conn.execute("DELETE FROM characters WHERE guid = 1")
        seed(self.conn, 2, 8, "Reused Name", sentinel="fresh",
             identity_generation=10)
        self.conn.execute("INSERT INTO relationships VALUES (2, 'fresh-relation')")
        self.conn.commit()
        before = snapshot(self.conn, 2)
        self.conn.execute(self.adapted_save_sql,
                          save_params(guid=1, account=7, name="Reused Name"))
        self.conn.commit()
        self.assertIsNone(snapshot(self.conn, 1))
        self.assertEqual(snapshot(self.conn, 2), before)
        self.assertEqual(
            self.conn.execute("SELECT * FROM relationships").fetchall(),
            [(2, "fresh-relation")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
