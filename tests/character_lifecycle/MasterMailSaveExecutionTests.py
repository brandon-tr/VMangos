#!/usr/bin/env python3
"""Executable regression for MasterPlayer checked mail saving.

The real `MasterPlayer::AppendMailsToDB` body and its identity helpers are
extracted from MasterPlayer.cpp and compiled against recording stubs.  The
scenarios cover changed-mail updates, deleted-mail cleanup (items, body, mail,
links), commit-only dirty-state clearing, and preserving a newer in-memory change
that happened after staging.  Database execution is stubbed by design.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/game/Chat/MasterPlayer.cpp"

PREAMBLE = r'''
#include <algorithm>
#include <ctime>
#include <deque>
#include <functional>
#include <iostream>
#include <map>
#include <string>
#include <vector>

using uint8 = unsigned char;
using uint16 = unsigned short;
using uint32 = unsigned int;
using uint64 = unsigned long long;

enum MailStationery { MAIL_STATIONERY_DEFAULT = 41, MAIL_STATIONERY_AUCTION = 62 };
enum MailState { MAIL_STATE_UNCHANGED = 1, MAIL_STATE_CHANGED = 2, MAIL_STATE_DELETED = 3 };
enum { HIGHGUID_PLAYER = 0 };

struct ObjectGuid {
    uint32 value = 0;
    ObjectGuid() {}
    ObjectGuid(int, uint32 v) : value(v) {}
    uint32 GetCounter() const { return value; }
};

struct MailItemInfo { uint32 itemGuid = 0; uint32 itemId = 0; };

int g_deletedMails = 0;
struct Mail {
    uint32 messageID = 0;
    uint8 stationery = MAIL_STATIONERY_DEFAULT;
    std::string subject;
    uint32 itemTextId = 0;
    bool has_items = false;
    std::vector<MailItemInfo> items;
    std::vector<uint32> removedItems;
    time_t expire_time = 0;
    time_t deliver_time = 0;
    uint32 money = 0;
    uint32 COD = 0;
    uint32 checked = 0;
    MailState state = MAIL_STATE_UNCHANGED;
    bool HasItems() const { return has_items; }
    ~Mail() { ++g_deletedMails; }
};
using PlayerMails = std::deque<Mail*>;

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

struct CharacterIdentitySnapshot {
    uint32 guid = 0;
    uint32 accountId = 0;
    std::string canonicalName;
    uint64 generation = 0;
};

struct CharacterPersistenceContext {
    CharacterPersistenceContext(Database& database, uint32) { m_batch.db = &database; }
    SqlCheckedBatch& Batch() { return m_batch; }
    bool IsInvalid() const { return m_invalid; }
    bool WasSubmitted() const { return false; }
    void Invalidate() { m_invalid = true; m_acks.clear(); }
    bool HasIdentityGuard(CharacterIdentitySnapshot const&) const { return true; }
    bool AppendIdentityGuards(std::vector<CharacterIdentitySnapshot> const& identities) {
        m_identities = identities;
        return true;
    }
    std::vector<CharacterIdentitySnapshot> const& StagedIdentities() const { return m_identities; }
    void OnCommit(std::function<void()> action) {
        if (!m_invalid && action)
            m_acks.push_back(std::move(action));
    }
    void CommitForTest() {
        auto acks = std::move(m_acks);
        m_acks.clear();
        for (auto& ack : acks)
            ack();
    }
    std::size_t PendingCallbacks() const { return m_acks.size(); }
    SqlCheckedBatch m_batch;
    bool m_invalid = false;
    std::vector<std::function<void()>> m_acks;
    std::vector<CharacterIdentitySnapshot> m_identities;
};

struct LogStub { template<typename... A> void Out(A&&...) {} } sLog;

struct WorldSession {
    uint32 account = 42;
    uint32 GetAccountId() const { return account; }
};

class MasterPlayer {
public:
    bool AppendMailsToDB(CharacterPersistenceContext& context);
    uint32 GetGUIDLow() const { return guid.GetCounter(); }
    WorldSession* GetSession() const { return session; }
    ObjectGuid guid = ObjectGuid(HIGHGUID_PLAYER, 500);
    std::string name = "Master Name";
    uint64 identityGeneration = 9;
    WorldSession* session = new WorldSession();
    PlayerMails m_mail;
    bool m_mailsUpdated = false;
};
'''

MAIN = r'''
static int failures = 0;
static void check(bool value, std::string const& label) {
    if (!value) { std::cerr << "FAIL " << label << "\n"; ++failures; }
}
static std::size_t countWrites(CharacterPersistenceContext& context, char const* needle) {
    std::size_t count = 0;
    for (auto const& write : context.m_batch.writes)
        if (write.sql.find(needle) != std::string::npos)
            ++count;
    return count;
}
static Mail* makeMail(uint32 id, MailState state, uint32 items,
                      uint32 removed, bool hasItems, uint32 itemTextId,
                      uint8 stationery = MAIL_STATIONERY_DEFAULT) {
    Mail* mail = new Mail();
    mail->messageID = id;
    mail->state = state;
    mail->has_items = hasItems;
    mail->itemTextId = itemTextId;
    mail->stationery = stationery;
    for (uint32 i = 0; i < items; ++i)
        mail->items.push_back(MailItemInfo{100 + i, 200 + i});
    for (uint32 i = 0; i < removed; ++i)
        mail->removedItems.push_back(300 + i);
    return mail;
}

int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";

    if (scenario == "changed_mail") {
        Database database;
        CharacterPersistenceContext context(database, 500);
        MasterPlayer master;
        Mail* mail = makeMail(1, MAIL_STATE_CHANGED, 0, 2, true, 77);
        master.m_mail.push_back(mail);
        master.m_mailsUpdated = true;
        check(master.AppendMailsToDB(context), "assembly");
        check(countWrites(context, "UPDATE `mail` SET") == 1, "one mail update");
        check(countWrites(context, "DELETE FROM `mail_items` WHERE `item_guid` = ?") == 2,
              "every removed attachment link deleted");
        check(mail->state == MAIL_STATE_CHANGED && mail->removedItems.size() == 2,
              "nothing acknowledged before commit");
        check(context.PendingCallbacks() == 1, "publication deferred");
        context.CommitForTest();
        check(mail->state == MAIL_STATE_UNCHANGED && mail->removedItems.empty(),
              "state and removed links acknowledged on commit");
        check(!master.m_mailsUpdated, "dirty flag cleared only after commit");
    }
    else if (scenario == "deleted_mail") {
        Database database;
        CharacterPersistenceContext context(database, 500);
        MasterPlayer master;
        Mail* mail = makeMail(2, MAIL_STATE_DELETED, 2, 0, true, 88,
                              MAIL_STATIONERY_AUCTION);
        master.m_mail.push_back(mail);
        master.m_mailsUpdated = true;
        check(master.AppendMailsToDB(context), "assembly");
        check(countWrites(context, "DELETE FROM `item_instance` WHERE `guid` = ? AND `owner_guid` = ?") == 2,
              "every attachment row deleted");
        check(countWrites(context, "DELETE FROM `item_text` WHERE `id` = ?") == 1,
              "selected body deleted");
        check(countWrites(context, "DELETE FROM `mail` WHERE `id` = ? AND `receiver_guid` = ?") == 1,
              "guarded mail delete");
        check(countWrites(context, "DELETE FROM `mail_items` WHERE `mail_id` = ? AND `receiver_guid` = ?") == 1,
              "link rows deleted");
        check(master.m_mail.size() == 1 && g_deletedMails == 0, "mail object alive before commit");
        context.CommitForTest();
        check(master.m_mail.empty() && g_deletedMails == 1, "mail object destroyed on commit");
        check(!master.m_mailsUpdated, "dirty flag cleared after commit");
    }
    else if (scenario == "newer_change_preserved") {
        Database database;
        CharacterPersistenceContext context(database, 500);
        MasterPlayer master;
        Mail* mail = makeMail(3, MAIL_STATE_CHANGED, 0, 0, true, 77);
        master.m_mail.push_back(mail);
        master.m_mailsUpdated = true;
        check(master.AppendMailsToDB(context), "assembly");
        // A newer gameplay change after staging must not be acknowledged by the
        // older snapshot's completion.
        mail->itemTextId = 99;
        context.CommitForTest();
        check(mail->state == MAIL_STATE_CHANGED, "newer change keeps the mail dirty");
        check(master.m_mailsUpdated, "dirty flag stays set for newer state");
    }
    else if (scenario == "default_stationery_body_kept") {
        Database database;
        CharacterPersistenceContext context(database, 500);
        MasterPlayer master;
        Mail* mail = makeMail(4, MAIL_STATE_DELETED, 0, 0, false, 55,
                              MAIL_STATIONERY_DEFAULT);
        master.m_mail.push_back(mail);
        check(master.AppendMailsToDB(context), "assembly");
        check(countWrites(context, "DELETE FROM `item_text` WHERE `id` = ?") == 0,
              "default stationery body is not deleted (legacy rule)");
        check(countWrites(context, "DELETE FROM `mail` WHERE `id` = ? AND `receiver_guid` = ?") == 1,
              "mail still removed");
        context.CommitForTest();
        check(master.m_mail.empty() && g_deletedMails == 1, "mail consumed");
    }
    else if (scenario == "unchanged_mail_no_writes") {
        Database database;
        CharacterPersistenceContext context(database, 500);
        MasterPlayer master;
        master.m_mail.push_back(makeMail(5, MAIL_STATE_UNCHANGED, 2, 0, true, 0));
        check(master.AppendMailsToDB(context), "assembly");
        check(context.m_batch.writes.empty(), "no SQL for unchanged mail");
        check(context.PendingCallbacks() == 0, "no completion for unchanged mail");
    }
    else { std::cerr << "unknown\n"; return 2; }

    if (failures) { std::cerr << "failures=" << failures << "\n"; return 1; }
    std::cout << "master-mail " << scenario << ": PASS\n";
    return 0;
}
'''


def extract():
    source = SOURCE.read_text(encoding="utf-8")
    identity = source[source.index("CharacterIdentitySnapshot MasterSaveIdentity("):
                      source.index("void AppendMasterGenerationPublication(")]
    save = source[source.index("bool MasterPlayer::AppendMailsToDB("):
                  source.index("void MasterPlayer::RemoveMail(")]
    return identity, save


@unittest.skipUnless(shutil.which("cmake"), "cmake not available")
class MasterMailSaveExecutionTests(unittest.TestCase):
    def test_master_mail_save(self):
        identity, save = extract()
        program = PREAMBLE + "\n" + identity + "\n" + save + "\n" + MAIN
        with tempfile.TemporaryDirectory(prefix="fn05h-master-mail-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(master_mail_exec CXX)\n"
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
            for scenario in ["changed_mail", "deleted_mail", "newer_change_preserved",
                             "default_stationery_body_kept", "unchanged_mail_no_writes"]:
                r = subprocess.run([str(exe), scenario], text=True,
                                   capture_output=True, timeout=30)
                if r.returncode != 0 or ("master-mail " + scenario + ": PASS") not in r.stdout:
                    failures.append(scenario + "\n" + r.stdout + r.stderr)
            self.assertFalse(failures, "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
