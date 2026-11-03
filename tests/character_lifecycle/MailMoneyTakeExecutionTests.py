#!/usr/bin/env python3
"""Executable regression for the checked mail-money withdrawal wrapper.

The real `TakeMailMoney` body and the guarded `AppendMailMoneyWithdrawal` body are
extracted from MailPersistence.cpp / CharacterIdentityPersistence.cpp and
compiled against recording stubs.  The scenarios cover the commit-only balance
and mail publication, zero money, cap rejection, and the known/unknown failure
outcomes.  Database execution is stubbed by design.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAIL = ROOT / "src/game/Mail/MailPersistence.cpp"
IDENTITY = ROOT / "src/game/Database/CharacterIdentityPersistence.cpp"

PREAMBLE = r'''
#include <algorithm>
#include <ctime>
#include <functional>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <string>
#include <vector>

using uint8 = unsigned char;
using uint32 = unsigned int;
using uint64 = unsigned long long;
using int32 = signed int;
using int64 = signed long long;

enum MailMessageType { MAIL_NORMAL = 0 };
enum MailState { MAIL_STATE_UNCHANGED = 1, MAIL_STATE_CHANGED = 2, MAIL_STATE_DELETED = 3 };
enum MailCheckMask { MAIL_CHECK_MASK_NONE = 0, MAIL_CHECK_MASK_READ = 1, MAIL_CHECK_MASK_RETURNED = 2, MAIL_CHECK_MASK_COPIED = 4, MAIL_CHECK_MASK_HAS_BODY = 8, MAIL_CHECK_MASK_COD_PAYMENT = 16 };
enum { HIGHGUID_PLAYER = 0 };

struct ObjectGuid {
    uint32 value = 0;
    ObjectGuid() {}
    ObjectGuid(int, uint32 v) : value(v) {}
    uint32 GetCounter() const { return value; }
    bool IsPlayer() const { return true; }
    bool operator==(ObjectGuid const& other) const { return value == other.value; }
    bool operator!=(ObjectGuid const& other) const { return value != other.value; }
};

struct Mail {
    uint32 messageID = 0;
    uint8 messageType = MAIL_NORMAL;
    uint32 sender = 0;
    ObjectGuid receiverGuid;
    uint32 itemTextId = 0;
    bool has_items = false;
    time_t expire_time = 0;
    time_t deliver_time = 0;
    uint32 money = 0;
    uint32 COD = 0;
    uint32 checked = 0;
    std::vector<uint32> removedItems;
    MailState state = MAIL_STATE_UNCHANGED;
    bool HasItems() const { return has_items; }
    bool RemoveItem(uint32) { return true; }
};

enum class CharacterRecoveryKind { WholeSave, Rename, Restoration, SoftDeletion, PermanentDeletion, AccountDeletion };
struct CharacterRecoveryRecord {
    CharacterRecoveryKind kind = CharacterRecoveryKind::WholeSave;
    std::vector<uint32> guids;
    std::vector<uint32> accountIds;
    uint64 expectedGeneration = 0;
    std::string detail;
};
std::vector<CharacterRecoveryRecord> g_recoveryRecords;
bool BlockCharacterRecovery(CharacterRecoveryRecord const& record) {
    g_recoveryRecords.push_back(record);
    return true;
}

struct CharacterIdentitySnapshot {
    uint32 guid = 0;
    uint32 accountId = 0;
    std::string canonicalName;
    uint64 generation = 0;
};

struct MailMoneyWithdrawal {
    CharacterIdentitySnapshot identity;
    uint32 mailId = 0;
    uint32 amount = 0;
    uint32 currentBalance = 0;
    uint32 maximumBalance = 0;
};

enum class SqlAffectedRowsPolicy { Any, ExactlyOne };
enum class SqlWriteOutcome { NotExecuted, Succeeded, DuplicateKey, Failed, OutcomeUnknown };
enum class SqlBatchStage { NotStarted, Begin, Statement, RowCount, Commit, Complete };
struct SqlWriteResult { SqlWriteOutcome outcome = SqlWriteOutcome::Succeeded; };
struct SqlBatchResult {
    SqlWriteOutcome outcome = SqlWriteOutcome::Succeeded;
    SqlBatchStage stage = SqlBatchStage::Complete;
    std::size_t statementIndex = 0;
    bool rollbackAttempted = false;
    SqlWriteResult rollbackResult;
};
struct BoundWrite {
    std::string sql;
    std::vector<std::string> params;
    SqlAffectedRowsPolicy policy;
};
struct Database;
struct SqlCheckedBatch {
    Database* db = nullptr;
    std::vector<BoundWrite> writes;
    Database* GetDatabase() const { return db; }
    bool Empty() const { return writes.empty(); }
};
struct SqlStatementID {};
struct SqlStatement {
    std::string sql;
    std::vector<std::string> params;
    SqlStatement& addUInt32(uint32 value) { params.push_back(std::to_string(value)); return *this; }
    SqlStatement& addUInt64(uint64 value) { params.push_back(std::to_string(value)); return *this; }
    SqlStatement& addString(std::string const& value) { params.push_back(value); return *this; }
    bool AppendToCheckedBatch(SqlCheckedBatch& batch, SqlAffectedRowsPolicy policy);
};
struct Database {
    SqlStatement CreateStatement(SqlStatementID&, char const* sql) {
        SqlStatement statement;
        statement.sql = sql;
        return statement;
    }
};
inline bool SqlStatement::AppendToCheckedBatch(SqlCheckedBatch& batch,
                                               SqlAffectedRowsPolicy policy) {
    batch.writes.push_back(BoundWrite{sql, params, policy});
    return true;
}


enum class AccountLifecycleKind { Mutation, Deletion };
class AccountLifecycleLease;
struct CharacterPersistenceAdmission {
    bool Admit(std::vector<uint32> const&, AccountLifecycleKind,
               AccountLifecycleLease const* = nullptr) { return true; }
};

struct CharacterPersistenceContext {
    CharacterPersistenceContext(Database& database, uint32) { m_batch.db = &database; }
    SqlCheckedBatch& Batch() { return m_batch; }
    bool IsInvalid() const { return m_invalid; }
    bool WasSubmitted() const { return m_submitted; }
    void Invalidate() { m_invalid = true; m_acks.clear(); }
    bool HasIdentityGuard(CharacterIdentitySnapshot const&) const { return true; }
    bool AppendIdentityGuards(std::vector<CharacterIdentitySnapshot> const& identities) {
        m_identities = identities;
        return !identities.empty();
    }
    std::vector<CharacterIdentitySnapshot> const& StagedIdentities() const { return m_identities; }
    void OnCommit(std::function<void()> action) {
        if (!m_invalid && action)
            m_acks.push_back(std::move(action));
    }
    SqlBatchResult Execute() {
        ++g_executeCalls;
        m_submitted = true;
        g_lastWrites = m_batch.writes;
        SqlBatchResult result = g_result;
        if (result.outcome != SqlWriteOutcome::Succeeded ||
            result.stage != SqlBatchStage::Complete) {
            m_acks.clear();
            return result;
        }
        auto acks = std::move(m_acks);
        m_acks.clear();
        for (auto& ack : acks)
            ack();
        return result;
    }
    static SqlBatchResult g_result;
    static std::vector<BoundWrite> g_lastWrites;
    static int g_executeCalls;
    SqlCheckedBatch m_batch;
    bool m_invalid = false;
    bool m_submitted = false;
    std::vector<std::function<void()>> m_acks;
    std::vector<CharacterIdentitySnapshot> m_identities;
};
SqlBatchResult CharacterPersistenceContext::g_result;
std::vector<BoundWrite> CharacterPersistenceContext::g_lastWrites;
int CharacterPersistenceContext::g_executeCalls = 0;

struct WorldSession {
    uint32 account = 42;
    uint32 GetAccountId() const { return account; }
};

struct LogStub { template<typename... A> void Out(A&&...) {} } sLog;

struct Player {
    uint32 guid = 500;
    uint32 money = 1000;
    uint32 maximum = 100000;
    int moneyLogs = 0;
    WorldSession session;
    WorldSession* GetSession() const { return const_cast<WorldSession*>(&session); }
    uint32 GetGUIDLow() const { return guid; }
    ObjectGuid GetObjectGuid() const { return ObjectGuid(HIGHGUID_PLAYER, guid); }
    char const* GetName() const { return "Money Taker"; }
    uint64 GetIdentityGeneration() const { return 9; }
    uint32 GetMoney() const { return money; }
    uint32 GetMaxMoney() const { return maximum; }
    void LogModifyMoney(int32 delta, char const*, ObjectGuid = ObjectGuid(), uint32 = 0) {
        ++moneyLogs;
        money = uint32(int32(money) + delta);
    }
    void AppendIdentityGenerationPublication(CharacterPersistenceContext&) {}
};

CharacterIdentitySnapshot MailActorIdentity(Player const& player) {
    CharacterIdentitySnapshot identity;
    identity.guid = player.GetGUIDLow();
    identity.accountId = player.GetSession()->GetAccountId();
    identity.canonicalName = player.GetName();
    identity.generation = player.GetIdentityGeneration();
    return identity;
}

Database CharacterDatabase;

MailPersistenceStatusPlaceholder
'''


def _tail():
    source = MAIL.read_text(encoding="utf-8")
    pending = source[source.index("struct MailPendingState"):
                     source.index("\n}\n\nMailPersistenceStatus ClassifyMailBatch")]
    classify = source[source.index("MailPersistenceStatus ClassifyMailBatch"):
                      source.index("bool AppendItemTextSave(")]
    take = source[source.index("MailPersistenceStatus TakeMailMoney("):
                  source.index("MailPersistenceStatus ProcessExpiredMail(")]
    identity = IDENTITY.read_text(encoding="utf-8")
    withdraw = identity[identity.index("bool AppendMailMoneyWithdrawal("):]
    return pending, classify, take, withdraw


MAIN = r'''
static int failures = 0;
static void check(bool value, std::string const& label) {
    if (!value) { std::cerr << "FAIL " << label << "\n"; ++failures; }
}
static std::size_t countWrites(char const* needle) {
    std::size_t count = 0;
    for (auto const& write : CharacterPersistenceContext::g_lastWrites)
        if (write.sql.find(needle) != std::string::npos)
            ++count;
    return count;
}
static void resetState() {
    CharacterPersistenceContext::g_result = SqlBatchResult();
    CharacterPersistenceContext::g_lastWrites.clear();
    CharacterPersistenceContext::g_executeCalls = 0;
    g_recoveryRecords.clear();
}

int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";

    if (scenario == "take_money_commit") {
        resetState();
        Player player;
        Mail mail;
        mail.messageID = 9;
        mail.sender = 800;
        mail.receiverGuid = ObjectGuid(HIGHGUID_PLAYER, 500);
        mail.money = 100;
        mail.itemTextId = 77;
        mail.checked = MAIL_CHECK_MASK_READ;
        mail.expire_time = 1234;
        mail.state = MAIL_STATE_CHANGED;
        MailPersistenceStatus status = TakeMailMoney(player, &mail);
        check(status == MailPersistenceStatus::Committed &&
              CharacterPersistenceContext::g_executeCalls == 1,
              std::string("one committed execution status=") +
              std::to_string(int(status)) + " calls=" +
              std::to_string(CharacterPersistenceContext::g_executeCalls));
        check(countWrites("UPDATE `mail` SET `money`=0") == 1, "mail money claimed");
        check(countWrites("UPDATE `characters` SET `money`=?") == 1, "guarded balance update");
        check(player.money == 1100 && player.moneyLogs == 1, "money published on commit");
        check(countWrites("UPDATE `mail` SET `item_text_id`") == 1,
              "complete captured pending mail state staged");
        check(mail.money == 0 && mail.state == MAIL_STATE_UNCHANGED &&
              mail.checked == MAIL_CHECK_MASK_READ && mail.itemTextId == 77,
              "captured pending mutation acknowledged, not dropped");
        check(g_recoveryRecords.empty(), "no recovery");
    }
    else if (scenario == "pending_newer_change_preserved") {
        resetState();
        Mail mail;
        mail.messageID = 9;
        mail.money = 100;
        mail.checked = MAIL_CHECK_MASK_READ;
        mail.expire_time = 1234;
        mail.itemTextId = 77;
        MailPendingState staged = CapturePendingState(mail);
        mail.checked ^= MAIL_CHECK_MASK_COD_PAYMENT;   // newer change
        PublishPendingMailState(mail, staged, 0, staged.cod, false, 0);
        check(mail.money == 0 && mail.COD == staged.cod, "planned changes applied");
        check(mail.checked == (MAIL_CHECK_MASK_READ ^ MAIL_CHECK_MASK_COD_PAYMENT),
              "newer field change preserved");
        check(mail.state == MAIL_STATE_CHANGED,
              "newer dirty state not falsely acknowledged");
    }
    else if (scenario == "take_money_zero") {
        resetState();
        Player player;
        Mail mail;
        mail.receiverGuid = ObjectGuid(HIGHGUID_PLAYER, 500);
        mail.money = 0;
        MailPersistenceStatus status = TakeMailMoney(player, &mail);
        check(status == MailPersistenceStatus::Rejected, "zero money rejected");
        check(CharacterPersistenceContext::g_executeCalls == 0 && player.money == 1000,
              "no execution/money change");
    }
    else if (scenario == "take_money_cap") {
        resetState();
        Player player;
        player.money = 99950;
        player.maximum = 100000;
        Mail mail;
        mail.receiverGuid = ObjectGuid(HIGHGUID_PLAYER, 500);
        mail.money = 100;
        MailPersistenceStatus status = TakeMailMoney(player, &mail);
        check(status == MailPersistenceStatus::Rejected, "over-cap rejected");
        check(CharacterPersistenceContext::g_executeCalls == 0, "no execution");
    }
    else if (scenario == "take_money_known_failure") {
        resetState();
        CharacterPersistenceContext::g_result.outcome = SqlWriteOutcome::Failed;
        Player player;
        Mail mail;
        mail.messageID = 10;
        mail.receiverGuid = ObjectGuid(HIGHGUID_PLAYER, 500);
        mail.money = 100;
        MailPersistenceStatus status = TakeMailMoney(player, &mail);
        check(status == MailPersistenceStatus::Rejected, "known failure classified");
        check(player.money == 1000 && mail.money == 100, "nothing published");
        check(g_recoveryRecords.empty(), "known failure does not block");
    }
    else if (scenario == "take_money_unknown") {
        resetState();
        CharacterPersistenceContext::g_result.outcome = SqlWriteOutcome::OutcomeUnknown;
        Player player;
        Mail mail;
        mail.messageID = 11;
        mail.receiverGuid = ObjectGuid(HIGHGUID_PLAYER, 500);
        mail.money = 100;
        MailPersistenceStatus status = TakeMailMoney(player, &mail);
        check(status == MailPersistenceStatus::OutcomeUnknown, "unknown classified");
        check(g_recoveryRecords.size() == 1 &&
              g_recoveryRecords[0].guids == std::vector<uint32>({500}), "identity blocked");
        check(player.money == 1000 && mail.money == 100, "nothing published on unknown");
    }
    else if (scenario == "take_money_wrong_receiver") {
        resetState();
        Player player;
        Mail mail;
        mail.receiverGuid = ObjectGuid(HIGHGUID_PLAYER, 999);
        mail.money = 100;
        MailPersistenceStatus status = TakeMailMoney(player, &mail);
        check(status == MailPersistenceStatus::Rejected, "foreign mail rejected");
        check(CharacterPersistenceContext::g_executeCalls == 0, "no execution");
    }
    else { std::cerr << "unknown\n"; return 2; }

    if (failures) { std::cerr << "failures=" << failures << "\n"; return 1; }
    std::cout << "mail-money " << scenario << ": PASS\n";
    return 0;
}
'''


@unittest.skipUnless(shutil.which("cmake"), "cmake not available")
class MailMoneyTakeExecutionTests(unittest.TestCase):
    def test_mail_money_take(self):
        pending, classify, take, withdraw = _tail()
        preamble = (PREAMBLE.replace("MailPersistenceStatusPlaceholder", "")
                    .replace("enum class SqlAffectedRowsPolicy",
                             "enum class MailPersistenceStatus { Committed, Rejected, OutcomeUnknown };\n"
                             "enum class SqlAffectedRowsPolicy"))
        program = preamble + "\n" + pending + "\n" + classify + "\n" + withdraw + "\n" + take + "\n" + MAIN
        with tempfile.TemporaryDirectory(prefix="fn05h-mail-money-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(mail_money_exec CXX)\n"
                "set(CMAKE_CXX_STANDARD 14)\n"
                "add_executable(probe probe.cpp)\n", encoding="utf-8")
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
            exe = build / "Release" / "probe.exe"
            if not exe.exists():
                exe = build / "probe"
            failures = []
            for scenario in ["take_money_commit", "pending_newer_change_preserved",
                             "take_money_zero", "take_money_cap",
                             "take_money_known_failure", "take_money_unknown",
                             "take_money_wrong_receiver"]:
                r = subprocess.run([str(exe), scenario], text=True,
                                   capture_output=True, timeout=30)
                if r.returncode != 0 or ("mail-money " + scenario + ": PASS") not in r.stdout:
                    failures.append(scenario + "\n" + r.stdout + r.stderr)
            self.assertFalse(failures, "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
