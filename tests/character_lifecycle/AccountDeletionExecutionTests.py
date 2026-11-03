#!/usr/bin/env python3
"""Actual AccountMgr::DeleteAccount with the real recovery registry.

Each scenario is a fresh process: no registry reset hook exists or is added.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACCOUNT = ROOT / "src/game/AccountMgr.cpp"
RECOVERY = ROOT / "src/game/Database/CharacterPersistenceRecovery.cpp"


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
                return source[start:index + 1]
    raise AssertionError("unterminated " + signature)


PREAMBLE = r'''
#include "Database/CharacterPersistenceRecovery.h"
#include <algorithm>
#include <cstddef>
#include <cstdlib>
#include <deque>
#include <iostream>
#include <limits>
#include <memory>
#include <string>
#include <utility>
#include <vector>

struct Field {
    std::string value; bool null_value = false;
    Field() {}
    Field(std::string v, bool n) : value(std::move(v)), null_value(n) {}
    char const* GetString() const { return null_value ? nullptr : value.c_str(); }
    std::string GetCppString() const { return null_value ? std::string() : value; }
    uint32 GetUInt32() const { return uint32(std::strtoull(value.c_str(), nullptr, 10)); }
    uint64 GetUInt64() const { return std::strtoull(value.c_str(), nullptr, 10); }
};
struct Reply {
    std::string label; bool null_result = false; int reported_rows = 0;
    std::vector<std::vector<Field>> rows;
};
struct QueryResult {
    Reply reply; int index = -1; std::vector<Field> current;
    explicit QueryResult(Reply v) : reply(std::move(v)) {}
    int GetRowCount() const { return reply.reported_rows; }
    Field* Fetch() {
        if (index < 0) index = 0;
        if (index >= int(reply.rows.size())) return nullptr;
        current = reply.rows[index]; return current.data();
    }
    bool NextRow() { ++index; return index < int(reply.rows.size()); }
};
struct SqlStatement; struct SqlStatementID;
std::deque<Reply> g_replies;
std::vector<std::string> g_events;
struct Database {
    SqlStatement CreateStatement(SqlStatementID&, char const*);
    std::unique_ptr<QueryResult> PQuery(char const*, ...) {
        if (g_replies.empty()) { std::cerr << "unexpected query\n"; std::abort(); }
        Reply reply = std::move(g_replies.front()); g_replies.pop_front();
        g_events.push_back(reply.label);
        if (reply.null_result) return nullptr;
        return std::unique_ptr<QueryResult>(new QueryResult(std::move(reply)));
    }
};
Database CharacterDatabase, LoginDatabase;
struct SqlStatementID {}; struct SqlCheckedBatch {};
enum class SqlAffectedRowsPolicy { Any, ExactlyOne };
extern bool g_character_assembly, g_login_assembly, g_fail_second_login_append;
extern int g_login_append_calls;
struct SqlStatement {
    bool character = false;
    void addUInt32(uint32) {}
    bool AppendToCheckedBatch(SqlCheckedBatch&, SqlAffectedRowsPolicy) {
        if (character) return g_character_assembly;
        ++g_login_append_calls;
        if (g_fail_second_login_append && g_login_append_calls == 2) return false;
        return g_login_assembly;
    }
};
SqlStatement Database::CreateStatement(SqlStatementID&, char const* sql) {
    SqlStatement statement;
    statement.character = (&*this == &CharacterDatabase);
    return statement;
}
enum class SqlWriteOutcome { Succeeded, Failed, DuplicateKey, OutcomeUnknown };
enum class SqlBatchStage { NotStarted, Begin, Statement, RowCount, Commit, Complete };
struct SqlBatchResult { SqlWriteOutcome outcome = SqlWriteOutcome::Succeeded;
    SqlBatchStage stage = SqlBatchStage::Complete; std::size_t statementIndex = 0; };
SqlBatchResult g_character_result, g_login_result;
bool g_character_assembly = true, g_login_assembly = true;
bool g_fail_second_login_append = false;
int g_login_append_calls = 0;
struct CharacterPersistenceContext {
    Database& database; SqlCheckedBatch batch;
    CharacterPersistenceContext(Database& db, uint32) : database(db) {}
    SqlCheckedBatch& Batch() { return batch; }
    SqlBatchResult Execute() {
        if (&database == &CharacterDatabase) { g_events.push_back("character-commit"); return g_character_result; }
        g_events.push_back("login-commit"); return g_login_result;
    }
};
struct ObjectGuid { uint32 guid = 0; ObjectGuid() {} ObjectGuid(int, uint32 v) : guid(v) {} };
enum { HIGHGUID_PLAYER = 0 };
struct CharacterIdentitySnapshot { uint32 guid = 0, accountId = 0; std::string canonicalName; uint64 generation = 0; };
struct CharacterDeletionSnapshot { CharacterIdentitySnapshot identity; uint64 deletedTime = 0; };
struct DeleteCall { uint32 guid = 0, account = 0; CharacterDeletionSnapshot snapshot; };
std::vector<DeleteCall> g_delete_calls;
std::vector<bool> g_delete_results;
std::size_t g_delete_index = 0;
bool g_borrowed_deletion_lease = true;
struct Player {
    static bool DeleteFromDB(ObjectGuid guid, uint32 account, bool, bool,
                             CharacterDeletionSnapshot const* snapshot,
                             AccountLifecycleLease const* lease) {
        g_events.push_back("delete:" + std::to_string(guid.guid));
        DeleteCall call; call.guid = guid.guid; call.account = account;
        if (snapshot) call.snapshot = *snapshot;
        g_delete_calls.push_back(call);
        if (!lease || lease->GetKind() != AccountLifecycleKind::Deletion ||
            !lease->Covers(account) ||
            !GetAccountLifecycleRegistry().Allows(*lease, account, AccountLifecycleKind::Deletion))
            g_borrowed_deletion_lease = false;
        bool result = g_delete_index < g_delete_results.size() ? g_delete_results[g_delete_index] : true;
        ++g_delete_index; return result;
    }
};
struct ObjectAccessor { static void KickPlayer(ObjectGuid) {} };
enum { LOG_BASIC = 0, LOG_LVL_ERROR = 0 };
struct Log { template<typename... Args> void Out(Args&&...) {} };
Log sLog;
enum AccountOpResult { AOR_OK = 0, AOR_NAME_NOT_EXIST, AOR_DB_INTERNAL_ERROR };
struct AccountMgr { static AccountOpResult DeleteAccount(uint32); };
'''

MAIN = r'''
static int failures = 0;
static void check(bool value, std::string const& label) {
    if (!value) { std::cerr << "FAIL " << label << "\n"; ++failures; }
}
static Reply count(std::string label, std::string value) {
    Reply r; r.label = std::move(label); r.reported_rows = 1;
    r.rows.push_back({Field{value, false}}); return r;
}
static Reply null_reply(std::string label) {
    Reply r; r.label = std::move(label); r.null_result = true; return r;
}
static Reply rows(std::string label, std::vector<std::vector<Field>> values, int reported = -1) {
    Reply r; r.label = std::move(label); r.rows = std::move(values);
    r.reported_rows = reported < 0 ? int(r.rows.size()) : reported; return r;
}
static SqlBatchResult complete() { return {SqlWriteOutcome::Succeeded, SqlBatchStage::Complete, 0}; }
static void reset() {
    g_replies.clear(); g_events.clear(); g_delete_calls.clear(); g_delete_results.clear();
    g_delete_index = 0; g_borrowed_deletion_lease = true;
    g_character_assembly = g_login_assembly = true;
    g_fail_second_login_append = false; g_login_append_calls = 0;
    g_character_result = g_login_result = complete();
}
static bool admissible(uint32 account) {
    return GetAccountLifecycleRegistry().TryAcquire({account}, AccountLifecycleKind::Mutation) != nullptr;
}
static bool hasAccountRecord(uint32 account) {
    for (CharacterRecoveryRecord const& record : GetCharacterRecoveryRecords())
        if (std::find(record.accountIds.begin(), record.accountIds.end(), account) != record.accountIds.end())
            return true;
    return false;
}
static std::vector<Field> activeRow(uint32 guid, uint32 account, char const* name = "Alice Smith") {
    return {Field{std::to_string(guid), false}, Field{std::to_string(account), false},
            Field{name, false}, Field{"3", false}, Field{"0", false},
            Field{"0", false}, Field{"", false}};
}
static void oneCharacter(uint32 account) {
    g_replies.push_back(count("account-count", "1"));
    g_replies.push_back(count("character-count", "1"));
    g_replies.push_back(rows("character-list", {activeRow(1, account)}));
    g_replies.push_back(count("remaining-count", "0"));
    g_delete_results.assign(1, true);
}
static void emptyAccountReply() {
    g_replies.push_back(count("account-count", "1"));
    g_replies.push_back(count("character-count", "0"));
    g_replies.push_back(count("remaining-count", "0"));
}
int main(int argc, char** argv) {
    reset(); std::string scenario = argc > 1 ? argv[1] : "";
    if (scenario == "success_retire") {
        uint32 a = 2010; emptyAccountReply();
        check(AccountMgr::DeleteAccount(a) == AOR_OK, "success");
        check(!admissible(a), "success retirement");
        check(GetCharacterRecoveryRecords().empty(), "success no recovery");
    } else if (scenario == "missing") {
        uint32 a = 2011; g_replies.push_back(count("account-count", "0"));
        check(AccountMgr::DeleteAccount(a) == AOR_NAME_NOT_EXIST, "missing");
        check(admissible(a), "missing admissible");
        check(GetCharacterRecoveryRecords().empty(), "missing no record");
    } else if (scenario == "early_query_failure") {
        uint32 a = 2012; g_replies.push_back(null_reply("account-count"));
        check(AccountMgr::DeleteAccount(a) == AOR_DB_INTERNAL_ERROR, "early error");
        check(admissible(a), "early admissible");
        check(!hasAccountRecord(a), "early no recovery");
    } else if (scenario == "character_false") {
        uint32 a = 2013; oneCharacter(a); g_delete_results[0] = false;
        check(AccountMgr::DeleteAccount(a) == AOR_DB_INTERNAL_ERROR, "char false");
        check(hasAccountRecord(a) && !admissible(a), "char false blocks");
        check(g_borrowed_deletion_lease, "deletion lease validated");
    } else if (scenario == "second_character_false") {
        uint32 a = 2014;
        g_replies.push_back(count("account-count", "1"));
        g_replies.push_back(count("character-count", "2"));
        g_replies.push_back(rows("character-list", {activeRow(1, a), activeRow(2, a, "Bob Jones")}));
        g_replies.push_back(count("remaining-count", "0"));
        g_delete_results = {true, false};
        check(AccountMgr::DeleteAccount(a) == AOR_DB_INTERNAL_ERROR, "second false");
        check(!admissible(a), "second false blocks");
    } else if (scenario == "remaining_failure") {
        uint32 a = 2015; oneCharacter(a); g_replies.pop_back();
        g_replies.push_back(null_reply("remaining-count"));
        check(AccountMgr::DeleteAccount(a) == AOR_DB_INTERNAL_ERROR, "remaining error");
        check(!admissible(a), "remaining blocks");
    } else if (scenario == "remaining_malformed") {
        uint32 a = 2031; oneCharacter(a); g_replies.pop_back();
        g_replies.push_back(count("remaining-count", "x"));
        check(AccountMgr::DeleteAccount(a) == AOR_DB_INTERNAL_ERROR, "remaining malformed");
        check(!admissible(a), "remaining malformed blocks");
    } else if (scenario == "remaining_nonzero") {
        uint32 a = 2032; oneCharacter(a); g_replies.pop_back();
        g_replies.push_back(count("remaining-count", "1"));
        check(AccountMgr::DeleteAccount(a) == AOR_DB_INTERNAL_ERROR, "remaining nonzero");
        check(!admissible(a), "remaining nonzero blocks");
    } else if (scenario == "tutorial_after_dispatch") {
        uint32 a = 2016; oneCharacter(a); g_character_assembly = false;
        check(AccountMgr::DeleteAccount(a) == AOR_DB_INTERNAL_ERROR, "tutorial error");
        check(!admissible(a), "tutorial after dispatch blocks");
    } else if (scenario == "tutorial_known_no_dispatch") {
        uint32 a = 2017; emptyAccountReply(); g_character_assembly = false;
        check(AccountMgr::DeleteAccount(a) == AOR_DB_INTERNAL_ERROR, "known error");
        check(admissible(a), "known no-dispatch admissible");
    } else if (scenario == "tutorial_unknown_no_dispatch") {
        uint32 a = 2018; emptyAccountReply();
        g_character_result = {SqlWriteOutcome::OutcomeUnknown, SqlBatchStage::Commit, 0};
        check(AccountMgr::DeleteAccount(a) == AOR_DB_INTERNAL_ERROR, "unknown error");
        check(!admissible(a), "unknown no-dispatch blocks");
    } else if (scenario == "login_append_first") {
        uint32 a = 2019; emptyAccountReply(); g_login_assembly = false;
        check(AccountMgr::DeleteAccount(a) == AOR_DB_INTERNAL_ERROR, "login append1");
        check(!admissible(a), "login append1 blocks");
    } else if (scenario == "login_append_second") {
        uint32 a = 2020; emptyAccountReply(); g_fail_second_login_append = true;
        check(AccountMgr::DeleteAccount(a) == AOR_DB_INTERNAL_ERROR, "login append2");
        check(!admissible(a), "login append2 blocks");
    } else if (scenario == "login_execution") {
        uint32 a = 2021; emptyAccountReply(); g_login_result = {SqlWriteOutcome::Failed, SqlBatchStage::Statement, 1};
        check(AccountMgr::DeleteAccount(a) == AOR_DB_INTERNAL_ERROR, "login fail");
        check(!admissible(a), "login fail blocks");
    } else if (scenario == "login_incomplete") {
        uint32 a = 2022; emptyAccountReply(); g_login_result = {SqlWriteOutcome::Succeeded, SqlBatchStage::Statement, 1};
        check(AccountMgr::DeleteAccount(a) == AOR_DB_INTERNAL_ERROR, "login incomplete");
        check(!admissible(a), "login incomplete blocks");
    } else if (scenario == "login_unknown") {
        uint32 a = 2023; emptyAccountReply(); g_login_result = {SqlWriteOutcome::OutcomeUnknown, SqlBatchStage::Commit, 1};
        check(AccountMgr::DeleteAccount(a) == AOR_DB_INTERNAL_ERROR, "login unknown");
        check(!admissible(a), "login unknown blocks");
    } else if (scenario == "blocked_before") {
        uint32 a = 2024;
        BlockCharacterRecovery(CharacterRecoveryRecord{CharacterRecoveryKind::AccountDeletion, {}, {a}, 0, "blocked"});
        check(AccountMgr::DeleteAccount(a) == AOR_DB_INTERNAL_ERROR, "blocked");
        check(g_events.empty(), "blocked no query");
    } else if (scenario == "success_with_character") {
        uint32 a = 2025; oneCharacter(a);
        check(AccountMgr::DeleteAccount(a) == AOR_OK, "character success");
        check(g_events == std::vector<std::string>{"account-count", "character-count",
              "character-list", "delete:1", "remaining-count", "character-commit", "login-commit"},
              "event order");
        check(g_borrowed_deletion_lease, "valid deletion lease");
        check(!admissible(a), "retirement after login commit");
    } else { std::cerr << "unknown scenario\n"; return 2; }
    if (failures) { std::cerr << "failures=" << failures << "\n"; return 1; }
    std::cout << "scenario " << scenario << ": PASS\n"; return 0;
}
'''

SCENARIOS = [
    "success_retire", "missing", "early_query_failure", "character_false",
    "second_character_false", "remaining_failure", "remaining_malformed",
    "remaining_nonzero", "tutorial_after_dispatch",
    "tutorial_known_no_dispatch", "tutorial_unknown_no_dispatch",
    "login_append_first", "login_append_second", "login_execution",
    "login_incomplete", "login_unknown", "blocked_before",
    "success_with_character",
]


@unittest.skipUnless(shutil.which("cmake"), "cmake not available")
class AccountDeletionExecutionTests(unittest.TestCase):
    def _build_and_run(self, body, parser, scenario):
        program = PREAMBLE + "\n" + parser + "\n" + body + "\n" + MAIN
        with tempfile.TemporaryDirectory(prefix="fn05h-v30-variant-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(account_variant_exec CXX)\n"
                "set(CMAKE_CXX_STANDARD 14)\n"
                f"add_executable(account probe.cpp \"{RECOVERY.as_posix()}\")\n"
                f"target_include_directories(account PRIVATE "
                f"\"{(ROOT / 'src/game').as_posix()}\" "
                f"\"{(ROOT / 'src/shared').as_posix()}\" "
                f"\"{(ROOT / 'src/framework').as_posix()}\")\n",
                encoding="utf-8")
            (temp / "probe.cpp").write_text(program, encoding="utf-8")
            build = temp / "build"
            subprocess.run(["cmake", "-S", str(temp), "-B", str(build),
                            "-DCMAKE_BUILD_TYPE=Release"], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            built = subprocess.run(["cmake", "--build", str(build),
                                    "--config", "Release", "--parallel", "2"],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True)
            self.assertEqual(built.returncode, 0, built.stdout)
            executable = build / "Release" / "account.exe"
            if not executable.exists():
                executable = build / "account"
            return subprocess.run([str(executable), scenario], text=True,
                                  capture_output=True)

    def test_recovery_variants_are_sensitive(self):
        source = ACCOUNT.read_text(encoding="utf-8")
        parser = extract(source, "static bool ParseAccountDeletionCount")
        body = extract(source, "AccountOpResult AccountMgr::DeleteAccount")
        variants = [
            ("remaining_failure", "FAIL remaining blocks",
             'return fail(deletionAttempted || cleanupCommitted,\n'
             '                    "remaining character count query failed after cleanup");',
             'return AOR_DB_INTERNAL_ERROR;'),
            ("login_append_first", "FAIL login append1 blocks",
             'return fail(true, "realm-character cleanup assembly failed after tutorial commit");',
             'return AOR_DB_INTERNAL_ERROR;'),
        ]
        for scenario, expected, old, new in variants:
            self.assertIn(old, body)
            variant = body.replace(old, new, 1)
            result = self._build_and_run(variant, parser, scenario)
            self.assertNotEqual(result.returncode, 0,
                                scenario + " variant unexpectedly passed: " +
                                result.stdout + result.stderr)
            self.assertIn(expected, result.stdout + result.stderr,
                          scenario + " missing expected failure label")
    def test_actual_delete_account_body_real_registry(self):
        source = ACCOUNT.read_text(encoding="utf-8")
        parser = extract(source, "static bool ParseAccountDeletionCount")
        body = extract(source, "AccountOpResult AccountMgr::DeleteAccount")
        program = PREAMBLE + "\n" + parser + "\n" + body + "\n" + MAIN
        with tempfile.TemporaryDirectory(prefix="fn05h-v30-account-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(account_deletion_exec CXX)\n"
                "set(CMAKE_CXX_STANDARD 14)\n"
                f"add_executable(account probe.cpp \"{RECOVERY.as_posix()}\")\n"
                f"target_include_directories(account PRIVATE "
                f"\"{(ROOT / 'src/game').as_posix()}\" "
                f"\"{(ROOT / 'src/shared').as_posix()}\" "
                f"\"{(ROOT / 'src/framework').as_posix()}\")\n",
                encoding="utf-8")
            (temp / "probe.cpp").write_text(program, encoding="utf-8")
            build = temp / "build"
            subprocess.run(["cmake", "-S", str(temp), "-B", str(build),
                            "-DCMAKE_BUILD_TYPE=Release"], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            built = subprocess.run(["cmake", "--build", str(build),
                                    "--config", "Release", "--parallel", "2"],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True)
            self.assertEqual(built.returncode, 0, built.stdout)
            executable = build / "Release" / "account.exe"
            if not executable.exists():
                executable = build / "account"
            for scenario in SCENARIOS:
                result = subprocess.run([str(executable), scenario],
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0,
                                 scenario + "\n" + result.stdout + result.stderr)
                self.assertIn("scenario " + scenario + ": PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
