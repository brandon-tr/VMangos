import os
import re
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from NativeTargetGuard import SCHEMA_RE, docker_target

ROOT = Path(__file__).resolve().parents[2]
IDENTITY = ROOT / "src" / "game" / "Database" / "CharacterIdentityPersistence.cpp"
CONTEXT = ROOT / "src" / "game" / "Database" / "CharacterPersistenceContext.cpp"
AUDIT = ROOT / "src" / "game" / "Database" / "CharacterNameSchemaChecks.h"
CONTAINER = "vmangos-mysql"


def extract(source, signature):
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace:index + 1]
    raise AssertionError("unterminated " + signature)


def sql_after(source, marker, signature=False):
    at = source.index(marker)
    start = source.index('"', at) if signature else source.rfind('"', 0, at)
    end = source.index(";", at)
    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', source[start:end]))


def joined_literals(segment):
    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', segment))


def load_permanent_sql():
    source = IDENTITY.read_text(encoding="utf-8")
    body = extract(source, "bool AppendPermanentCharacterDeletion")
    active_at = body.index("guardActive")
    active_end = body.index("if (!guard.AppendToCheckedBatch", active_at)
    tomb_at = body.index("guardTombstone")
    tomb_end = body.index("if (!guard.AppendToCheckedBatch", tomb_at)
    deletes_at = body.index('"DELETE FROM `character_action`')
    final_at = body.index("deleteActive")
    final_end = body.index("if (!finalDelete.AppendToCheckedBatch", final_at)
    active_guard = joined_literals(body[active_at:active_end])
    tombstone_guard = joined_literals(body[tomb_at:tomb_end])
    deletes = re.findall(
        r'"(DELETE FROM (?:`character_action`|`character_aura`|`character_battleground_data`|'
        r'`character_deleted_items`|`character_gifts`|`character_homebind`|`character_instance`|'
        r'`group_instance`|`character_inventory`|`character_queststatus`|`character_reputation`|'
        r'`character_skills`|`character_forgotten_skills`|`character_spell`|'
        r'`character_spell_cooldown`|`item_instance`|`character_social`|`mail`|`mail_items`|'
        r'`pet_aura`|`pet_spell`|`pet_spell_cooldown`|`character_pet`|`guild_eventlog`)[^"]*)"',
        body[deletes_at:final_at])
    active_final_at = body.index('deleteActive,')
    active_final_at = body.index('"', active_final_at)
    tombstone_final_at = body.index('deleteTombstone,')
    tombstone_final_at = body.index('"', tombstone_final_at)
    active_final_end = body.index(");", active_final_at)
    tombstone_final_end = body.index(")\n        :", tombstone_final_at)
    active_final = joined_literals(body[active_final_at:active_final_end])
    tombstone_final = joined_literals(body[tombstone_final_at:tombstone_final_end])
    assert active_guard.startswith("UPDATE `characters`")
    assert tombstone_guard.startswith("UPDATE `characters`")
    assert active_guard.count("?") == 8, active_guard
    assert tombstone_guard.count("?") == 8, tombstone_guard
    assert active_final.startswith("DELETE FROM `characters`")
    assert tombstone_final.startswith("DELETE FROM `characters`")
    assert active_final.count("?") == 4, active_final
    assert tombstone_final.count("?") == 5, tombstone_final
    assert len(deletes) == 24, len(deletes)
    return {
        "active_guard": active_guard,
        "tombstone_guard": tombstone_guard,
        "deletes": deletes,
        "active_final": active_final,
        "tombstone_final": tombstone_final,
    }


def load_sql():
    identity = IDENTITY.read_text(encoding="utf-8")
    context = CONTEXT.read_text(encoding="utf-8")
    audit = AUDIT.read_text(encoding="utf-8")
    start = audit.index("CheckCharacterNameOwnershipCounts")
    start = audit.index("Check const checks[]", start)
    block = audit[start:audit.index("};", start)]
    checks = []
    for _reason, raw in re.findall(
            r'{\s*"([^"]+)",\s*((?:"(?:[^"\\]|\\.)*"\s*)+)}', block):
        checks.append("".join(
            re.findall(r'"((?:[^"\\]|\\.)*)"', raw)))
    assert len(checks) == 4
    return (
        sql_after(context, "SqlStatement guard =", True),
        sql_after(identity, "SqlStatement release =", True),
        sql_after(identity, "SqlStatement tombstone =", True),
        sql_after(identity, "SqlStatement restore =", True),
        sql_after(identity, "SqlStatement ownership =", True),
        checks,
    )


