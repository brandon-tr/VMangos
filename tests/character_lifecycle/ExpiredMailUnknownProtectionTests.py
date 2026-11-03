"""Runtime protection check for ProcessExpiredMail uncertainty.

The real operation body is extracted; admission and block calls are instrumented
to prove the recovery record is registered before the local admission ends.
"""

import pathlib
import subprocess
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "src/game/Mail/MailPersistence.cpp").read_text(encoding="utf-8")


def extract():
    start = SOURCE.index("MailPersistenceStatus ProcessExpiredMail(")
    end = SOURCE.index("void Mail::prepareTemplateItems", start)
    return SOURCE[start:end]


PREAMBLE = r'''
#include <cstdint>
#include <iostream>
#include <map>
#include <string>
#include <vector>

using uint8 = unsigned char;
using uint32 = unsigned int;
using uint64 = unsigned long long;
using time_t = long long;

enum { HIGHGUID_PLAYER = 0 };
enum { DAY = 86400 };
enum MailCheckMask { MAIL_CHECK_MASK_RETURNED = 2 };
enum class MailPersistenceStatus { Committed, Rejected, OutcomeUnknown };
enum class CharacterRecoveryKind { WholeSave };
enum class AccountLifecycleKind { Mutation, Deletion };
enum class SqlAffectedRowsPolicy { Any, ExactlyOne };

struct ObjectGuid {
    uint32 value = 0;
    ObjectGuid() = default;
    ObjectGuid(int, uint32 v) : value(v) {}
    uint32 GetCounter() const { return value; }
};

struct CharacterRecoveryRecord {
    CharacterRecoveryKind kind = CharacterRecoveryKind::WholeSave;
    std::vector<uint32> guids;
    std::vector<uint32> accountIds;
    uint64 expectedGeneration = 0;
    std::string detail;
};

std::vector<CharacterRecoveryRecord> g_recovery;
bool g_admissionHeld = false;
bool g_blockHeld = false;
int g_admitCalls = 0;

bool BlockCharacterRecovery(CharacterRecoveryRecord const& record) {
    g_recovery.push_back(record);
    g_blockHeld = g_admissionHeld;
    return true;
}

class AccountLifecycleLease;
struct CharacterPersistenceAdmission {
    bool Admit(std::vector<uint32> const&, AccountLifecycleKind,
               AccountLifecycleLease const* = nullptr) {
        ++g_admitCalls;
        g_admissionHeld = true;
        return true;
    }
    ~CharacterPersistenceAdmission() { g_admissionHeld = false; }
};

struct SqlStatementID {};
struct SqlBatch;
struct SqlStatement {
    template<typename T> SqlStatement& addUInt32(T) { return *this; }
    template<typename T> SqlStatement& addUInt64(T) { return *this; }
    bool AppendToCheckedBatch(SqlBatch&, SqlAffectedRowsPolicy) { return true; }
};
struct Database {
    SqlStatement CreateStatement(SqlStatementID&, char const*) { return {}; }
};
Database CharacterDatabase;
struct SqlBatch {
    Database* GetDatabase() { return &CharacterDatabase; }
};
struct SqlBatchResult {};
MailPersistenceStatus g_status = MailPersistenceStatus::Committed;
MailPersistenceStatus ClassifyMailBatch(SqlBatchResult const&) { return g_status; }

struct CharacterPersistenceContext {
    CharacterPersistenceContext(Database&, uint32) {}
    SqlBatch& Batch() { return batch; }
    SqlBatchResult Execute() { return {}; }
    SqlBatch batch;
};

bool AppendItemOwnershipTransfer(CharacterPersistenceContext&, uint32, uint32, uint32) { return true; }
bool AppendItemDisposal(CharacterPersistenceContext&, uint32, uint32) { return true; }

struct MailItemInfo { uint32 itemGuid = 0; uint32 itemId = 0; };
struct Mail {
    uint32 messageID = 0;
    uint32 sender = 0;
    ObjectGuid receiverGuid;
    uint32 itemTextId = 0;
};

struct ObjectMgrStub {
    std::map<uint32, uint32> accounts;
    uint32 GetPlayerAccountIdByGUID(ObjectGuid guid) {
        auto itr = accounts.find(guid.GetCounter());
        return itr == accounts.end() ? 0 : itr->second;
    }
} sObjectMgr;
'''

MAIN = r'''
int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";
    Mail mail;
    mail.messageID = 9; mail.sender = 700; mail.itemTextId = 0;
    mail.receiverGuid = ObjectGuid(HIGHGUID_PLAYER, 500);
    sObjectMgr.accounts[500] = 42;
    sObjectMgr.accounts[700] = 43;

    if (scenario == "unknown") {
        g_status = MailPersistenceStatus::OutcomeUnknown;
        MailPersistenceStatus status = ProcessExpiredMail(
            mail, {}, false, 1000);
        if (status != MailPersistenceStatus::OutcomeUnknown) return 1;
        if (g_recovery.size() != 2) return 2;
        if (!g_blockHeld) return 3;
        if (g_admissionHeld) return 4;
        return 0;
    }
    if (scenario == "unknown_return") {
        g_status = MailPersistenceStatus::OutcomeUnknown;
        MailPersistenceStatus status = ProcessExpiredMail(
            mail, {}, true, 1000);
        if (status != MailPersistenceStatus::OutcomeUnknown) return 1;
        if (g_recovery.size() != 2 || !g_blockHeld) return 2;
        return 0;
    }
    if (scenario == "rejected") {
        g_status = MailPersistenceStatus::Rejected;
        MailPersistenceStatus status = ProcessExpiredMail(
            mail, {}, false, 1000);
        if (status != MailPersistenceStatus::Rejected) return 1;
        if (!g_recovery.empty()) return 2;
        return 0;
    }
    std::cerr << "unknown\n";
    return 2;
}
'''


class ExpiredMailUnknownProtectionTests(unittest.TestCase):
    def test_recovery_is_registered_under_admission(self):
        with tempfile.TemporaryDirectory(prefix="fn05h-expiry-protect-") as td:
            work = pathlib.Path(td)
            (work / "probe.cpp").write_text(
                PREAMBLE + "\n" + extract() + "\n" + MAIN, encoding="utf-8")
            (work / "CMakeLists.txt").write_text(textwrap.dedent("""
                cmake_minimum_required(VERSION 3.20)
                project(expiry_protect_probe CXX)
                add_executable(probe probe.cpp)
                set_property(TARGET probe PROPERTY CXX_STANDARD 14)
            """), encoding="utf-8")
            build = work / "build"
            subprocess.run(["cmake", "-S", str(work), "-B", str(build),
                            "-DCMAKE_BUILD_TYPE=Release"], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            built = subprocess.run(["cmake", "--build", str(build),
                                    "--config", "Release", "--parallel", "1"],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True)
            self.assertEqual(built.returncode, 0, built.stdout)
            exe = build / "Release" / "probe.exe"
            if not exe.exists():
                exe = build / "probe"
            for scenario in ("unknown", "unknown_return", "rejected"):
                run = subprocess.run([str(exe), scenario], stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True)
                self.assertEqual(run.returncode, 0, scenario + "\n" + run.stdout)


if __name__ == "__main__":
    unittest.main()
