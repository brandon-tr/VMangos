import re
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HANDLER = ROOT / "src" / "game" / "Handlers" / "CharacterHandler.cpp"
PLAYER = ROOT / "src" / "game" / "Objects" / "Player.cpp"
RENAME_FLAG = 0x4000
REVIEW_FLAG = 0x8000


def sql_after(source, marker):
    marker_at = source.index(marker)
    start = source.rfind('"', 0, marker_at)
    end = source.index(";", marker_at)
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', source[start:end])
    sql = "".join(parts)
    sql = re.sub(r"BINARY `name`=BINARY \?", "`name`=?", sql)
    sql = re.sub(r"BINARY `canonical_name`=BINARY \?", "`canonical_name`=?", sql)
    sql = re.sub(r"BINARY `name`=BINARY \?", "`name`=?", sql)
    return sql


def load_sql():
    handler = HANDLER.read_text(encoding="utf-8")
    player = PLAYER.read_text(encoding="utf-8")
    return (
        sql_after(handler, "UPDATE `characters` SET `name`"),
        sql_after(handler, "UPDATE `character_name_ownership` SET"),
        sql_after(player, "INSERT INTO `character_name_ownership`"),
    )


class CreationRenameSqlTests(unittest.TestCase):
    def setUp(self):
        self.rename, self.ownership_rename, self.creation_claim = load_sql()
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("""
            CREATE TABLE characters(
                guid INTEGER PRIMARY KEY, account INTEGER, name TEXT NOT NULL,
                character_flags INTEGER, identity_generation INTEGER,
                deleted_time INTEGER);
            CREATE TABLE character_name_ownership(
                canonical_name BLOB PRIMARY KEY, guid INTEGER UNIQUE);
            CREATE TABLE character_action(
                guid INTEGER, button INTEGER, action INTEGER, type INTEGER);
            CREATE TABLE character_tutorial(
                account INTEGER PRIMARY KEY, tut0 INTEGER);
        """)
        self.db.execute(
            "INSERT INTO characters VALUES (1,7,'Alice Smith',?,3,NULL)",
            (RENAME_FLAG,))
        self.db.execute(
            "INSERT INTO character_name_ownership VALUES ('Alice Smith',1)")
        self.db.commit()

    def rename_params(self, new="Bob Jones", old="Alice Smith",
                      account=7, generation=3, flags=RENAME_FLAG):
        return (new, RENAME_FLAG, REVIEW_FLAG, 1, account, old,
                generation, flags, flags, 1, old)

    def snapshot(self):
        return (
            self.db.execute(
                "SELECT account,name,character_flags,identity_generation "
                "FROM characters WHERE guid=1").fetchone(),
            self.db.execute(
                "SELECT canonical_name,guid FROM character_name_ownership"
            ).fetchall())

    def test_rename_success(self):
        self.db.execute("BEGIN")
        self.assertEqual(self.db.execute(
            self.rename, self.rename_params()).rowcount, 1)
        self.assertEqual(self.db.execute(
            self.ownership_rename,
            ("Bob Jones", 1, "Alice Smith")).rowcount, 1)
        self.db.commit()
        self.assertEqual(self.snapshot(),
                         ((7, "Bob Jones", REVIEW_FLAG, 4),
                          [("Bob Jones", 1)]))

    def test_destination_conflict_rolls_back(self):
        self.db.execute(
            "INSERT INTO characters VALUES (2,8,'Bob Jones',0,1,NULL)")
        self.db.execute(
            "INSERT INTO character_name_ownership VALUES ('Bob Jones',2)")
        self.db.commit()
        before = self.snapshot()
        self.db.execute("BEGIN")
        self.assertEqual(self.db.execute(
            self.rename, self.rename_params()).rowcount, 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(self.ownership_rename,
                            ("Bob Jones", 1, "Alice Smith"))
        self.db.rollback()
        self.assertEqual(self.snapshot(), before)

    def test_wrong_account_revision_or_flag_rejects(self):
        for params in (
            self.rename_params(account=8),
            self.rename_params(generation=2),
        ):
            self.assertEqual(self.db.execute(self.rename, params).rowcount, 0)
        self.db.execute("UPDATE characters SET character_flags=0 WHERE guid=1")
        self.assertEqual(self.db.execute(
            self.rename, self.rename_params()).rowcount, 0)
        self.assertEqual(self.snapshot()[0], (7, "Alice Smith", 0, 3))

    def test_max_generation_rejects(self):
        self.db.execute(
            "UPDATE characters SET identity_generation=18446744073709551615 "
            "WHERE guid=1")
        self.assertEqual(self.db.execute(self.rename, self.rename_params()).rowcount, 0)

    def test_same_name_clears_flag_without_ownership_update(self):
        self.db.execute("BEGIN")
        self.assertEqual(self.db.execute(
            self.rename, self.rename_params(new="Alice Smith")).rowcount, 1)
        self.db.commit()
        self.assertEqual(self.snapshot()[0],
                         (7, "Alice Smith", REVIEW_FLAG, 4))
        self.assertEqual(self.snapshot()[1], [("Alice Smith", 1)])

    def test_creation_claim_conflict_rolls_back_all_rows(self):
        self.db.execute("BEGIN")
        self.db.execute(
            "INSERT INTO characters VALUES (2,7,'Alice Smith',0,1,NULL)")
        self.db.execute(
            "INSERT INTO character_action VALUES (2,0,1,0)")
        self.db.execute(
            "INSERT INTO character_tutorial VALUES (7,1)")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(self.creation_claim, ("Alice Smith", 2))
        self.db.rollback()
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM characters WHERE guid=2").fetchone()[0], 0)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM character_action WHERE guid=2").fetchone()[0], 0)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM character_tutorial WHERE account=7").fetchone()[0], 0)

    def test_same_first_and_same_surname_coexist(self):
        self.db.execute("BEGIN")
        self.db.execute("INSERT INTO characters VALUES (2,7,'Alice Jones',0,1,NULL)")
        self.db.execute(
            "INSERT INTO character_name_ownership VALUES ('Alice Jones',2)")
        self.db.execute("INSERT INTO characters VALUES (3,7,'Carol Smith',0,1,NULL)")
        self.db.execute(
            "INSERT INTO character_name_ownership VALUES ('Carol Smith',3)")
        self.db.commit()
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM character_name_ownership").fetchone()[0], 3)


if __name__ == "__main__":
    unittest.main()
