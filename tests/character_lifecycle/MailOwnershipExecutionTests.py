#!/usr/bin/env python3
"""Executable ownership/publication regression for the checked mail send path.

The real `MailDraft::AppendSendTo` body and its append helpers are extracted from
MailPersistence.cpp and compiled against a recording checked-context stub.  This
exercises the actual assembly order (body text, item rows, inventory removal,
ownership transfer, mail row, every attachment link) and the commit-only
publication (online mailbox/item registry versus offline staging-item disposal).

The database and context are stubs by design; the control flow and ownership
consequences are production code.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/game/Mail/MailPersistence.cpp"

PREAMBLE = r'''
#include <algorithm>
#include <ctime>
#include <functional>
#include <iostream>
#include <map>
#include <memory>
#include <string>
#include <vector>

using uint8 = unsigned char;
using uint16 = unsigned short;
using uint32 = unsigned int;
using uint64 = unsigned long long;
using int16 = signed short;

#define HOUR 3600
#define DAY 86400

enum MailMessageType { MAIL_NORMAL = 0, MAIL_AUCTION = 2, MAIL_CREATURE = 3 };
enum MailCheckMask { MAIL_CHECK_MASK_NONE = 0x00, MAIL_CHECK_MASK_RETURNED = 0x02,
                     MAIL_CHECK_MASK_COD_PAYMENT = 0x08 };
enum MailStationery { MAIL_STATIONERY_DEFAULT = 41 };
enum MailState { MAIL_STATE_UNCHANGED = 1, MAIL_STATE_CHANGED = 2, MAIL_STATE_DELETED = 3 };
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

struct Field {
    uint32 u = 0;
    uint32 GetUInt32() const { return u; }
    uint8 GetUInt8() const { return uint8(u); }
    int16 GetInt16() const { return int16(u); }
    bool GetBool() const { return u != 0; }
    uint64 GetUInt64() const { return u; }
};

struct MailItemInfo {
    uint32 itemGuid = 0;
    uint32 itemId = 0;
};

struct Mail {
    uint32 messageID = 0;
    uint8 messageType = MAIL_NORMAL;
    uint8 stationery = MAIL_STATIONERY_DEFAULT;
    uint16 mailTemplateId = 0;
    uint32 sender = 0;
    ObjectGuid receiverGuid;
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
    bool RemoveItem(uint32) { return true; }
    void AddItem(uint32 guid, uint32 id) {
        items.push_back(MailItemInfo{guid, id});
        has_items = true;
    }
};

enum class SqlAffectedRowsPolicy { Any, ExactlyOne };
enum class MailPersistenceStatus { Committed, Rejected, OutcomeUnknown };
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
    bool appendOk = true;
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
    if (!appendOk)
        return false;
    batch.writes.push_back(BoundWrite{sql, params, policy});
    return true;
}


enum class AccountLifecycleKind { Mutation, Deletion };
class AccountLifecycleLease;
struct CharacterPersistenceAdmission {
    bool admitOk = true;
    bool Admit(std::vector<uint32> const&, AccountLifecycleKind,
               AccountLifecycleLease const* = nullptr) { return admitOk; }
};

struct CharacterPersistenceContext {
    CharacterPersistenceContext(Database& database, uint32) {
        m_batch.db = &database;
    }
    SqlCheckedBatch& Batch() { return m_batch; }
    void Invalidate() { m_invalid = true; m_acks.clear(); }
    bool IsInvalid() const { return m_invalid; }
    bool WasSubmitted() const { return false; }
    void OnCommit(std::function<void()> action) {
        if (!m_invalid && action)
            m_acks.push_back(std::move(action));
    }
    void CommitForTest() {
        std::vector<std::function<void()>> acks = std::move(m_acks);
        m_acks.clear();
        for (auto& ack : acks)
            ack();
    }
    std::size_t PendingCallbacks() const { return m_acks.size(); }
    SqlCheckedBatch m_batch;
    bool m_invalid = false;
    std::vector<std::function<void()>> m_acks;
};

struct Player;
struct MasterPlayer;
struct MailReceiver {
    MailReceiver(Player* player, ObjectGuid guid) : m_player(player), m_guid(guid) {}
    explicit MailReceiver(ObjectGuid guid) : m_player(nullptr), m_guid(guid) {}
    Player* GetPlayer() const { return m_player; }
    ObjectGuid const& GetPlayerGuid() const { return m_guid; }
    Player* m_player;
    ObjectGuid m_guid;
};

struct MailSender {
    MailSender(MailMessageType type, uint32 id, MailStationery stationery)
        : m_type(type), m_id(id), m_stationery(stationery) {}
    MailMessageType GetMailMessageType() const { return m_type; }
    uint32 GetSenderId() const { return m_id; }
    MailStationery GetStationery() const { return m_stationery; }
    MailMessageType m_type;
    uint32 m_id;
    MailStationery m_stationery;
};

struct Item;
struct MasterPlayer {
    std::vector<Mail*> mails;
    std::vector<Item*> items;
    int deliverTimes = 0;
    void AddNewMailDeliverTime(time_t) { ++deliverTimes; }
    void AddMail(Mail* mail) { mails.push_back(mail); }
    void AddMItem(Item* item) { items.push_back(item); }
};

struct ObjectAccessorStub {
    MasterPlayer* master = nullptr;
    MasterPlayer* FindMasterPlayer(ObjectGuid) const { return master; }
} sObjectAccessor;

struct ObjectMgrStub {
    uint32 nextMailId = 100;
    uint32 GenerateMailID() { return ++nextMailId; }
    std::vector<std::pair<uint32, std::string>> itemTexts;
    void AddItemText(uint32 id, std::string const& text) { itemTexts.push_back({id, text}); }
} sObjectMgr;

struct LogStub { template<typename... A> void Out(A&&...) {} } sLog;

int g_deletedItems = 0;
uint32 g_itemSaveWrites = 0;
uint32 g_inventoryRemovalWrites = 0;

struct Item {
    uint32 guid = 0;
    uint32 entry = 0;
    ObjectGuid owner;
    bool appendOk = true;
    bool AppendSaveToDB(CharacterPersistenceContext&) {
        if (!appendOk)
            return false;
        ++g_itemSaveWrites;
        return true;
    }
    bool AppendInventoryRemovalToDB(CharacterPersistenceContext&) {
        ++g_inventoryRemovalWrites;
        return true;
    }
    uint32 GetGUIDLow() const { return guid; }
    uint32 GetEntry() const { return entry; }
    ObjectGuid const& GetOwnerGuid() const { return owner; }
    void SetOwnerGuid(ObjectGuid value) { owner = value; }
    ~Item() { ++g_deletedItems; }
};

struct Player {};

class MailDraft {
public:
    bool AppendSendTo(CharacterPersistenceContext& context,
                      MailReceiver const& receiver, MailSender const& sender,
                      MailCheckMask checked, uint32 deliver_delay,
                      uint32 expire_delay, uint32& mailId,
                      std::function<void()> prePublication = nullptr);
    bool prepareItems(Player*) { return false; }
    std::string const& GetSubject() const { return m_subject; }
    uint32 GetBodyId() const { return m_bodyId; }
    bool HasPendingBodyText() const { return m_bodyTextPending; }
    std::string const& GetPendingBodyText() const { return m_bodyText; }
    uint32 GetMoney() const { return m_money; }
    uint32 GetCOD() const { return m_COD; }
    uint16 GetMailTemplateId() const { return m_mailTemplateId; }

    uint16 m_mailTemplateId = 0;
    bool m_mailTemplateItemsNeed = false;
    std::string m_subject;
    uint32 m_bodyId = 0;
    bool m_bodyTextPending = false;
    std::string m_bodyText;
    std::map<uint32, Item*> m_items;
    uint32 m_money = 0;
    uint32 m_COD = 0;
};
'''

MAIN = r'''
static int failures = 0;
static void check(bool value, std::string const& label) {
    if (!value) { std::cerr << "FAIL " << label << "\n"; ++failures; }
}

static bool hasWrite(CharacterPersistenceContext& context, char const* needle) {
    for (auto const& write : context.m_batch.writes)
        if (write.sql.find(needle) != std::string::npos)
            return true;
    return false;
}

static std::size_t countWrites(CharacterPersistenceContext& context, char const* needle) {
    std::size_t count = 0;
    for (auto const& write : context.m_batch.writes)
        if (write.sql.find(needle) != std::string::npos)
            ++count;
    return count;
}

static std::size_t indexOfWrite(CharacterPersistenceContext& context, char const* needle) {
    for (std::size_t i = 0; i < context.m_batch.writes.size(); ++i)
        if (context.m_batch.writes[i].sql.find(needle) != std::string::npos)
            return i;
    return context.m_batch.writes.size();
}

static Item* makeItem(uint32 guid, uint32 entry, uint32 owner) {
    Item* item = new Item();
    item->guid = guid;
    item->entry = entry;
    item->owner = ObjectGuid(HIGHGUID_PLAYER, owner);
    return item;
}

int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";

    if (scenario == "offline_two_items") {
        Database database;
        CharacterPersistenceContext context(database, 600);
        MailDraft draft;
        draft.m_subject = "subject";
        draft.m_bodyId = 900;
        draft.m_bodyTextPending = true;
        draft.m_bodyText = "hello";
        Item* first = makeItem(11, 111, 500);
        Item* second = makeItem(12, 112, 500);
        draft.m_items[11] = first;
        draft.m_items[12] = second;
        uint32 mailId = 0;
        bool ok = draft.AppendSendTo(context, MailReceiver(ObjectGuid(HIGHGUID_PLAYER, 600)),
                                     MailSender(MAIL_NORMAL, 500, MAIL_STATIONERY_DEFAULT),
                                     MAIL_CHECK_MASK_NONE, 0, 0, mailId);
        check(ok, "assembly succeeded");
        check(countWrites(context, "INSERT INTO `item_text`") == 1, "one body insert");
        check(countWrites(context, "INSERT INTO `mail`") == 1, "one mail insert");
        check(countWrites(context, "INSERT INTO `mail_items`") == 2, "every attachment linked");
        check(countWrites(context, "UPDATE `item_instance` SET `owner_guid`") == 2,
              "every attachment ownership transferred");
        check(g_itemSaveWrites == 2 && g_inventoryRemovalWrites == 2,
              "every attachment saved and removed from inventory");
        check(indexOfWrite(context, "INSERT INTO `item_text`") <
              indexOfWrite(context, "INSERT INTO `mail`"), "body before mail");
        check(indexOfWrite(context, "UPDATE `item_instance` SET `owner_guid`") <
              indexOfWrite(context, "INSERT INTO `mail`"), "ownership before mail");
        check(context.PendingCallbacks() == 1 && g_deletedItems == 0,
              "nothing published/destroyed before commit");
        context.CommitForTest();
        check(g_deletedItems == 2, "offline staging items disposed on commit");
        check(sObjectMgr.itemTexts.size() == 1 && sObjectMgr.itemTexts[0].first == 900 &&
              sObjectMgr.itemTexts[0].second == "hello", "body published on commit");
        check(sObjectAccessor.master == nullptr, "no online publication");
    }
    else if (scenario == "online_two_items") {
        Database database;
        CharacterPersistenceContext context(database, 700);
        MasterPlayer master;
        sObjectAccessor.master = &master;
        MailDraft draft;
        draft.m_subject = "subject";
        Item* first = makeItem(21, 121, 500);
        Item* second = makeItem(22, 122, 500);
        draft.m_items[21] = first;
        draft.m_items[22] = second;
        uint32 mailId = 0;
        bool ok = draft.AppendSendTo(context, MailReceiver(ObjectGuid(HIGHGUID_PLAYER, 700)),
                                     MailSender(MAIL_NORMAL, 500, MAIL_STATIONERY_DEFAULT),
                                     MAIL_CHECK_MASK_NONE, 0, 0, mailId);
        check(ok && context.PendingCallbacks() == 1, "assembly, publication deferred");
        check(g_deletedItems == 0 && master.mails.empty() && master.items.empty(),
              "nothing published before commit");
        context.CommitForTest();
        check(g_deletedItems == 0, "online items not destroyed");
        check(master.mails.size() == 1 && master.mails[0]->items.size() == 2,
              "online mail carries every attachment");
        check(master.items.size() == 2, "online item registry got every attachment");
        check(master.deliverTimes == 1, "delivery time published once");
        check(first->GetOwnerGuid().GetCounter() == 700 &&
              second->GetOwnerGuid().GetCounter() == 700, "owners published on commit");
    }
    else if (scenario == "same_owner_no_transfer") {
        Database database;
        CharacterPersistenceContext context(database, 600);
        MailDraft draft;
        Item* item = makeItem(31, 131, 600);
        draft.m_items[31] = item;
        uint32 mailId = 0;
        bool ok = draft.AppendSendTo(context, MailReceiver(ObjectGuid(HIGHGUID_PLAYER, 600)),
                                     MailSender(MAIL_NORMAL, 500, MAIL_STATIONERY_DEFAULT),
                                     MAIL_CHECK_MASK_NONE, 0, 0, mailId);
        check(ok, "assembly");
        check(countWrites(context, "UPDATE `item_instance` SET `owner_guid`") == 0,
              "no redundant ownership update");
        check(countWrites(context, "INSERT INTO `mail_items`") == 1, "link still staged");
    }
    else if (scenario == "assembly_failure") {
        Database database;
        CharacterPersistenceContext context(database, 600);
        MailDraft draft;
        Item* good = makeItem(41, 141, 500);
        Item* bad = makeItem(42, 142, 500);
        bad->appendOk = false;
        draft.m_items[41] = good;
        draft.m_items[42] = bad;
        uint32 mailId = 0;
        bool ok = draft.AppendSendTo(context, MailReceiver(ObjectGuid(HIGHGUID_PLAYER, 600)),
                                     MailSender(MAIL_NORMAL, 500, MAIL_STATIONERY_DEFAULT),
                                     MAIL_CHECK_MASK_NONE, 0, 0, mailId);
        check(!ok, "assembly failure reported");
        check(!hasWrite(context, "INSERT INTO `mail`"), "no mail row on failure");
        context.CommitForTest();
        check(g_deletedItems == 0, "no destruction on failure");
    }
    else if (scenario == "empty_draft") {
        Database database;
        CharacterPersistenceContext context(database, 600);
        MailDraft draft;
        draft.m_subject = "note";
        uint32 mailId = 0;
        bool ok = draft.AppendSendTo(context, MailReceiver(ObjectGuid(HIGHGUID_PLAYER, 600)),
                                     MailSender(MAIL_NORMAL, 500, MAIL_STATIONERY_DEFAULT),
                                     MAIL_CHECK_MASK_NONE, 0, 0, mailId);
        check(ok, "empty draft sends");
        check(countWrites(context, "INSERT INTO `mail`") == 1, "one mail row");
        check(countWrites(context, "INSERT INTO `mail_items`") == 0, "no links");
        check(countWrites(context, "INSERT INTO `item_text`") == 0, "no body row");
        context.CommitForTest();
        check(g_deletedItems == 0, "no items");
    }
    else { std::cerr << "unknown\n"; return 2; }

    if (failures) { std::cerr << "failures=" << failures << "\n"; return 1; }
    std::cout << "mail-ownership " << scenario << ": PASS\n";
    return 0;
}
'''


def extract():
    source = SOURCE.read_text(encoding="utf-8")
    namespace_end = source.index("\n}\n\nMailPersistenceStatus ClassifyMailBatch")
    row_helpers = source[source.index("struct MailRow"):
                         source.index("bool AppendItemDisposal(")]
    publish_helpers = source[source.index("Mail* BuildPublishedMail("):namespace_end]
    helpers = row_helpers + "\n" + publish_helpers
    tail = source[source.index("MailPersistenceStatus ClassifyMailBatch"):
                  source.index("MailPersistenceStatus MailDraft::SendMailTo(")]
    return helpers, tail


@unittest.skipUnless(shutil.which("cmake"), "cmake not available")
class MailOwnershipExecutionTests(unittest.TestCase):
    def test_mail_send_ownership(self):
        helpers, tail = extract()
        program = (PREAMBLE + "\nnamespace mail_helpers {\n" + helpers + "\n}\n"
                   "using namespace mail_helpers;\n" + tail + "\n" + MAIN)
        with tempfile.TemporaryDirectory(prefix="fn05h-mail-own-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(mail_ownership_exec CXX)\n"
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
            for scenario in ["offline_two_items", "online_two_items",
                             "same_owner_no_transfer", "assembly_failure",
                             "empty_draft"]:
                r = subprocess.run([str(exe), scenario], text=True,
                                   capture_output=True, timeout=30)
                if r.returncode != 0 or ("mail-ownership " + scenario + ": PASS") not in r.stdout:
                    failures.append(scenario + "\n" + r.stdout + r.stderr)
            self.assertFalse(failures, "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
