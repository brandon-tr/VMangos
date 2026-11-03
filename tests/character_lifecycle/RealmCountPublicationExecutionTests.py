#!/usr/bin/env python3
"""Executes actual World::UpdateRealmCharCount with the real recovery registry."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORLD = ROOT / "src" / "game" / "World.cpp"


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

#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>
#include <cstddef>
#include <utility>

using uint32 = unsigned int;
using uint64 = unsigned long long;
using uint8 = unsigned char;

uint32 realmID = 1;

enum class SqlWriteOutcome { NotExecuted, Succeeded, DuplicateKey, Failed, OutcomeUnknown };
enum class SqlBatchStage { NotStarted, Begin, Statement, RowCount, Commit, Complete };
struct SqlBatchResult {
    SqlWriteOutcome outcome = SqlWriteOutcome::Succeeded;
    SqlBatchStage stage = SqlBatchStage::Complete;
    std::size_t statementIndex = 0;
};
extern bool g_appendOk;
extern SqlBatchResult g_result;

struct SqlStmtFieldData {
    uint32 value = 0;
    explicit SqlStmtFieldData(uint32 value_) : value(value_) {}
};

enum class SqlAffectedRowsPolicy { Any, ExactlyOne };
struct SqlStatementID {};
extern bool g_countOk;
extern uint32 g_count;

extern std::vector<std::string> g_events;
extern int g_countCalls, g_createCalls, g_appendCalls, g_execCalls;
extern std::string g_sql;
extern std::vector<uint32> g_bound;
extern SqlAffectedRowsPolicy g_policy;
extern uint32* g_outputPtr;
extern uint32 g_sentinel;
extern bool g_outputAtExecution;
struct SqlCheckedBatch {
    bool valid = false;
    std::vector<uint32> values;
};

struct SqlStatement {
    std::vector<uint32> values;
    void addUInt32(uint32 value) { values.push_back(value); }
    bool AppendToCheckedBatch(SqlCheckedBatch& batch, SqlAffectedRowsPolicy policy) {
        ++g_appendCalls; g_bound = values; g_policy = policy;
        g_events.push_back("append");
        batch.valid = g_appendOk; batch.values = values;
        return g_appendOk;
    }
};

struct Database {
    std::vector<uint32> executions;
    SqlCheckedBatch CreateCheckedBatch() { return {}; }
    SqlStatement CreateStatement(SqlStatementID&, char const* sql) {
        ++g_createCalls; g_sql = sql; g_events.push_back("create"); return {};
    }
    SqlBatchResult DirectExecuteCheckedBatch(SqlCheckedBatch batch) {
        ++g_execCalls; g_events.push_back("execute");
        g_outputAtExecution = g_outputPtr && *g_outputPtr == g_sentinel;
        if (batch.valid)
            executions.push_back(batch.values.size() > 1 ? batch.values[1] : 0);
        return g_result;
    }
};
Database LoginDatabase;

struct AccountMgr {
    bool TryGetCharactersCount(uint32, uint32& count) {
        ++g_countCalls; g_events.push_back("count");
        if (!g_countOk) return false;
        count = g_count;
        return true;
    }
};
AccountMgr sAccountMgr;

bool g_countOk = true;
uint32 g_count = 0;
bool g_appendOk = true;
SqlBatchResult g_result = {SqlWriteOutcome::Succeeded, SqlBatchStage::Complete, 0};
std::vector<std::string> g_events;
int g_countCalls = 0, g_createCalls = 0, g_appendCalls = 0, g_execCalls = 0;
std::string g_sql;
std::vector<uint32> g_bound;
SqlAffectedRowsPolicy g_policy = SqlAffectedRowsPolicy::Any;
uint32* g_outputPtr = nullptr;
uint32 g_sentinel = 0;
bool g_outputAtExecution = false;

struct World {
    bool UpdateRealmCharCount(uint32 accountId,
                              AccountLifecycleLease const* lease,
                              uint32* publishedCount);
};
'''

MAIN = r'''
static int failures = 0;
static void check(bool value, std::string const& label) {
    if (!value) {
        std::cerr << "FAIL " << label << "\n";
        ++failures;
    }
}
static void reset() {
    g_countOk = true;
    g_count = 0;
    g_appendOk = true;
    g_result = {SqlWriteOutcome::Succeeded, SqlBatchStage::Complete, 0};
    LoginDatabase.executions.clear();
    g_events.clear(); g_countCalls = g_createCalls = g_appendCalls = g_execCalls = 0;
    g_sql.clear(); g_bound.clear(); g_policy = SqlAffectedRowsPolicy::Any;
    g_outputPtr = nullptr; g_sentinel = 0; g_outputAtExecution = false;
}

static bool g_ownedAdmissionRan = false;
static bool admissible(uint32 account) {
    return GetAccountLifecycleRegistry().TryAcquire(
        {account}, AccountLifecycleKind::Mutation) != nullptr;
}
static void armOutput(uint32& output, uint32 sentinel) {
    g_outputPtr = &output; g_sentinel = sentinel; g_outputAtExecution = false;
}
int main() {
    World world;
    uint32 output = 0;

    // Borrowed Mutation admission succeeds without reacquisition.
    {
        reset();
        auto lease = GetAccountLifecycleRegistry().TryAcquire(
            {1001}, AccountLifecycleKind::Mutation);
        g_count = 3;
        output = 99;
        armOutput(output, 99);
        check(world.UpdateRealmCharCount(1001, lease.get(), &output),
              "borrowed mutation succeeds");
        check(output == 3, "authoritative count assigned");
        check(LoginDatabase.executions == std::vector<uint32>{1001},
              "one checked publication");
        check(g_countCalls == 1 && g_createCalls == 1 &&
              g_appendCalls == 1 && g_execCalls == 1,
              "one count/assembly/execution");
        check(g_sql.find("REPLACE INTO `realmcharacters`") != std::string::npos,
              "realmcharacters REPLACE SQL");
        check(g_bound == std::vector<uint32>{3, 1001, 1},
              "bound count/account/realm order");
        check(g_policy == SqlAffectedRowsPolicy::Any, "append policy Any");
        check(g_events == std::vector<std::string>{"count", "create", "append", "execute"},
              "publication event order");
        lease.reset();
        auto again = GetAccountLifecycleRegistry().TryAcquire(
            {1001}, AccountLifecycleKind::Mutation);
        check(again != nullptr, "owned publication releases admission");
    }

    // Internally acquired admission success.
    {
        reset();
        g_count = 2;
        output = 123;
        g_outputPtr = &output;
        g_sentinel = 123;
        g_outputAtExecution = false;
        check(world.UpdateRealmCharCount(1099, nullptr, &output),
              "owned mutation succeeds");
        g_ownedAdmissionRan = true;
        check(output == 2 && g_execCalls == 1 && g_countCalls == 1,
              "owned publication count/exec");
        check(g_outputAtExecution,
              "output remains sentinel during owned execution");
        auto again = GetAccountLifecycleRegistry().TryAcquire(
            {1099}, AccountLifecycleKind::Mutation);
        check(again != nullptr, "owned admission releases");
        std::cout << "OWNED_ADMISSION_PASS\n";
    }

    // Busy admission: no SQL.
    {
        reset();
        auto holder = GetAccountLifecycleRegistry().TryAcquire(
            {1002}, AccountLifecycleKind::Mutation);
        output = 77;
        check(!world.UpdateRealmCharCount(1002, nullptr, &output),
              "busy admission rejected");
        check(LoginDatabase.executions.empty() && output == 77 &&
              g_countCalls == 0 && g_createCalls == 0 &&
              g_appendCalls == 0 && g_execCalls == 0,
              "busy admission no count/SQL/output");
        holder.reset();
    }

    // Foreign registry lease.
    {
        reset();
        AccountLifecycleRegistry other;
        auto foreign = other.TryAcquire({1003}, AccountLifecycleKind::Mutation);
        output = 66;
        check(!world.UpdateRealmCharCount(1003, foreign.get(), &output),
              "foreign lease rejected");
        check(LoginDatabase.executions.empty() && output == 66 &&
              g_countCalls == 0 && g_createCalls == 0 &&
              g_appendCalls == 0 && g_execCalls == 0,
              "foreign lease no count/SQL/output");
    }

    // Deletion lease cannot publish.
    {
        reset();
        auto deletion = GetAccountLifecycleRegistry().TryAcquire(
            {1004}, AccountLifecycleKind::Deletion);
        output = 55;
        check(!world.UpdateRealmCharCount(1004, deletion.get(), &output),
              "deletion lease rejected");
        check(LoginDatabase.executions.empty() && output == 55 &&
              g_countCalls == 0 && g_createCalls == 0 &&
              g_appendCalls == 0 && g_execCalls == 0,
              "deletion lease no count/SQL/output");
    }

    // Count failure records recovery and never executes.
    {
        reset();
        auto lease = GetAccountLifecycleRegistry().TryAcquire(
            {1005}, AccountLifecycleKind::Mutation);
        g_countOk = false;
        output = 44;
        check(!world.UpdateRealmCharCount(1005, lease.get(), &output),
              "count failure rejected");
        check(LoginDatabase.executions.empty() && output == 44,
              "count failure no SQL/output");
        check(IsAccountRecoveryBlocked(1005), "count failure recovery block");
        lease.reset();
        check(!admissible(1005), "count failure survives lease release");
    }

    // Append failure records recovery and never executes.
    {
        reset();
        auto lease = GetAccountLifecycleRegistry().TryAcquire(
            {1006}, AccountLifecycleKind::Mutation);
        g_appendOk = false;
        output = 33;
        check(!world.UpdateRealmCharCount(1006, lease.get(), &output),
              "append failure rejected");
        check(LoginDatabase.executions.empty() && output == 33,
              "append failure no SQL/output");
        check(IsAccountRecoveryBlocked(1006), "append failure recovery block");
        lease.reset();
        check(!admissible(1006), "append failure survives lease release");
    }

    // Execution outcomes.
    uint32 account = 1007;
    for (SqlBatchResult result : {
             SqlBatchResult{SqlWriteOutcome::Failed, SqlBatchStage::Statement, 0},
             SqlBatchResult{SqlWriteOutcome::Succeeded, SqlBatchStage::Statement, 0},
             SqlBatchResult{SqlWriteOutcome::OutcomeUnknown, SqlBatchStage::Commit, 0}}) {
        reset();
        auto lease = GetAccountLifecycleRegistry().TryAcquire(
            {account}, AccountLifecycleKind::Mutation);
        g_result = result;
        output = 22;
        armOutput(output, 22);
        check(!world.UpdateRealmCharCount(account, lease.get(), &output),
              "failed publication rejected");
        check(output == 22 && IsAccountRecoveryBlocked(account),
              "failed publication output/recovery");
        check(g_outputAtExecution,
              "output sentinel during failed execution");
        lease.reset();
        check(!admissible(account), "execution failure survives lease release");
        ++account;
    }

    // Unrelated account remains admissible.
    {
        auto unrelated = GetAccountLifecycleRegistry().TryAcquire(
            {1100}, AccountLifecycleKind::Mutation);
        check(unrelated != nullptr, "unrelated account admissible");
    }

    check(g_ownedAdmissionRan, "owned admission scenario executed");

    if (failures) {
        std::cerr << "failures=" << failures << "\n";
        return 1;
    }
    std::cout << "realm publication orchestration: PASS\n";
    return 0;
}
'''


@unittest.skipUnless(shutil.which("cmake"), "cmake not available")
class RealmCountPublicationExecutionTests(unittest.TestCase):
    def test_parameter_order_variant_is_sensitive(self):
        source = WORLD.read_text(encoding="utf-8")
        body = extract(source, "bool World::UpdateRealmCharCount")
        old = "    statement.addUInt32(charCount);\n    statement.addUInt32(accountId);"
        self.assertIn(old, body)
        variant = body.replace(
            old,
            "    statement.addUInt32(accountId);\n    statement.addUInt32(charCount);",
            1)
        program = PREAMBLE + "\n" + variant + "\n" + MAIN
        with tempfile.TemporaryDirectory(prefix="fn05h-v30-publish-variant-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(realm_publication_variant CXX)\n"
                "set(CMAKE_CXX_STANDARD 14)\n"
                f"add_executable(publish probe.cpp "
                f"\"{(ROOT / 'src/game/Database/CharacterPersistenceRecovery.cpp').as_posix()}\")\n"
                f"target_include_directories(publish PRIVATE "
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
            executable = build / "Release" / "publish.exe"
            if not executable.exists():
                executable = build / "publish"
            result = subprocess.run([str(executable)], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0,
                                "swapped binding variant unexpectedly passed: " +
                                result.stdout + result.stderr)
    def test_actual_publication_body(self):
        source = WORLD.read_text(encoding="utf-8")
        body = extract(source, "bool World::UpdateRealmCharCount")
        program = PREAMBLE + "\n" + body + "\n" + MAIN
        with tempfile.TemporaryDirectory(prefix="fn05h-v29-publish-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(realm_publication_exec CXX)\n"
                "set(CMAKE_CXX_STANDARD 14)\n"
                "add_executable(publish probe.cpp\n"
                f"  \"{(ROOT / 'src/game/Database/CharacterPersistenceRecovery.cpp').as_posix()}\")\n"
                f"target_include_directories(publish PRIVATE "
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
            executable = build / "Release" / "publish.exe"
            if not executable.exists():
                executable = build / "publish"
            result = subprocess.run([str(executable)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("realm publication orchestration: PASS", result.stdout)
            self.assertIn("OWNED_ADMISSION_PASS", result.stdout)


    def test_owned_token_variant_is_sensitive(self):
        source = WORLD.read_text(encoding="utf-8")
        body = extract(source, "bool World::UpdateRealmCharCount")
        old = "        owned = GetAccountLifecycleRegistry().TryAcquire(\n            {accountId}, AccountLifecycleKind::Mutation);"
        self.assertIn(old, body)
        variant = body.replace(old, "        owned = nullptr;", 1)
        program = PREAMBLE + "\n" + variant + "\n" + MAIN
        with tempfile.TemporaryDirectory(prefix="fn05h-v31-owned-variant-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(realm_owned_variant CXX)\n"
                "set(CMAKE_CXX_STANDARD 14)\n"
                f"add_executable(publish probe.cpp "
                f"\"{(ROOT / 'src/game/Database/CharacterPersistenceRecovery.cpp').as_posix()}\")\n"
                f"target_include_directories(publish PRIVATE "
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
            executable = build / "Release" / "publish.exe"
            if not executable.exists():
                executable = build / "publish"
            result = subprocess.run([str(executable)], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0,
                                "no-token variant unexpectedly passed: " +
                                result.stdout + result.stderr)
            self.assertIn("FAIL owned mutation succeeds",
                          result.stdout + result.stderr,
                          result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
