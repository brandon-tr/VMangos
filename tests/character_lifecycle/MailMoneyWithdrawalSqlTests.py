import re
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "src" / "game" / "Database" / "CharacterIdentityPersistence.cpp"
CONTEXT = ROOT / "src" / "game" / "Database" / "CharacterPersistenceContext.cpp"


def function_body(source, signature):
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
    raise AssertionError("unterminated function: " + signature)


def statement_sql(body, marker):
    start = body.index(marker)
    end = body.index(";", start)
    literals = re.findall(r'"((?:[^"\\]|\\.)*)"', body[start:end])
    if not literals:
        raise AssertionError("no SQL literals after " + marker)
    return "".join(literals)


def adapt(sql):
    sql = re.sub(r"BINARY `name`=BINARY \?", "CAST(`name` AS BLOB)=CAST(? AS BLOB)", sql)
    sql = re.sub(r"BINARY `canonical_name`=BINARY \?", "CAST(`canonical_name` AS BLOB)=CAST(? AS BLOB)", sql)
    return sql


def load_sql():
    builder = BUILDER.read_text(encoding="utf-8")
    context = CONTEXT.read_text(encoding="utf-8")
    builder_body = function_body(builder, "bool AppendMailMoneyWithdrawal(")
    guard_body = function_body(context, "bool CharacterPersistenceContext::AppendIdentityGuards(")
    claim = statement_sql(builder_body, "SqlStatement claim =")
    balance = statement_sql(builder_body, "SqlStatement balance =")
    guard = statement_sql(guard_body, "SqlStatement guard =")
    return adapt(guard), adapt(claim), adapt(balance)


class MailMoneyWithdrawalSqlTests(unittest.TestCase):
    def setUp(self):
        self.guard, self.claim, self.balance = load_sql()
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript("""
            CREATE TABLE characters(
                guid INTEGER PRIMARY KEY, account INTEGER, name TEXT,
                identity_generation INTEGER, deleted_time INTEGER,
                money INTEGER);
            CREATE TABLE character_name_ownership(
                canonical_name BLOB PRIMARY KEY, guid INTEGER UNIQUE);
            CREATE TABLE mail(
                id INTEGER PRIMARY KEY, receiver_guid INTEGER, money INTEGER);
        """)
        self.seed()

    def seed(self):
        self.conn.execute(
            "INSERT INTO characters VALUES (1,7,'Alice Smith',3,NULL,100)")
        self.conn.execute(
            "INSERT INTO character_name_ownership VALUES ('Alice Smith',1)")
        self.conn.execute("INSERT INTO mail VALUES (42,1,25)")
        self.conn.commit()

    def guard_params(self, generation=3, name="Alice Smith", guid=1, account=7):
        return (guid, account, name, generation, guid, name)

    def run_guard(self, generation=3, name="Alice Smith", guid=1, account=7):
        return self.conn.execute(
            self.guard, self.guard_params(generation, name, guid, account)).rowcount

    def run_claim(self, mail=42, receiver=1, amount=25):
        return self.conn.execute(self.claim, (mail, receiver, amount)).rowcount

    def run_balance(self, amount=125, guid=1, account=7,
                    name="Alice Smith", generation=4):
        return self.conn.execute(
            self.balance, (amount, guid, account, name, generation)).rowcount

    def snapshot(self):
        character = self.conn.execute(
            "SELECT identity_generation, money, name FROM characters WHERE guid=1").fetchone()
        mail = self.conn.execute("SELECT money FROM mail WHERE id=42").fetchone()
        return character, mail

    def test_successful_transfer(self):
        self.conn.execute("BEGIN")
        self.assertEqual(self.run_guard(), 1)
        self.assertEqual(self.run_claim(), 1)
        self.assertEqual(self.run_balance(), 1)
        self.conn.commit()
        self.assertEqual(self.snapshot(), ((4, 125, "Alice Smith"), (0,)))

    def test_stale_revision(self):
        self.conn.execute("BEGIN")
        self.assertEqual(self.conn.execute(
            self.guard, self.guard_params(generation=2)).rowcount, 0)
        self.conn.rollback()
        self.assertEqual(self.snapshot(), ((3, 100, "Alice Smith"), (25,)))

    def test_ownership_mismatch(self):
        self.conn.execute("DELETE FROM character_name_ownership")
        self.conn.execute(
            "INSERT INTO character_name_ownership VALUES ('Other Name',1)")
        self.conn.commit()
        self.conn.execute("BEGIN")
        self.assertEqual(self.run_guard(), 0)
        self.conn.rollback()
        self.assertEqual(self.snapshot(), ((3, 100, "Alice Smith"), (25,)))

    def test_wrong_receiver(self):
        self.conn.execute("BEGIN")
        self.assertEqual(self.run_guard(), 1)
        self.assertEqual(self.run_claim(receiver=2), 0)
        self.conn.rollback()
        self.assertEqual(self.snapshot(), ((3, 100, "Alice Smith"), (25,)))

    def test_wrong_amount(self):
        self.conn.execute("BEGIN")
        self.assertEqual(self.run_guard(), 1)
        self.assertEqual(self.run_claim(amount=24), 0)
        self.conn.rollback()
        self.assertEqual(self.snapshot(), ((3, 100, "Alice Smith"), (25,)))

    def test_repeated_withdrawal(self):
        self.conn.execute("BEGIN")
        self.assertEqual(self.run_guard(), 1)
        self.assertEqual(self.run_claim(), 1)
        self.assertEqual(self.run_balance(), 1)
        self.conn.commit()
        self.conn.execute("BEGIN")
        self.assertEqual(self.run_guard(generation=4), 1)
        self.assertEqual(self.run_claim(), 0)
        self.conn.rollback()
        self.assertEqual(self.snapshot(), ((4, 125, "Alice Smith"), (0,)))

    def test_failure_after_claim_rolls_back(self):
        self.conn.executescript("""
            CREATE TRIGGER fail_balance BEFORE UPDATE ON characters
            WHEN NEW.money <> OLD.money
            BEGIN SELECT RAISE(ABORT, 'balance failure'); END;
        """)
        self.conn.execute("BEGIN")
        self.assertEqual(self.run_guard(), 1)
        self.assertEqual(self.run_claim(), 1)
        with self.assertRaises(sqlite3.DatabaseError):
            self.run_balance()
        self.conn.rollback()
        self.assertEqual(self.snapshot(), ((3, 100, "Alice Smith"), (25,)))

    def test_unchanged_balance_is_allowed(self):
        self.conn.execute("BEGIN")
        self.assertEqual(self.run_guard(), 1)
        self.assertEqual(self.run_claim(), 1)
        self.run_balance(amount=100)
        self.conn.commit()
        self.assertEqual(self.snapshot(), ((4, 100, "Alice Smith"), (0,)))


if __name__ == "__main__":
    unittest.main()