def bind(sql, *params):
    expected = sql.count("?")
    if len(params) != expected:
        raise AssertionError(
            f"binding count {len(params)} != placeholders {expected}: {sql}")
    for value in params:
        if isinstance(value, str):
            literal = "'" + value.replace("'", "''") + "'"
        elif value is None:
            literal = "NULL"
        else:
            literal = str(value)
        sql = sql.replace("?", literal, 1)
    if "?" in sql:
        raise AssertionError("unbound placeholder")
    return sql


from NativeSchemaFixture import MySql

@unittest.skipUnless(subprocess.run(
    ["docker", "ps", "--format", "{{.Names}}"], capture_output=True,
    text=True).stdout.find(CONTAINER) >= 0, "vmangos-mysql unavailable")
class NativeLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        docker_target()          # validate image/endpoint before CREATE
        cls.sql = load_sql()
        cls.permanent = load_permanent_sql()
        cls.db = MySql()
        cls.db.create()

    @classmethod
    def tearDownClass(cls):
        cls.db.drop()

    def setUp(self):
        names = ["Alice Smith", "Bob Jones", "Carol Smith", "Alice Jones"]
        tables = [
            "character_asset", "character_action", "mail", "mail_items",
            "character_pet", "pet_aura", "pet_spell", "pet_spell_cooldown",
            "character_social", "guild_eventlog", "character_deleted_items",
            "character_aura", "character_battleground_data", "character_gifts",
            "character_homebind", "character_instance", "group_instance",
            "character_inventory", "character_queststatus", "character_reputation",
            "character_skills", "character_forgotten_skills", "character_spell",
            "character_spell_cooldown", "item_instance",
            "character_name_ownership", "characters",
        ]
        self.db.sql("SET FOREIGN_KEY_CHECKS=0; " + "; ".join(
            f"DELETE FROM `{table}`" for table in tables) +
            "; SET FOREIGN_KEY_CHECKS=1;")
        self.guard, self.release, self.tombstone, self.restore, self.claim, self.checks = self.sql
        self.when = 1700000000

    def guard_sql(self, guid=1, account=7, name="Alice Smith", generation=3):
        return bind(self.guard, guid, account, name, generation, guid, name)

    def release_sql(self, guid=1, name="Alice Smith"):
        return bind(self.release, guid, name)

    def tombstone_sql(self, guid=1, account=7, name="Alice Smith", generation=3):
        return bind(self.tombstone, self.when, guid, account, name, generation + 1)

    def restore_sql(self, guid=1, generation=4, name="Alice Smith",
                    account=7, target="Alice Smith", target_account=7,
                    when=None):
        return bind(self.restore, target, target_account, guid, generation,
                    self.when if when is None else when, account, name, guid)

    def claim_sql(self, name="Alice Smith", guid=1):
        return bind(self.claim, name, guid)

    def audit(self):
        return [int(self.db.one(sql)) for sql in self.checks]

    def seed(self, guid=1, account=7, name="Alice Smith", generation=3):
        self.db.create_character(guid, account, name, generation)

    def guard_row(self, guid=1, account=7, name="Alice Smith", generation=3):
        return self.db.sql(self.guard_sql(guid, account, name, generation) +
                           "; SELECT ROW_COUNT();")

    def test_concurrent_creation_same_name_one_wins(self):
        import concurrent.futures

        def attempt(guid):
            return self.db.sql(f"""
                START TRANSACTION;
                INSERT INTO characters(guid,account,name,identity_generation)
                    VALUES({guid},7,'Alice Smith',1);
                INSERT INTO character_name_ownership VALUES('Alice Smith',{guid});
                SELECT SLEEP(0.1);
                COMMIT;
            """)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt, guid) for guid in (1, 2)]
        outcomes = []
        for future in futures:
            try:
                future.result()
                outcomes.append("ok")
            except RuntimeError:
                outcomes.append("failed")
        self.assertEqual(sorted(outcomes), ["failed", "ok"])
        self.assertEqual(self.db.one(
            "SELECT COUNT(*) FROM characters WHERE name='Alice Smith';"), "1")
        self.assertEqual(self.audit(), [0, 0, 0, 0])

    def test_release_then_later_failure_rolls_back(self):
        self.seed()
        self.db.sql(f"""
            START TRANSACTION;
            {self.guard_sql()};
            {self.release_sql()};
            UPDATE characters SET deleted_time=1700000000
              WHERE guid=1 AND BINARY name=BINARY 'Wrong Name';
            ROLLBACK;
        """)
        self.assertEqual(self.db.one(
            "SELECT CONCAT(name,'|',account,'|',IFNULL(deleted_time,'NULL'),'|',identity_generation) "
            "FROM characters WHERE guid=1;"), "Alice Smith|7|NULL|3")
        self.assertEqual(self.db.one(
            "SELECT COUNT(*) FROM character_name_ownership WHERE guid=1;"), "1")
        self.assertEqual(self.audit(), [0, 0, 0, 0])

    def test_startup_audit_corruption_cases(self):
        self.seed()
        self.db.sql("DELETE FROM character_name_ownership;")
        self.assertNotEqual(self.audit()[0], 0)
        self.db.sql("INSERT INTO character_name_ownership VALUES('Alice Smith',1);")
        self.db.sql("UPDATE character_name_ownership SET canonical_name='Wrong Name';")
        self.assertNotEqual(self.audit()[1], 0)
        self.db.sql("UPDATE character_name_ownership SET canonical_name='Alice Smith';")
        self.db.sql("UPDATE characters SET identity_generation=0;")
        self.assertNotEqual(self.audit()[2], 0)
        self.db.sql("UPDATE characters SET identity_generation=1;")
        self.db.sql("INSERT INTO characters(guid,account,name,identity_generation) "
                    "VALUES(2,7,'Alice Smith',1);")
        self.assertNotEqual(self.audit()[0], 0)

    def permanent_guard(self, guid=1, account=7, name="Alice Smith",
                        generation=3, deleted_time=None):
        if deleted_time is None:
            return bind(self.permanent["active_guard"], generation + 1, guid,
                        account, name, generation, guid, name, guid)
        return bind(self.permanent["tombstone_guard"], generation + 1, guid,
                    generation, deleted_time, account, name, guid, guid)

    def permanent_batch(self, commit, guid=1, account=7, name="Alice Smith",
                        generation=3, deleted_time=None):
        statements = [self.permanent_guard(
            guid, account, name, generation, deleted_time)]
        if deleted_time is None:
            statements.append(bind(self.release, guid, name))
        statements.extend(bind(sql, guid, guid) if sql.count("?") == 2 else bind(sql, guid)
                          for sql in self.permanent["deletes"])
        if deleted_time is None:
            statements.append(bind(self.permanent["active_final"], guid,
                                   account, name, generation + 1))
        else:
            statements.append(bind(self.permanent["tombstone_final"], guid,
                                   generation + 1, deleted_time, account, name))
        return "START TRANSACTION;\n" + ";\n".join(statements) + (
            ";\nCOMMIT;" if commit else ";\nROLLBACK;")

    def seed_related(self):
        self.db.sql("""
            INSERT INTO character_action VALUES(1,2);
            INSERT INTO mail(receiver_guid,id) VALUES(1,9); INSERT INTO mail_items VALUES(1,9);
            INSERT INTO character_pet VALUES(1,5);
            INSERT INTO pet_aura VALUES(5,7); INSERT INTO pet_spell VALUES(5,7);
            INSERT INTO pet_spell_cooldown VALUES(5,7);
            INSERT INTO character_social VALUES(1,2); INSERT INTO character_social VALUES(2,1);
            INSERT INTO guild_eventlog VALUES(1,2); INSERT INTO character_deleted_items VALUES(1,3);
        """)

    def test_permanent_delete_commits_all_rows(self):
        self.seed()
        self.seed_related()
        self.db.sql(self.permanent_batch(True))
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM characters;"), "0")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM character_name_ownership;"), "0")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM character_action;"), "0")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM pet_aura;"), "0")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM pet_spell;"), "0")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM pet_spell_cooldown;"), "0")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM mail;"), "0")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM character_social;"), "0")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM guild_eventlog;"), "0")

    def test_permanent_delete_rolls_back_all_rows(self):
        self.seed()
        self.seed_related()
        self.db.sql(self.permanent_batch(False))
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM characters;"), "1")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM character_name_ownership;"), "1")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM character_action;"), "1")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM mail;"), "1")
        self.assertEqual(self.audit(), [0, 0, 0, 0])

    def test_permanent_delete_fk_failure_rolls_back_all_rows(self):
        self.seed()
        self.seed_related()
        self.db.sql("INSERT INTO character_asset VALUES(1,'retained');")
        with self.assertRaises(RuntimeError):
            self.db.sql(self.permanent_batch(True))
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM characters;"), "1")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM character_name_ownership;"), "1")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM character_action;"), "1")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM character_asset;"), "1")
        self.assertEqual(self.audit(), [0, 0, 0, 0])

    def test_permanent_delete_of_tombstone(self):
        self.seed()
        self.db.sql(f"START TRANSACTION; {self.guard_sql()}; {self.release_sql()}; {self.tombstone_sql()}; COMMIT;")
        self.db.sql(self.permanent_batch(True, generation=4, deleted_time=self.when))
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM characters;"), "0")
        self.assertEqual(self.audit(), [0, 0, 0, 0])

    def test_active_guard_scope_and_claim(self):
        self.seed()
        checks = [
            self.permanent_guard(account=8),
            self.permanent_guard(name="Wrong Name"),
            self.permanent_guard(generation=2),
            bind(self.permanent["active_guard"], 4, 1, 7, "Alice Smith", 3, 1,
                 "Wrong Claim", 1),
        ]
        for guard in checks:
            out = self.db.sql(f"START TRANSACTION; {guard}; SELECT ROW_COUNT(); ROLLBACK;")
            self.assertEqual(out.strip(), "0")
        self.assertEqual(self.db.one(
            "SELECT CONCAT(name,'|',account,'|',identity_generation) FROM characters;"),
            "Alice Smith|7|3")
        self.assertEqual(self.audit(), [0, 0, 0, 0])

    def test_tombstone_retained_claim_and_stale_restore(self):
        self.seed()
        self.db.sql(f"START TRANSACTION; {self.guard_sql()}; {self.release_sql()}; {self.tombstone_sql()}; COMMIT;")
        self.db.sql("INSERT INTO character_name_ownership VALUES('Alice Smith',1);")
        out = self.db.sql(
            f"START TRANSACTION; {self.permanent_guard(generation=4, deleted_time=self.when)}; "
            "SELECT ROW_COUNT(); ROLLBACK;")
        self.assertEqual(out.strip(), "0")
        self.db.sql("DELETE FROM character_name_ownership WHERE guid=1;")
        out = self.db.sql(
            f"START TRANSACTION; {self.permanent_guard(generation=4, deleted_time=self.when)}; "
            "SELECT ROW_COUNT(); ROLLBACK;")
        self.assertEqual(out.strip(), "1")

        self.db.sql(f"START TRANSACTION; {self.restore_sql(generation=4)}; COMMIT;")
        out = self.db.sql(
            f"START TRANSACTION; {self.permanent_guard(generation=4, deleted_time=self.when)}; "
            "SELECT ROW_COUNT(); ROLLBACK;")
        self.assertEqual(out.strip(), "0", "stale tombstone after restore")

    def test_two_tombstones_coexist(self):
        self.seed()
        self.db.create_character(2, 7, "Bob Jones", 3)
        self.db.sql(f"""
            START TRANSACTION;
            {bind(self.guard, 1, 7, 'Alice Smith', 3, 1, 'Alice Smith')};
            {self.release_sql()};
            {self.tombstone_sql()};
            {bind(self.guard, 2, 7, 'Bob Jones', 3, 2, 'Bob Jones')};
            {bind(self.release, 2, 'Bob Jones')};
            {bind(self.tombstone, self.when, 2, 7, 'Bob Jones', 4)};
            COMMIT;
        """)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM characters;"), "2")
        self.assertEqual(self.db.one(
            "SELECT COUNT(*) FROM characters WHERE name='' AND account=0;"), "2")
        self.assertEqual(self.audit(), [0, 0, 0, 0])

    def test_cod_guard_blocks_concurrent_matching_insert(self):
        import threading
        import time

        self.seed()
        cmd = ["docker", "exec", "-i", CONTAINER, "sh", "-c",
               'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8 --batch --raw -N -D '
               + self.db.schema]
        guard = self.permanent_guard()
        holder = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8")
        assert holder.stdin is not None
        holder.stdin.write(
            "SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ;\n"
            "START TRANSACTION;\n" + guard + ";\nDO SLEEP(3);\nROLLBACK;\n")
        holder.stdin.flush()

        result = {}
        def insert():
            time.sleep(0.4)
            try:
                self.db.sql(
                    "SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ;"
                    "SET SESSION innodb_lock_wait_timeout=1;"
                    "START TRANSACTION;"
                    "INSERT INTO mail(receiver_guid,id,has_items,cod) "
                    "VALUES(1,10,1,100); COMMIT;")
                result["outcome"] = "inserted"
            except RuntimeError as error:
                result["outcome"] = str(error)

        thread = threading.Thread(target=insert)
        thread.start()
        thread.join(10)
        holder.communicate(timeout=10)
        self.assertEqual(holder.returncode, 0)
        self.assertNotEqual(result.get("outcome"), "inserted")
        self.assertIn("lock wait timeout", result["outcome"].lower())
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM mail;"), "0")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM characters;"), "1")
    def test_cod_guard_refuses_and_changes_nothing(self):
        self.seed()
        self.db.sql("INSERT INTO mail(receiver_guid,id,has_items,cod) VALUES(1,9,1,100);")
        out = self.db.sql(
            f"START TRANSACTION; {self.permanent_guard()}; SELECT ROW_COUNT(); ROLLBACK;")
        self.assertEqual(out.strip(), "0")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM characters;"), "1")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM mail;"), "1")
        self.assertEqual(self.audit(), [0, 0, 0, 0])
    def test_non_ascii_name_over_25_bytes(self):
        name = "Éowynéowynéo Jacobsmith"
        self.seed(name=name)
        self.assertEqual(self.audit(), [0, 0, 0, 0])
        self.db.sql(self.permanent_batch(True, name=name))
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM characters;"), "0")
        self.assertEqual(self.audit(), [0, 0, 0, 0])
    def test_soft_delete_preserves_cod_mail(self):
        self.seed()
        self.db.sql("INSERT INTO mail(receiver_guid,id,has_items,cod) VALUES(1,9,1,100);")
        self.db.sql(f"""
            START TRANSACTION;
            {self.guard_sql()}; {self.release_sql()}; {self.tombstone_sql()}; COMMIT;
        """)
        self.assertEqual(self.db.one(
            "SELECT CONCAT(name,'|',account,'|',deleted_name) FROM characters;"),
            "|0|Alice Smith")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM mail;"), "1")
        self.assertEqual(self.audit(), [0, 0, 0, 0])
    def test_connection_loss_rolls_back_transaction(self):
        self.seed()
        self.seed_related()
        with self.assertRaises(RuntimeError):
            self.db.sql("""
                START TRANSACTION;
                DELETE FROM character_action WHERE guid=1;
                KILL CONNECTION_ID();
            """)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM characters;"), "1")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM character_action;"), "1")
        self.assertEqual(self.audit(), [0, 0, 0, 0])

    def test_version_and_engine(self):
        self.assertEqual(self.db.one("SELECT VERSION();"), "5.6.51")
        self.assertEqual(self.db.one(
            f"SELECT ENGINE FROM information_schema.TABLES WHERE "
            f"TABLE_SCHEMA='{self.db.schema}' AND TABLE_NAME='characters';"),
            "InnoDB")

    def test_creation_and_audit_zero(self):
        self.seed()
        self.assertEqual(self.audit(), [0, 0, 0, 0])

    def test_soft_delete_transaction(self):
        self.seed()
        out = self.db.sql(f"""
            START TRANSACTION;
            {self.guard_sql()};
            SELECT 'guard', ROW_COUNT();
            {self.release_sql()};
            SELECT 'release', ROW_COUNT();
            {self.tombstone_sql()};
            SELECT 'tombstone', ROW_COUNT();
            COMMIT;
        """)
        self.assertIn("guard\t1", out)
        self.assertIn("release\t1", out)
        self.assertIn("tombstone\t1", out)
        self.assertEqual(self.db.one(
            "SELECT CONCAT(name,'|',account,'|',deleted_name,'|',"
            "deleted_account,'|',identity_generation,'|',deleted_time) "
            "FROM characters WHERE guid=1;"), "|0|Alice Smith|7|4|1700000000")
        self.assertEqual(self.db.one(
            "SELECT COUNT(*) FROM character_name_ownership WHERE guid=1;"), "0")
        self.assertEqual(self.audit(), [0, 0, 0, 0])

    def test_claim_fk_restricts_hard_delete(self):
        self.seed()
        with self.assertRaises(RuntimeError):
            self.db.sql("START TRANSACTION; DELETE FROM characters WHERE guid=1; COMMIT;")
        self.assertEqual(self.audit(), [0, 0, 0, 0])

    def test_name_reuse_and_guid_assets(self):
        self.seed()
        self.db.sql("INSERT INTO character_asset VALUES(1,'original');")
        self.db.sql(f"""
            START TRANSACTION;
            {self.guard_sql()}; {self.release_sql()}; {self.tombstone_sql()}; COMMIT;
        """)
        self.db.create_character(2, 7, "Alice Smith", 1)
        self.db.sql("INSERT INTO character_asset VALUES(2,'reused');")
        self.assertEqual(self.db.one(
            "SELECT GROUP_CONCAT(CONCAT(guid,':',label) ORDER BY guid) FROM character_asset;"),
            "1:original,2:reused")
        self.assertEqual(self.audit(), [0, 0, 0, 0])

    def test_restore_stale_predicates(self):
        self.seed()
        self.db.sql(f"START TRANSACTION; {self.guard_sql()}; {self.release_sql()}; {self.tombstone_sql()}; COMMIT;")
        for changed in [
            self.restore_sql(generation=5),
            self.restore_sql(when=self.when + 1),
            self.restore_sql(account=8),
            self.restore_sql(name="Alicia Smith"),
        ]:
            self.assertEqual(self.db.sql(f"START TRANSACTION; {changed}; SELECT ROW_COUNT(); ROLLBACK;").strip(), "0")

    def test_restore_success_and_conflict(self):
        self.seed()
        self.db.sql(f"START TRANSACTION; {self.guard_sql()}; {self.release_sql()}; {self.tombstone_sql()}; COMMIT;")
        self.db.sql("START TRANSACTION; "
                    "INSERT INTO characters(guid,account,name,identity_generation) "
                    "VALUES(2,7,'Bob Jones',1); "
                    "INSERT INTO character_name_ownership VALUES('Bob Jones',2); COMMIT;")
        with self.assertRaises(RuntimeError):
            self.db.sql(f"""
                START TRANSACTION;
                {self.restore_sql(target="Bob Jones")};
                {self.claim_sql("Bob Jones")};
                COMMIT;
            """)
        self.assertEqual(self.db.one(
            "SELECT CONCAT(name,'|',deleted_time,'|',identity_generation) "
            "FROM characters WHERE guid=1;"), "|1700000000|4")
        self.assertEqual(self.db.one(
            "SELECT COUNT(*) FROM character_name_ownership WHERE guid=2;"), "1")
        self.assertEqual(self.audit(), [0, 0, 0, 0])

    def test_restore_after_name_available(self):
        self.seed()
        self.db.sql(f"START TRANSACTION; {self.guard_sql()}; {self.release_sql()}; {self.tombstone_sql()}; COMMIT;")
        self.db.sql(f"START TRANSACTION; {self.restore_sql()}; {self.claim_sql()}; COMMIT;")
        self.assertEqual(self.db.one(
            "SELECT CONCAT(name,'|',account,'|',IFNULL(deleted_time,'NULL'),'|',identity_generation) "
            "FROM characters WHERE guid=1;"), "Alice Smith|7|NULL|5")
        self.assertEqual(self.audit(), [0, 0, 0, 0])

    def test_generation_max_rejects_restore(self):
        self.seed(1, 7, "Alice Smith", 18446744073709551615)
        # A maximum-generation active character cannot be guarded/incremented.
        self.assertEqual(self.guard_row(1, 7, "Alice Smith", 18446744073709551615).splitlines()[-1], "0")
        # A maximum-generation tombstone cannot be restored.
        self.db.sql("SET FOREIGN_KEY_CHECKS=0; UPDATE characters SET deleted_name=name, "
                    "deleted_account=account, deleted_time=1700000000, name='', account=0 "
                    "WHERE guid=1; DELETE FROM character_name_ownership; SET FOREIGN_KEY_CHECKS=1;")
        self.assertEqual(self.db.sql(
            f"START TRANSACTION; {self.restore_sql(generation=18446744073709551615)}; SELECT ROW_COUNT(); ROLLBACK;").strip(), "0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
