#!/usr/bin/env python3
"""Actual restoration helper with real registry and captured restoration request."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMANDS = ROOT / "src/game/Commands/CharacterCommands.cpp"
IDENTITY_H = ROOT / "src/game/Database/CharacterIdentityPersistence.h"
IDENTITY_CPP = ROOT / "src/game/Database/CharacterIdentityPersistence.cpp"
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
#include <iostream>
#include <memory>
#include <string>

using uint32 = unsigned int;
using uint64 = unsigned long long;
using uint8 = unsigned char;

enum { LANG_CHARACTER_DELETED_SKIP_ACCOUNT, LANG_CHARACTER_DELETED_SKIP_FULL,
       LANG_CHARACTER_DELETED_SKIP_NAME };
enum { LOG_BASIC=0, LOG_LVL_ERROR=0 };
struct Log { template<typename... A> void Out(A&&...) {} } sLog;

struct DeletedInfo {
    uint32 lowguid = 77;
    uint32 accountId = 3020;
    uint32 originalAccountId = 3010;
    std::string name = "Alice Jones";
    std::string accountName = "target";
    std::string originalName = "Old Smith";
    uint64 originalGeneration = 9;
    uint64 originalDeletedTime = 12345;
};

struct CharacterRestoration {
    uint32 guid = 0, targetAccountId = 0;
    uint64 expectedGeneration = 0;
    std::string deletedName, targetCanonicalName;
    uint32 deletedAccountId = 0;
    uint64 deletedTime = 0;
};

struct SqlStatementID {};
struct SqlCheckedBatch {};
enum class SqlAffectedRowsPolicy { Any, ExactlyOne, AtMostOne };
struct SqlStatement {
    bool AppendToCheckedBatch(SqlCheckedBatch&, SqlAffectedRowsPolicy) { return true; }
};
struct Database {
    SqlStatement CreateStatement(SqlStatementID&, char const*) { return {}; }
} CharacterDatabase;
struct SqlWriteOutcome { enum E { Succeeded, Failed, DuplicateKey, OutcomeUnknown }; };
enum class SqlBatchStage { NotStarted, Begin, Statement, RowCount, Commit, Complete };
struct SqlWriteResult { int outcome = 0; };
struct SqlBatchResult {
    int outcome = 0; SqlBatchStage stage = SqlBatchStage::Complete;
    std::size_t statementIndex = 0; bool rollbackAttempted = false;
    SqlWriteResult rollbackResult; SqlWriteResult cause;
};
// NAME_STATUS
// CLASSIFIER
static bool available(uint32 account);
bool g_publishProtected = false, g_cacheProtected = false;
int g_execCalls = 0, g_countReadCalls = 0, g_publishCalls = 0, g_cacheCalls = 0;
std::vector<std::string> g_events;
bool g_builderProtected = false, g_execProtected = false;
int g_appendCalls = 0;
int g_messages = 0;
struct CharacterPersistenceContext {
    CharacterPersistenceContext(Database&, uint32) {}
    SqlCheckedBatch& Batch() { static SqlCheckedBatch b; return b; }
    SqlBatchResult Execute() { ++g_execCalls; g_events.push_back("execute");
        g_execProtected = !available(3010) && !available(3020); return g_batch; }
    static SqlBatchResult g_batch;
};
SqlBatchResult CharacterPersistenceContext::g_batch;
CharacterRestoration g_restorationRequest;
bool g_appendOk = true, g_countOk = true, g_cacheOk = true, g_nameTaken = false;
bool g_canonicalOk = true, g_publishOk = true;
bool g_builderExcluded = false, g_countExcluded = false;
bool g_cacheExcluded = false;
uint32 g_count = 0;
static bool available(uint32 account);
bool AppendCharacterRestoration(Database&, CharacterPersistenceContext&,
                                CharacterRestoration const& request) {
    ++g_appendCalls; g_restorationRequest = request; g_events.push_back("builder");
    g_builderProtected = !available(3010) && !available(3020); return g_appendOk;
}
bool isCanonicalFullPlayerName(std::string const&) { return g_canonicalOk; }
struct AccountMgr {
    bool TryGetCharactersCount(uint32, uint32& out) {
        ++g_countReadCalls; g_events.push_back("count_read");
        if (!g_countOk) return false; out = g_count; return true;
    }
} sAccountMgr;
struct ObjectMgr {
    bool g_name = false;
    uint32 GetPlayerGuidByName(std::string const&) { return g_nameTaken ? 1 : 0; }
    bool LoadPlayerCacheData(uint32) { ++g_cacheCalls; g_cacheProtected = !available(3010) && !available(3020); g_events.push_back("cache");
        return g_cacheOk; }
} sObjectMgr;
struct World {
    bool UpdateRealmCharCount(uint32 account, AccountLifecycleLease const* lease, uint32* = nullptr) {
        ++g_publishCalls; g_events.push_back("realm_count");
        g_publishProtected = !available(3010) && !available(3020);
        if (lease) g_countLeaseValid = lease->Covers(3010) && lease->Covers(3020) && lease->IsActive();
        return g_publishOk;
    }
    bool g_countLeaseValid = false;
} sWorld;
static bool available(uint32 account) {
    return GetAccountLifecycleRegistry().TryAcquire(
        {account}, AccountLifecycleKind::Mutation) != nullptr;
}
struct ChatHandler {
    bool HandleCharacterDeletedRestoreHelper(DeletedInfo const&, AccountLifecycleLease const* = nullptr);
    template<typename... A> void PSendSysMessage(A&&...) { ++g_messages; }
};
'''

MAIN = r"""
static int failures = 0;
static void check(bool value, std::string const& label) {
    if (!value) { std::cerr << "FAIL " << label << "\n"; ++failures; }
}
static bool record(uint32 guid, uint32 a1, uint32 a2, uint64 gen) {
    for (auto const& r : GetCharacterRecoveryRecords())
        if (r.kind == CharacterRecoveryKind::Restoration &&
            r.guids.size() == 1 && r.guids[0] == guid &&
            r.accountIds.size() == 2 && r.accountIds[0] == a1 && r.accountIds[1] == a2 &&
            r.expectedGeneration == gen) return true;
    return false;
}
int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";
    DeletedInfo info; ChatHandler handler;
    bool borrowed = false, busy = false;
    AccountLifecycleLeasePtr lease;
    if (scenario == "borrowed_both") { lease = GetAccountLifecycleRegistry().TryAcquire({3010,3020}, AccountLifecycleKind::Mutation); borrowed = true; }
    else if (scenario == "borrowed_original") { lease = GetAccountLifecycleRegistry().TryAcquire({3010}, AccountLifecycleKind::Mutation); borrowed = true; }
    else if (scenario == "borrowed_target") { lease = GetAccountLifecycleRegistry().TryAcquire({3020}, AccountLifecycleKind::Mutation); borrowed = true; }
    else if (scenario == "borrowed_wrong_kind") { lease = GetAccountLifecycleRegistry().TryAcquire({3010,3020}, AccountLifecycleKind::Deletion); borrowed = true; }
    else if (scenario == "busy_original") { lease = GetAccountLifecycleRegistry().TryAcquire({3010}, AccountLifecycleKind::Mutation); busy = true; }
    else if (scenario == "busy_target") { lease = GetAccountLifecycleRegistry().TryAcquire({3020}, AccountLifecycleKind::Mutation); busy = true; }
    bool ok = false;
    if (scenario == "borrowed_foreign") {
        AccountLifecycleRegistry foreign;
        auto foreignLease = foreign.TryAcquire({3010,3020}, AccountLifecycleKind::Mutation);
        ok = handler.HandleCharacterDeletedRestoreHelper(info, foreignLease.get());
        check(!ok && g_countReadCalls == 0 && g_appendCalls == 0 && g_execCalls == 0 &&
              foreignLease->IsActive(), "foreign lease rejected before effects");
    } else if (scenario == "borrowed_both") {
        ok = handler.HandleCharacterDeletedRestoreHelper(info, lease.get());
        check(ok && lease->IsActive() && lease->Covers(3010) && lease->Covers(3020),
              "borrowed both succeeds/retained");
        check(!available(3010) && !available(3020), "borrowed keeps both excluded");
        lease.reset();
        check(available(3010) && available(3020), "release restores both");
    } else if (scenario == "borrowed_original" || scenario == "borrowed_target") {
        ok = handler.HandleCharacterDeletedRestoreHelper(info, lease.get());
        check(!ok && g_countReadCalls == 0 && g_appendCalls == 0 && g_execCalls == 0 &&
              lease->IsActive(), scenario + " rejected before effects");
        lease.reset();
        check(available(3010) && available(3020), "release restores both");
    } else if (scenario == "borrowed_wrong_kind") {
        ok = handler.HandleCharacterDeletedRestoreHelper(info, lease.get());
        check(!ok && g_countReadCalls == 0 && g_appendCalls == 0 && lease->IsActive(),
              "wrong kind rejected before effects");
        lease.reset();
    } else if (scenario == "busy_original" || scenario == "busy_target") {
        uint32 held = scenario == "busy_original" ? 3010 : 3020;
        uint32 free = scenario == "busy_original" ? 3020 : 3010;
        check(lease && available(free), "independent busy setup");
        ok = handler.HandleCharacterDeletedRestoreHelper(info, nullptr);
        check(!ok && g_countReadCalls == 0 && g_appendCalls == 0 && g_execCalls == 0 &&
              lease->IsActive() && available(free), "busy rejects before effects");
        lease.reset();
        check(available(3010) && available(3020), "busy release restores both");
    } else {
        if (scenario == "success") {
            ok = handler.HandleCharacterDeletedRestoreHelper(info, nullptr);
            std::vector<std::string> expected = {"count_read","builder","execute","realm_count","cache"};
            check(ok && g_events == expected && g_appendCalls == 1 && g_execCalls == 1 &&
                  g_countReadCalls == 1 && g_publishCalls == 1 && g_cacheCalls == 1,
                  "owned success exact event order/counts");
            check(g_restorationRequest.guid == 77 && g_restorationRequest.expectedGeneration == 9 &&
                  g_restorationRequest.deletedName == "Old Smith" &&
                  g_restorationRequest.deletedAccountId == 3010 &&
                  g_restorationRequest.deletedTime == 12345 &&
                  g_restorationRequest.targetCanonicalName == "Alice Jones" &&
                  g_restorationRequest.targetAccountId == 3020, "exact request");
            check(g_builderProtected && g_execProtected && g_publishProtected && g_cacheProtected,
                  "both accounts protected at every boundary");
            check(available(3010) && available(3020) && !record(77,3010,3020,9),
                  "no recovery and both released after return");
        } else if (scenario == "same_account") {
            info.accountId = 3010; info.accountName = "same";
            ok = handler.HandleCharacterDeletedRestoreHelper(info, nullptr);
            check(ok && g_execCalls == 1 && available(3010), "same-account normalized success");
        } else if (scenario == "missing_account") {
            info.accountName.clear();
            ok = handler.HandleCharacterDeletedRestoreHelper(info, nullptr);
            check(!ok && g_countReadCalls == 0 && g_appendCalls == 0, "missing account early reject");
        } else if (scenario == "count_read_failure") {
            g_countOk = false;
            ok = handler.HandleCharacterDeletedRestoreHelper(info, nullptr);
            check(!ok && g_appendCalls == 0 && g_execCalls == 0 && g_publishCalls == 0,
                  "count read failure early reject");
        } else if (scenario == "count_limit") {
            g_count = 10;
            ok = handler.HandleCharacterDeletedRestoreHelper(info, nullptr);
            check(!ok && g_appendCalls == 0 && g_execCalls == 0, "count limit early reject");
        } else if (scenario == "invalid_name") {
            g_canonicalOk = false;
            ok = handler.HandleCharacterDeletedRestoreHelper(info, nullptr);
            check(!ok && g_appendCalls == 0 && g_execCalls == 0, "invalid name early reject");
        } else if (scenario == "name_collision") {
            g_nameTaken = true;
            ok = handler.HandleCharacterDeletedRestoreHelper(info, nullptr);
            check(!ok && g_appendCalls == 0 && g_execCalls == 0, "collision early reject");
        } else if (scenario == "assembly_failure") {
            g_appendOk = false;
            ok = handler.HandleCharacterDeletedRestoreHelper(info, nullptr);
            check(!ok && g_execCalls == 0 && g_publishCalls == 0, "assembly failure stops");
        } else if (scenario == "known_rejection") {
            CharacterPersistenceContext::g_batch.outcome = SqlWriteOutcome::Failed;
            CharacterPersistenceContext::g_batch.stage = SqlBatchStage::Statement;
            ok = handler.HandleCharacterDeletedRestoreHelper(info, nullptr);
            check(!ok && g_execCalls == 1 && g_publishCalls == 0 && !record(77,3010,3020,9) &&
                  available(3010) && available(3020), "known rejection no publication/recovery");
        } else if (scenario == "duplicate") {
            CharacterPersistenceContext::g_batch.outcome = SqlWriteOutcome::DuplicateKey;
            CharacterPersistenceContext::g_batch.stage = SqlBatchStage::Statement;
            CharacterPersistenceContext::g_batch.statementIndex = 1;
            CharacterPersistenceContext::g_batch.cause.outcome = SqlWriteOutcome::DuplicateKey;
            CharacterPersistenceContext::g_batch.rollbackAttempted = true;
            CharacterPersistenceContext::g_batch.rollbackResult.outcome = SqlWriteOutcome::Succeeded;
            ok = handler.HandleCharacterDeletedRestoreHelper(info, nullptr);
            check(!ok && g_publishCalls == 0 && !record(77,3010,3020,9) &&
                  available(3010) && available(3020), "duplicate no publication/recovery");
        } else if (scenario == "unknown") {
            CharacterPersistenceContext::g_batch.outcome = SqlWriteOutcome::OutcomeUnknown;
            ok = handler.HandleCharacterDeletedRestoreHelper(info, nullptr);
            check(!ok && g_publishCalls == 0 && g_cacheCalls == 0 &&
                  record(77,3010,3020,9), "unknown exact recovery/no publication");
            check(!available(3010) && !available(3020), "unknown blocks both after release");
        } else if (scenario == "count_failure") {
            g_publishOk = false;
            ok = handler.HandleCharacterDeletedRestoreHelper(info, nullptr);
            check(!ok && g_execCalls == 1 && g_publishCalls == 1 && g_cacheCalls == 0 &&
                  record(77,3010,3020,9), "count failure recovery/no cache");
            check(!available(3010) && !available(3020), "count failure blocks both");
        } else if (scenario == "cache_failure") {
            g_cacheOk = false;
            ok = handler.HandleCharacterDeletedRestoreHelper(info, nullptr);
            check(!ok && g_execCalls == 1 && g_publishCalls == 1 && g_cacheCalls == 1 &&
                  record(77,3010,3020,9), "cache failure recovery");
            check(!available(3010) && !available(3020), "cache failure blocks both");
        } else { std::cerr << "unknown\n"; return 2; }
    }
    if (failures) { std::cerr << "failures=" << failures << "\n"; return 1; }
    std::cout << "restoration " << scenario << ": PASS\n"; return 0;
}
"""


@unittest.skipUnless(shutil.which("cmake"), "cmake not available")
class CharacterDeletionRestorationExecutionTests(unittest.TestCase):
    def test_actual_restoration_helper(self):
        source = COMMANDS.read_text(encoding="utf-8")
        helper = extract(source, "bool ChatHandler::HandleCharacterDeletedRestoreHelper")
        status_enum = extract(IDENTITY_H.read_text(encoding="utf-8"),
                              "enum class CharacterNameWriteStatus") + ";"
        classifier = extract(IDENTITY_CPP.read_text(encoding="utf-8"),
                             "CharacterNameWriteStatus ClassifyCharacterNameClaim")
        program = (PREAMBLE.replace("// NAME_STATUS", status_enum)
                   .replace("// CLASSIFIER", classifier) + "\n" + helper + "\n" + MAIN)
        with tempfile.TemporaryDirectory(prefix="fn05h-v59-restore-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(restore_exec CXX)\n"
                "set(CMAKE_CXX_STANDARD 14)\n"
                f"add_executable(probe probe.cpp \"{RECOVERY.as_posix()}\")\n"
                f"target_include_directories(probe PRIVATE "
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
            executable = build / "Release" / "probe.exe"
            if not executable.exists():
                executable = build / "probe"
            failures = []
            for scenario in ["success", "same_account", "missing_account",
                             "count_read_failure", "count_limit", "invalid_name",
                             "name_collision", "assembly_failure", "known_rejection",
                             "duplicate", "unknown", "count_failure", "cache_failure",
                             "busy_original", "busy_target", "borrowed_both",
                             "borrowed_original", "borrowed_target",
                             "borrowed_wrong_kind", "borrowed_foreign"]:
                result = subprocess.run([str(executable), scenario], text=True,
                                        capture_output=True, timeout=30)
                if result.returncode != 0 or (
                        "restoration " + scenario + ": PASS") not in result.stdout:
                    failures.append(scenario + "\n" + result.stdout + result.stderr)
            self.assertFalse(failures, "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
