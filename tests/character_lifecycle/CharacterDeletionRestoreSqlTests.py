import re
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IDENTITY = ROOT / "src" / "game" / "Database" / "CharacterIdentityPersistence.cpp"
CONTEXT = ROOT / "src" / "game" / "Database" / "CharacterPersistenceContext.cpp"
AUDIT = ROOT / "src" / "game" / "Database" / "CharacterNameSchemaChecks.h"
COMMANDS = ROOT / "src" / "game" / "Commands" / "CharacterCommands.cpp"
CHAT = ROOT / "src" / "game" / "Chat" / "Chat.h"


def function_body(source, marker, next_marker):
    start = source.index(marker)
    end = source.index(next_marker, start + len(marker))
    return source[start:end]


def sql_after(source, marker):
    at = source.index(marker)
    start = source.index('"', at) if marker.startswith("SqlStatement") else source.rfind('"', 0, at)
    end = source.index(";", at)
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', source[start:end])
    return "".join(parts).replace("BINARY `name`=BINARY ?", "`name`=?").replace(
        "BINARY `deleted_name`=BINARY ?", "`deleted_name`=?").replace(
        "BINARY `name`=BINARY ''", "`name`=''").replace(
        "BINARY `canonical_name`=BINARY ?", "`canonical_name`=?")


def load_sql():
    identity = IDENTITY.read_text(encoding="utf-8")
    context = CONTEXT.read_text(encoding="utf-8")
    audit = AUDIT.read_text(encoding="utf-8")
    start = audit.index("CheckCharacterNameOwnershipCounts")
    start = audit.index("Check const checks[]", start)
    block = audit[start:audit.index("};", start)]
    checks = []
    for reason, raw in re.findall(r'{\s*"([^"]+)",\s*((?:"(?:[^"\\]|\\.)*"\s*)+)}', block):
        checks.append((reason, "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', raw)).replace(
            "BINARY o.canonical_name=BINARY c.name", "o.canonical_name=c.name").replace(
            "BINARY c.name<>BINARY o.canonical_name", "c.name<>o.canonical_name")))
    assert len(checks) == 4, checks
    return (
        sql_after(context, "SqlStatement guard ="),
        sql_after(identity, "SqlStatement release ="),
        sql_after(identity, "SqlStatement tombstone ="),
        sql_after(identity, "SqlStatement restore ="),
        sql_after(identity, "SqlStatement ownership ="),
        checks,
    )


class Fixture:
    def __init__(self, sql):
        self.guard, self.release, self.tombstone, self.restore, self.claim_insert, self.checks = sql
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE characters(
                guid INTEGER PRIMARY KEY, account INTEGER, name TEXT,
                deleted_name TEXT NOT NULL DEFAULT '', deleted_account INTEGER NOT NULL DEFAULT 0,
                deleted_time INTEGER, identity_generation INTEGER, character_flags INTEGER);
            CREATE TABLE character_name_ownership(
                canonical_name BLOB PRIMARY KEY,
                guid INTEGER UNIQUE REFERENCES characters(guid));
            CREATE TABLE character_asset(
                guid INTEGER REFERENCES characters(guid), label TEXT);
        """)

    def create(self, guid, account, name, generation=1):
        self.db.execute(
            "INSERT INTO characters(guid,account,name,identity_generation) VALUES(?,?,?,?)",
            (guid, account, name, generation))
        self.db.execute("INSERT INTO character_name_ownership VALUES(?,?)", (name, guid))

    def run_guard(self, guid, account, name, generation):
        return self.db.execute(self.guard, (guid, account, name, generation, guid, name)).rowcount

    def delete(self, guid, account, name, generation, when=1700000000):
        self.db.execute("BEGIN")
        if self.run_guard(guid, account, name, generation) != 1:
            raise AssertionError("guard")
        if self.db.execute(self.release, (guid, name)).rowcount != 1:
            raise AssertionError("release")
        if self.db.execute(self.tombstone,
                           (when, guid, account, name, generation + 1)).rowcount != 1:
            raise AssertionError("tombstone")
        self.db.commit()

    def run_restore(self, guid, generation, deleted_name, account, when, target, target_account):
        return self.db.execute(self.restore,
            (target, target_account, guid, generation, when, account,
             deleted_name, guid)).rowcount

    def audit(self):
        return [self.db.execute(sql).fetchone()[0] for _, sql in self.checks]


class DeletionRestoreSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = load_sql()

    def setUp(self):
        self.f = Fixture(self.sql)
        self.f.create(1, 7, "Alice Smith", 3)

    def test_valid_creation_passes_audit(self):
        self.assertEqual(self.f.audit(), [0, 0, 0, 0])

    def test_delete_release_and_tombstone_pass_audit(self):
        self.f.delete(1, 7, "Alice Smith", 3)
        self.assertEqual(self.f.audit(), [0, 0, 0, 0])
        row = self.f.db.execute(
            "SELECT name,account,deleted_name,deleted_account,identity_generation,deleted_time "
            "FROM characters WHERE guid=1").fetchone()
        self.assertEqual(row, ("", 0, "Alice Smith", 7, 4, 1700000000))
        self.assertEqual(self.f.db.execute(
            "SELECT COUNT(*) FROM character_name_ownership WHERE guid=1").fetchone()[0], 0)

    def test_name_reuse_after_committed_delete(self):
        self.f.delete(1, 7, "Alice Smith", 3)
        self.f.create(2, 7, "Alice Smith", 1)
        self.assertEqual(self.f.audit(), [0, 0, 0, 0])

    def test_failed_restore_preserves_tombstone(self):
        self.f.delete(1, 7, "Alice Smith", 3)
        self.f.create(2, 7, "Bob Jones", 1)
        tombstone = self.f.db.execute(
            "SELECT name,account,deleted_name,deleted_account,deleted_time,identity_generation "
            "FROM characters WHERE guid=1").fetchone()
        owner = self.f.db.execute(
            "SELECT canonical_name,guid FROM character_name_ownership").fetchall()
        self.f.db.execute("BEGIN")
        self.assertEqual(self.f.run_restore(1, 4, "Alice Smith", 7, 1700000000,
                                        "Bob Jones", 7), 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.f.db.execute(self.sql[4], ("Bob Jones", 1))
        self.f.db.rollback()
        self.assertEqual(self.f.db.execute(
            "SELECT name,account,deleted_name,deleted_account,deleted_time,identity_generation "
            "FROM characters WHERE guid=1").fetchone(), tombstone)
        self.assertEqual(self.f.db.execute(
            "SELECT canonical_name,guid FROM character_name_ownership").fetchall(), owner)
        self.assertEqual(self.f.audit(), [0, 0, 0, 0])

    def test_restore_after_name_available(self):
        self.f.delete(1, 7, "Alice Smith", 3)
        self.f.db.execute("BEGIN")
        self.assertEqual(self.f.run_restore(1, 4, "Alice Smith", 7, 1700000000,
                                        "Alice Smith", 7), 1)
        self.f.db.execute(self.sql[4], ("Alice Smith", 1))
        self.f.db.commit()
        self.assertEqual(self.f.audit(), [0, 0, 0, 0])
        self.assertEqual(self.f.db.execute(
            "SELECT name,account,deleted_time,identity_generation FROM characters "
            "WHERE guid=1").fetchone(), ("Alice Smith", 7, None, 5))

    def test_rolled_back_deletion_preserves_active_row(self):
        self.f.db.execute("BEGIN")
        self.f.run_guard(1, 7, "Alice Smith", 3)
        self.f.db.execute(self.f.release, (1, "Alice Smith"))
        self.f.db.rollback()
        self.assertEqual(self.f.audit(), [0, 0, 0, 0])

    def test_stale_guard_rejected_after_delete_and_restore(self):
        self.f.delete(1, 7, "Alice Smith", 3)
        self.assertEqual(self.f.run_guard(1, 7, "Alice Smith", 3), 0)
        self.f.db.execute("BEGIN")
        self.assertEqual(self.f.run_restore(1, 4, "Alice Smith", 7, 1700000000,
                                            "Alice Smith", 7), 1)
        self.f.db.execute(self.sql[4], ("Alice Smith", 1))
        self.f.db.commit()
        self.assertEqual(self.f.run_guard(1, 7, "Alice Smith", 3), 0)
        self.assertEqual(self.f.run_guard(1, 7, "Alice Smith", 5), 1)
        self.assertEqual(self.f.audit(), [0, 0, 0, 0])

    def test_stale_tombstone_predicates(self):
        for field, args in (
            ("generation", (1, 3, "Alice Smith", 7, 1700000000, "Alice Smith", 7)),
            ("time", (1, 4, "Alice Smith", 7, 1700000001, "Alice Smith", 7)),
            ("account", (1, 4, "Alice Smith", 8, 1700000000, "Alice Smith", 7)),
            ("name", (1, 4, "Alicia Smith", 7, 1700000000, "Alice Smith", 7)),
        ):
            self.setUp()
            self.f.delete(1, 7, "Alice Smith", 3)
            self.assertEqual(self.f.run_restore(*args), 0, field)
            self.assertEqual(self.f.audit(), [0, 0, 0, 0], field)

    def test_guid_assets_survive_delete_reuse_restore(self):
        self.f.db.execute("INSERT INTO character_asset VALUES(1,'original')")
        self.f.delete(1, 7, "Alice Smith", 3)
        self.f.create(2, 7, "Alice Smith", 1)
        self.f.db.execute("INSERT INTO character_asset VALUES(2,'reused')")
        self.assertEqual(self.f.db.execute(
            "SELECT guid,label FROM character_asset ORDER BY guid").fetchall(),
            [(1, "original"), (2, "reused")])
        self.f.delete(2, 7, "Alice Smith", 1)
        self.f.db.execute("BEGIN")
        self.assertEqual(self.f.run_restore(1, 4, "Alice Smith", 7, 1700000000,
                                            "Alice Smith", 7), 1)
        self.f.db.execute(self.sql[4], ("Alice Smith", 1))
        self.f.db.commit()
        self.assertEqual(self.f.db.execute(
            "SELECT guid,label FROM character_asset ORDER BY guid").fetchall(),
            [(1, "original"), (2, "reused")])
        self.assertEqual(self.f.audit(), [0, 0, 0, 0])

    def test_duplicate_active_name_without_claim_fails_audit(self):
        self.f.db.execute(
            "INSERT INTO characters(guid,account,name,identity_generation) "
            "VALUES(2,7,'Alice Smith',1)")
        self.assertEqual(self.f.audit()[0], 1)

    def test_release_failure_or_later_failure_rolls_back(self):
        self.f.db.execute("INSERT INTO character_asset VALUES(1,'asset')")
        self.f.db.execute("BEGIN")
        self.f.run_guard(1, 7, "Alice Smith", 3)
        self.f.db.execute(self.f.release, (1, "Alice Smith"))
        self.f.db.rollback()
        self.assertEqual(self.f.db.execute(
            "SELECT name,account,deleted_time,identity_generation FROM characters "
            "WHERE guid=1").fetchone(), ("Alice Smith", 7, None, 3))
        self.assertEqual(self.f.db.execute(
            "SELECT canonical_name,guid FROM character_name_ownership").fetchall(),
            [("Alice Smith", 1)])
        self.assertEqual(self.f.db.execute(
            "SELECT guid,label FROM character_asset").fetchall(), [(1, "asset")])
        self.assertEqual(self.f.audit(), [0, 0, 0, 0])

    def test_exact_name_query_rejects_prefix_and_other_first_names(self):
        commands = COMMANDS.read_text(encoding="utf-8")
        match = re.search(
            r'"(SELECT `guid`[^"]*BINARY `deleted_name`[^"]*)"', commands)
        self.assertIsNotNone(match)
        query = match.group(1).replace(
            "BINARY `deleted_name` = BINARY '%s'", "`deleted_name` = ?")
        self.f.db.execute("PRAGMA foreign_keys=OFF")
        for guid, name in ((10, "Alice Smith"), (11, "Alice Smithson"),
                           (12, "Alice Jones"), (13, "Alicia Smith")):
            self.f.db.execute(
                "INSERT INTO characters(guid,account,name,deleted_name,"
                "deleted_account,deleted_time,identity_generation,character_flags) "
                "VALUES(?,0,'',?,0,1,2,0)", (guid, name))
        rows = self.f.db.execute(query, ("Alice Smith",)).fetchall()
        self.assertEqual([row[0] for row in rows], [10])
        self.f.db.execute("PRAGMA foreign_keys=ON")

    def test_audit_negative_fixtures(self):
        for mutation, index in (
            ("DELETE FROM character_name_ownership", 0),
            ("UPDATE character_name_ownership SET canonical_name='Wrong Name'", 1),
            ("UPDATE characters SET identity_generation=0", 2),
        ):
            self.setUp()
            self.f.db.execute(mutation)
            self.assertNotEqual(self.f.audit()[index], 0, mutation)
            self.assertEqual(self.f.audit()[-1], 0, mutation)

    def test_tombstone_retaining_claim_is_rejected(self):
        self.f.delete(1, 7, "Alice Smith", 3)
        self.f.db.execute("PRAGMA foreign_keys=OFF")
        self.f.db.execute("INSERT INTO character_name_ownership VALUES('Alice Smith',1)")
        self.f.db.commit()
        self.assertNotEqual(self.f.audit()[1], 0)

    def test_orphan_claim_is_rejected(self):
        self.f.db.execute("PRAGMA foreign_keys=OFF")
        self.f.db.execute("INSERT INTO character_name_ownership VALUES('Orphan Name',99)")
        self.f.db.commit()
        self.assertNotEqual(self.f.audit()[1], 0)


class RestorationWiringContractTests(unittest.TestCase):
    def test_live_helper_uses_builder_without_duplicate_sql(self):
        chat = (ROOT / "src" / "game" / "Chat" / "Chat.h").read_text(encoding="utf-8")
        commands = (ROOT / "src" / "game" / "Commands" / "CharacterCommands.cpp").read_text(encoding="utf-8")
        self.assertIn("uint64      originalGeneration", chat)
        self.assertIn("uint64      originalDeletedTime", chat)
        self.assertEqual(commands.count(
            "`deleted_time`, `identity_generation` FROM `characters`"), 5)
        self.assertIn("AppendCharacterRestoration(CharacterDatabase, context, request)", commands)
        self.assertNotIn("SqlStatementID restoreCharacter", commands)
        self.assertNotIn("SqlStatementID restoreClaim", commands)
        self.assertNotIn("INSERT INTO `character_name_ownership`", commands)
        self.assertIn("CharacterNameWriteStatus::Committed", commands)

class DeletedCommandTargetingTests(unittest.TestCase):
    def setUp(self):
        self.commands = COMMANDS.read_text(encoding="utf-8")
        self.chat = CHAT.read_text(encoding="utf-8")

    def test_no_default_or_two_argument_lookup(self):
        self.assertNotIn('std::string searchString = ""', self.chat)
        self.assertNotIn("GetDeletedCharacterInfoList(foundList, args)", self.commands)
        self.assertGreaterEqual(
            self.commands.count("GetDeletedCharacterInfoList(foundList, true,"), 2)
        self.assertIn("GetDeletedCharacterInfoList(foundList, useName, searchString)",
                      self.commands)

    def test_name_lookup_is_exact_canonical(self):
        self.assertIn("normalizeFullPlayerNameForLookup(searchString)", self.commands)
        self.assertIn("BINARY `deleted_name` = BINARY '%s'", self.commands)
        self.assertNotIn("`deleted_name` LIKE", self.commands)
        self.assertIn("ParseDeletedCharacterGuid", self.commands)
        self.assertIn("std::numeric_limits<uint32>::max()", self.commands)

    def test_delete_exactly_one(self):
        body = function_body(
            self.commands,
            "bool ChatHandler::HandleCharacterDeletedDeleteCommand",
            "bool ChatHandler::HandleCharacterDeletedOldCommand")
        self.assertIn("foundList.size() != 1", body)
        self.assertEqual(body.count("Player::DeleteFromDB("), 1)
        self.assertNotIn("for(const auto & itr : foundList)", body)
        self.assertIn("*args", body)

    def test_restore_exactly_one_and_preserves_original_fields(self):
        body = function_body(
            self.commands,
            "bool ChatHandler::HandleCharacterDeletedRestoreCommand",
            "bool ChatHandler::HandleCharacterDeletedDeleteCommand")
        self.assertIn("foundList.size() != 1", body)
        self.assertNotIn("HandleCharacterDeletedRestoreHelper(itr)", body)
        self.assertIn("DeletedInfo delInfo = foundList.front()", body)
        self.assertNotIn("originalName =", body)
        self.assertNotIn("originalGeneration =", body)

    def test_publication_gating_is_in_helper_body(self):
        body = function_body(
            self.commands,
            "bool ChatHandler::HandleCharacterDeletedRestoreHelper",
            "bool ChatHandler::HandleCharacterDeletedRestoreCommand")
        committed = body.index("CharacterNameWriteStatus::Committed")
        load = body.index("LoadPlayerCacheData")
        execute = body.index("context.Execute()")
        self.assertLess(execute, committed)
        self.assertLess(committed, load)


if __name__ == "__main__":
    unittest.main()
