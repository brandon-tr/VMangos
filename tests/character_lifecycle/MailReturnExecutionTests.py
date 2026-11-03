#!/usr/bin/env python3
"""Executable checked path for mail return-to-sender and disposal.

The real `ReturnMailToSender` body, `ClassifyMailBatch` and the ownership /
publication helpers are extracted from MailPersistence.cpp and compiled against
recording stubs.  The scenarios drive the actual flow: guarded old-mail deletion,
every attachment ownership transfer or disposal, a new mail row with COD cleared,
commit-only publication, and rejected/unknown outcomes that publish nothing.

Database execution is stubbed by design; control flow and ownership consequences
are production code.
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
#include <limits>
#include <map>
#include <memory>
#include <string>
#include <vector>

using uint8 = unsigned char;
using uint16 = unsigned short;
using uint32 = unsigned int;
using uint64 = unsigned long long;
using int16 = signed short;
using int32 = signed int;

#define HOUR 3600
#define DAY 86400

enum MailMessageType { MAIL_NORMAL = 0, MAIL_AUCTION = 2, MAIL_CREATURE = 3 };
enum MailCheckMask { MAIL_CHECK_MASK_NONE = 0x00, MAIL_CHECK_MASK_RETURNED = 0x02,
                     MAIL_CHECK_MASK_COD_PAYMENT = 0x08 };
enum MailStationery { MAIL_STATIONERY_DEFAULT = 41 };
enum MailState { MAIL_STATE_UNCHANGED = 1, MAIL_STATE_CHANGED = 2, MAIL_STATE_DELETED = 3 };
enum { HIGHGUID_PLAYER = 0 };
enum { LOG_BASIC = 0, LOG_LVL_ERROR = 0, LOG_LVL_MINIMAL = 0 };

struct ObjectGuid {
    uint32 value = 0;
    ObjectGuid() {}
    ObjectGuid(int, uint32 v) : value(v) {}
    uint32 GetCounter() const { return value; }
    bool IsPlayer() const { return true; }
    bool operator==(ObjectGuid const& other) const { return value == other.value; }
    bool operator!=(ObjectGuid const& other) const { return value != other.value; }
};

struct ItemPosCount {
    uint16 pos = 0;
    uint32 count = 1;
    ItemPosCount() {}
    ItemPosCount(uint16 p, uint32 c) : pos(p), count(c) {}
};
using ItemPosCountVec = std::vector<ItemPosCount>;

struct MailItemInfo {
    uint32 itemGuid = 0;
    uint32 itemId = 0;
};

int g_deletedMails = 0;
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
    void AddItem(uint32 itemGuidLow, uint32 itemId) {
        items.push_back(MailItemInfo{itemGuidLow, itemId});
        has_items = true;
    }
    bool HasItems() const { return has_items; }
    bool RemoveItem(uint32 itemGuid) {
        for (auto itr = items.begin(); itr != items.end(); ++itr)
            if (itr->itemGuid == itemGuid) { items.erase(itr); return true; }
        return false;
    }
    ~Mail() { ++g_deletedMails; }
};

enum class MailPersistenceStatus { Committed, Rejected, OutcomeUnknown };
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
    bool admitOk = true;
    bool Admit(std::vector<uint32> const&, AccountLifecycleKind,
               AccountLifecycleLease const* = nullptr) { return admitOk; }
};

struct CharacterPersistenceContext {
    CharacterPersistenceContext(Database& database, uint32) { m_batch.db = &database; }
    SqlCheckedBatch& Batch() { return m_batch; }
    void Invalidate() { m_invalid = true; m_acks.clear(); }
    bool IsInvalid() const { return m_invalid; }
    bool WasSubmitted() const { return m_submitted; }
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
        g_lastWrites = m_batch.writes;
        m_submitted = true;
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

struct MasterPlayer;
class Player;
int g_deletedItems = 0;
struct MailSender { MailMessageType type = MAIL_NORMAL; MailMessageType GetMailMessageType() const { return type; } };
#define ITEM_END 24
enum ItemUpdateState { ITEM_UNCHANGED = 0, ITEM_CHANGED = 1, ITEM_NEW = 2, ITEM_REMOVED = 3 };

struct Item {
    uint32 guid = 0;
    uint32 entry = 0;
    uint32 count = 1;
    ObjectGuid owner;
    ItemUpdateState state = ITEM_UNCHANGED;
    int16 queuePos = -1;
    bool inWorld = false;
    uint32 values[ITEM_END] = {0};
    uint32 GetGUIDLow() const { return guid; }
    uint32 GetEntry() const { return entry; }
    uint32 GetCount() const { return count; }
    ObjectGuid GetObjectGuid() const { return ObjectGuid(HIGHGUID_PLAYER, guid); }
    uint8 GetBagSlot() const { return 0; }
    uint8 GetSlot() const { return 0; }
    void SetCount(uint32 value) { count = value; }
    uint32 GetUInt32Value(uint32 index) const { return values[index]; }
    void SetUInt32Value(uint32 index, uint32 value) { values[index] = value; }
    ItemUpdateState GetState() const { return state; }
    void FSetState(ItemUpdateState value) { state = value; }
    void SetState(ItemUpdateState value) { state = value; }
    int16 GetQueuePos() const { return queuePos; }
    void AddToUpdateQueueOf(Player*) { queuePos = 0; }
    void RemoveFromUpdateQueueOf(Player*) { queuePos = -1; }
    bool IsInWorld() const { return inWorld; }
    void RemoveFromWorld() { inWorld = false; }
    ObjectGuid const& GetOwnerGuid() const { return owner; }
    void SetOwnerGuid(ObjectGuid value) { owner = value; }
    ~Item() { ++g_deletedItems; }
};

struct MasterPlayer {
    std::vector<Mail*> mails;
    std::vector<Item*> items;
    std::map<uint32, Item*> byItemGuid;
    int deliverTimes = 0;
    int marksUpdated = 0;
    void AddNewMailDeliverTime(time_t) { ++deliverTimes; }
    void AddMail(Mail* mail) { mails.push_back(mail); }
    void AddMItem(Item* item) { items.push_back(item); byItemGuid[item->guid] = item; }
    Item* GetMItem(uint32 guid) const {
        auto itr = byItemGuid.find(guid);
        return itr == byItemGuid.end() ? nullptr : itr->second;
    }
    void RemoveMItem(uint32 guid) {
        byItemGuid.erase(guid);
        items.erase(std::remove_if(items.begin(), items.end(),
            [guid](Item* item) { return item->guid == guid; }), items.end());
    }
    void RemoveMail(uint32 id) {
        mails.erase(std::remove_if(mails.begin(), mails.end(),
            [id](Mail* mail) { return mail->messageID == id; }), mails.end());
    }
    void MarkMailsUpdated() { ++marksUpdated; }
};

struct WorldSession {
    MasterPlayer* master = nullptr;
    MasterPlayer* GetMasterPlayer() const { return master; }
    uint32 GetAccountId() const { return 3010; }
};

class Player {
public:
    Player() : m_guid(HIGHGUID_PLAYER, 500), m_session(new WorldSession()) {}
    ~Player() { delete m_session; }
    uint32 GetGUIDLow() const { return m_guid.GetCounter(); }
    ObjectGuid const& GetObjectGuid() const { return m_guid; }
    char const* GetName() const { return "Returner Name"; }
    uint64 GetIdentityGeneration() const { return 9; }
    WorldSession* GetSession() const { return m_session; }
    void AppendIdentityGenerationPublication(CharacterPersistenceContext&) {}
    uint32 GetMoney() const { return money; }
    uint32 GetMaxMoney() const { return 100000; }
    void ModifyMoney(int32 delta) { money = uint32(int32(money) + delta); }
    Item* GetItemByPos(uint8 bag, uint8 slot) {
        auto itr = slots.find(uint16((bag << 8) | slot));
        return itr == slots.end() ? nullptr : itr->second;
    }
    Item* GetItemByGuid(ObjectGuid guid) const {
        for (auto const& entry : slots)
            if (entry.second && entry.second->guid == guid.GetCounter())
                return entry.second;
        return nullptr;
    }
    void RemoveItem(uint8 bag, uint8 slot, bool) {
        slots.erase(uint16((bag << 8) | slot));
    }
    void ItemRemovedQuestCheck(uint32, uint32) { ++questRemoved; }
    void MoveItemToInventory(ItemPosCountVec const& dest, Item* item, bool) {
        uint32 remaining = item->count;
        for (std::size_t i = 0; i < dest.size(); ++i) {
            ItemPosCount const& entry = dest[i];
            uint32 take = (i + 1 == dest.size()) ? remaining : entry.count;
            uint16 key = entry.pos;
            Item* target = GetItemByPos(uint8(key >> 8), uint8(key & 255));
            if (target && target != item) {
                target->count += take;
                remaining -= take;
                if (remaining == 0) {
                    item->SetOwnerGuid(GetObjectGuid());
                    item->FSetState(ITEM_REMOVED);
                }
                continue;
            }
            if (i + 1 == dest.size()) {
                slots[key] = item;
                item->SetCount(take);
                item->inWorld = true;
                item->FSetState(ITEM_CHANGED);
            } else {
                Item* clone = new Item();
                clone->guid = 9000 + uint32(slots.size());
                clone->entry = item->entry;
                clone->count = take;
                clone->owner = GetObjectGuid();
                clone->state = ITEM_NEW;
                clone->inWorld = true;
                slots[key] = clone;
                remaining -= take;
            }
        }
    }
    bool AppendInventoryToDB(CharacterPersistenceContext&) { return true; }
    bool AppendGoldToDB(CharacterPersistenceContext&) { return true; }
    bool AppendGoldProjectedToDB(CharacterPersistenceContext&, uint32) { return true; }
    uint32 money = 1000;
    int questRemoved = 0;
    std::map<uint16, Item*> slots;
    ObjectGuid m_guid;
    WorldSession* m_session;
};

void tearDown(Player& player, MasterPlayer& master) {
    for (auto& entry : player.slots)
        delete entry.second;
    player.slots.clear();
    for (Item* item : master.items)
        delete item;
    master.items.clear();
    master.byItemGuid.clear();
}

struct ObjectAccessorStub {
    MasterPlayer* master = nullptr;
    MasterPlayer* FindMasterPlayer(ObjectGuid) const { return master; }
} sObjectAccessor;

struct ObjectMgrStub {
    std::map<uint32, Player*> players;
    std::map<uint32, uint32> accounts;
    uint32 nextMailId = 100;
    uint32 GenerateMailID() { return ++nextMailId; }
    Player* GetPlayer(ObjectGuid guid) {
        auto itr = players.find(guid.GetCounter());
        return itr == players.end() ? nullptr : itr->second;
    }
    uint32 GetPlayerAccountIdByGUID(ObjectGuid guid) {
        auto itr = accounts.find(guid.GetCounter());
        return itr == accounts.end() ? 0 : itr->second;
    }
} sObjectMgr;

struct LogStub { template<typename... A> void Out(A&&...) {} } sLog;

Database CharacterDatabase;
'''

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
static bool hasWrite(char const* needle) { return countWrites(needle) != 0; }
static std::string const* findParam(char const* sqlNeedle, std::size_t paramIndex) {
    for (auto const& write : CharacterPersistenceContext::g_lastWrites) {
        if (write.sql.find(sqlNeedle) == std::string::npos)
            continue;
        if (paramIndex < write.params.size())
            return &write.params[paramIndex];
        return nullptr;
    }
    return nullptr;
}
static Item* makeItem(uint32 guid, uint32 entry, uint32 owner) {
    Item* item = new Item();
    item->guid = guid;
    item->entry = entry;
    item->owner = ObjectGuid(HIGHGUID_PLAYER, owner);
    return item;
}
static Mail* makeMail(uint32 id, uint32 sender, uint32 receiver,
                      std::vector<std::pair<uint32, uint32>> const& attachments,
                      uint32 cod = 0, uint8 messageType = MAIL_NORMAL,
                      uint32 itemTextId = 0) {
    Mail* mail = new Mail();
    mail->messageID = id;
    mail->messageType = messageType;
    mail->sender = sender;
    mail->receiverGuid = ObjectGuid(HIGHGUID_PLAYER, receiver);
    mail->subject = "Returned";
    mail->itemTextId = itemTextId;
    mail->COD = cod;
    mail->deliver_time = 0;
    mail->expire_time = 1;
    for (auto const& attachment : attachments)
        mail->AddItem(attachment.first, attachment.second);
    return mail;
}
static void resetState() {
    CharacterPersistenceContext::g_result = SqlBatchResult();
    CharacterPersistenceContext::g_lastWrites.clear();
    CharacterPersistenceContext::g_executeCalls = 0;
    g_recoveryRecords.clear();
    
    g_deletedMails = 0;
    sObjectAccessor.master = nullptr;
    sObjectMgr.players.clear();
    sObjectMgr.accounts.clear();
}

int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";

    if (scenario == "return_online_two_items") {
        resetState();
        Player returner;
        MasterPlayer actorMaster;
        MasterPlayer senderMaster;
        returner.GetSession()->master = &actorMaster;
        Player sender;
        sObjectMgr.players[800] = &sender;
        sObjectAccessor.master = &senderMaster;
        Item* first = makeItem(11, 111, 500);
        Item* second = makeItem(12, 112, 500);
        actorMaster.AddMItem(first);
        actorMaster.AddMItem(second);
        Mail* mail = makeMail(21, 800, 500, {{11, 111}, {12, 112}}, 30, MAIL_NORMAL, 77);
        MailPersistenceStatus status = ReturnMailToSender(returner, mail);
        check(status == MailPersistenceStatus::Committed && CharacterPersistenceContext::g_executeCalls == 1,
              "one committed execution");
        check(countWrites("DELETE FROM `mail` WHERE `id` = ? AND `receiver_guid` = ?") == 1,
              "guarded old mail delete");
        check(countWrites("DELETE FROM `mail_items` WHERE `mail_id` = ? AND `receiver_guid` = ?") == 1,
              "old links delete");
        check(countWrites("UPDATE `item_instance` SET `owner_guid` = ?") == 2,
              "every attachment ownership transferred");
        check(countWrites("INSERT INTO `mail`") == 1 && countWrites("INSERT INTO `mail_items`") == 2,
              "new mail and every attachment linked");
        std::string const* cod = findParam("INSERT INTO `mail`", 12);
        check(cod && *cod == "0", "COD never paid on a return");
        check(actorMaster.items.empty(), "old attachment registry cleaned");
        check(senderMaster.mails.size() == 1 && senderMaster.mails[0]->items.size() == 2,
              "returned mail published to online sender with all attachments");
        check(senderMaster.items.size() == 2 && senderMaster.deliverTimes == 1,
              "sender item registry and delivery published");
        check(first->GetOwnerGuid().GetCounter() == 800 &&
              second->GetOwnerGuid().GetCounter() == 800, "ownership published on commit");
        check(g_deletedItems == 0 && g_deletedMails == 1, "items kept, old mail object consumed");
    }
    else if (scenario == "return_offline_two_items") {
        resetState();
        Player returner;
        MasterPlayer actorMaster;
        returner.GetSession()->master = &actorMaster;
        sObjectMgr.accounts[900] = 42;
        Item* first = makeItem(31, 131, 500);
        Item* second = makeItem(32, 132, 500);
        actorMaster.AddMItem(first);
        actorMaster.AddMItem(second);
        Mail* mail = makeMail(22, 900, 500, {{31, 131}, {32, 132}});
        MailPersistenceStatus status = ReturnMailToSender(returner, mail);
        check(status == MailPersistenceStatus::Committed, "commit");
        check(hasWrite("INSERT INTO `mail`") && countWrites("INSERT INTO `mail_items`") == 2,
              "offline sender gets the returned mail row and links");
        check(actorMaster.items.empty(), "registry cleaned");
        check(g_deletedItems == 2, "offline staging items disposed after commit");
        check(g_deletedMails == 1, "old mail object consumed");
    }
    else if (scenario == "return_missing_sender_disposes") {
        resetState();
        Player returner;
        MasterPlayer actorMaster;
        returner.GetSession()->master = &actorMaster;
        Item* first = makeItem(41, 141, 500);
        Item* second = makeItem(42, 142, 500);
        actorMaster.AddMItem(first);
        actorMaster.AddMItem(second);
        Mail* mail = makeMail(23, 901, 500, {{41, 141}, {42, 142}}, 0, MAIL_NORMAL, 88);
        MailPersistenceStatus status = ReturnMailToSender(returner, mail);
        check(status == MailPersistenceStatus::Committed, "commit");
        check(!hasWrite("INSERT INTO `mail`"), "no new mail for missing sender");
        check(countWrites("DELETE FROM `item_instance` WHERE `guid` = ? AND `owner_guid` = ?") == 2,
              "every attachment row disposed");
        check(countWrites("DELETE FROM `item_text` WHERE `id` = ?") == 1,
              "selected body disposed");
        check(actorMaster.items.empty() && g_deletedItems == 2,
              "attachment objects destroyed after commit");
    }
    else if (scenario == "return_non_normal_disposes") {
        resetState();
        Player returner;
        MasterPlayer actorMaster;
        returner.GetSession()->master = &actorMaster;
        Item* item = makeItem(51, 151, 500);
        actorMaster.AddMItem(item);
        Mail* mail = makeMail(24, 0, 500, {{51, 151}}, 0, MAIL_CREATURE);
        MailPersistenceStatus status = ReturnMailToSender(returner, mail);
        check(status == MailPersistenceStatus::Committed &&
              !hasWrite("INSERT INTO `mail`") && g_deletedItems == 1,
              "non-player mail disposed with its attachment");
    }
    else if (scenario == "return_rejected_publishes_nothing") {
        resetState();
        CharacterPersistenceContext::g_result.outcome = SqlWriteOutcome::Failed;
        Player returner;
        MasterPlayer actorMaster;
        returner.GetSession()->master = &actorMaster;
        Item* item = makeItem(61, 161, 500);
        actorMaster.AddMItem(item);
        Mail* mail = makeMail(25, 800, 500, {{61, 161}});
        MailPersistenceStatus status = ReturnMailToSender(returner, mail);
        check(status == MailPersistenceStatus::Rejected, "known failure classified");
        check(CharacterPersistenceContext::g_executeCalls == 1, "one execution attempt");
        check(actorMaster.items.size() == 1 && g_deletedItems == 0 && g_deletedMails == 0,
              "no publication or destruction");
        check(g_recoveryRecords.empty(), "known failure does not block");
    }
    else if (scenario == "return_unknown_blocks") {
        resetState();
        CharacterPersistenceContext::g_result.outcome = SqlWriteOutcome::OutcomeUnknown;
        Player returner;
        MasterPlayer actorMaster;
        returner.GetSession()->master = &actorMaster;
        Item* item = makeItem(71, 171, 500);
        actorMaster.AddMItem(item);
        sObjectMgr.accounts[800] = 42;
        Mail* mail = makeMail(26, 800, 500, {{71, 171}});
        MailPersistenceStatus status = ReturnMailToSender(returner, mail);
        check(status == MailPersistenceStatus::OutcomeUnknown, "unknown classified");
        check(g_recoveryRecords.size() == 2 &&
              g_recoveryRecords[0].guids == std::vector<uint32>({500}) &&
              g_recoveryRecords[1].guids == std::vector<uint32>({800}),
              "initiator and receiver blocked");
        check(actorMaster.items.size() == 1 && g_deletedItems == 0,
              "no publication/replay");
    }
    else { std::cerr << "unknown\n"; return 2; }

    if (failures) { std::cerr << "failures=" << failures << "\n"; return 1; }
    std::cout << "mail-return " << scenario << ": PASS\n";
    return 0;
}
'''


TAKE_MAIN = r'''
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
static bool hasWrite(char const* needle) { return countWrites(needle) != 0; }
static std::string const* findParam(char const* sqlNeedle, std::size_t paramIndex) {
    for (auto const& write : CharacterPersistenceContext::g_lastWrites) {
        if (write.sql.find(sqlNeedle) == std::string::npos)
            continue;
        if (paramIndex < write.params.size())
            return &write.params[paramIndex];
        return nullptr;
    }
    return nullptr;
}
static Item* makeItem(uint32 guid, uint32 entry, uint32 owner) {
    Item* item = new Item();
    item->guid = guid;
    item->entry = entry;
    item->owner = ObjectGuid(HIGHGUID_PLAYER, owner);
    return item;
}
static Mail* makeMail(uint32 id, uint32 sender, uint32 receiver, uint32 cod) {
    Mail* mail = new Mail();
    mail->messageID = id;
    mail->messageType = MAIL_NORMAL;
    mail->sender = sender;
    mail->receiverGuid = ObjectGuid(HIGHGUID_PLAYER, receiver);
    mail->subject = "COD";
    mail->COD = cod;
    mail->deliver_time = 0;
    mail->expire_time = 1;
    mail->AddItem(11, 111);
    return mail;
}
static void resetState() {
    CharacterPersistenceContext::g_result = SqlBatchResult();
    CharacterPersistenceContext::g_lastWrites.clear();
    CharacterPersistenceContext::g_executeCalls = 0;
    g_recoveryRecords.clear();
    g_deletedItems = 0;
    g_deletedMails = 0;
    sObjectAccessor.master = nullptr;
    sObjectMgr.players.clear();
    sObjectMgr.accounts.clear();
}

int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";

    if (scenario == "take_with_cod") {
        resetState();
        Player player;
        MasterPlayer master;
        player.GetSession()->master = &master;
        sObjectMgr.accounts[800] = 42;
        Item* item = makeItem(11, 111, 500);
        master.AddMItem(item);
        Mail* mail = makeMail(30, 800, 500, 30);
        ItemPosCountVec dest(1);
        uint32 count = 0, itemId = 0;
        MailPersistenceStatus status = TakeMailAttachment(player, mail, 11, dest, count, itemId);
        check(status == MailPersistenceStatus::Committed &&
              CharacterPersistenceContext::g_executeCalls == 1, "one committed execution");
        check(countWrites("UPDATE `mail` SET `item_text_id`") == 1, "complete pending mail state staged");
        check(countWrites("DELETE FROM `mail_items` WHERE `item_guid` = ?") == 1,
              "attachment link removed in unit");
        check(countWrites("INSERT INTO `mail`") == 1, "COD payment mail inserted");
        std::string const* money = findParam("INSERT INTO `mail`", 11);
        std::string const* receiver = findParam("INSERT INTO `mail`", 5);
        check(money && *money == "30", "COD money transferred by mail");
        check(receiver && *receiver == "800", "COD paid to the sender GUID");
        check(player.GetMoney() == 970, "COD deducted");
        check(mail->COD == 0 && mail->items.empty(), "mail state published on commit");
        check(master.items.empty(), "attachment leaves the mail registry");
        check(count == 1 && itemId == 111, "taken count/item id reported");
        check(g_deletedItems == 0, "attachment item object kept for inventory");
    }
    else if (scenario == "take_zero_cod") {
        resetState();
        Player player;
        MasterPlayer master;
        player.GetSession()->master = &master;
        master.AddMItem(makeItem(11, 111, 500));
        Mail* mail = makeMail(31, 800, 500, 0);
        ItemPosCountVec dest(1);
        uint32 count = 0, itemId = 0;
        MailPersistenceStatus status = TakeMailAttachment(player, mail, 11, dest, count, itemId);
        check(status == MailPersistenceStatus::Committed, "commit");
        check(!hasWrite("INSERT INTO `mail`") && player.GetMoney() == 1000,
              "zero COD pays nothing");
        check(countWrites("UPDATE `mail` SET `item_text_id`") == 1, "pending mail state staged for zero COD");
    }
    else if (scenario == "take_rejected_refunds") {
        resetState();
        CharacterPersistenceContext::g_result.outcome = SqlWriteOutcome::Failed;
        Player player;
        MasterPlayer master;
        player.GetSession()->master = &master;
        sObjectMgr.accounts[800] = 42;
        master.AddMItem(makeItem(11, 111, 500));
        Mail* mail = makeMail(32, 800, 500, 30);
        ItemPosCountVec dest(1);
        uint32 count = 0, itemId = 0;
        MailPersistenceStatus status = TakeMailAttachment(player, mail, 11, dest, count, itemId);
        check(status == MailPersistenceStatus::Rejected, "known failure classified");
        check(player.GetMoney() == 1000, "known failure refunds the COD");
        check(mail->COD == 30 && g_deletedItems == 0, "no publication");
        check(g_recoveryRecords.empty(), "known failure does not block");
    }
    else if (scenario == "take_unknown_blocks") {
        resetState();
        CharacterPersistenceContext::g_result.outcome = SqlWriteOutcome::OutcomeUnknown;
        Player player;
        MasterPlayer master;
        player.GetSession()->master = &master;
        sObjectMgr.accounts[800] = 42;
        master.AddMItem(makeItem(11, 111, 500));
        Mail* mail = makeMail(33, 800, 500, 30);
        ItemPosCountVec dest(1);
        uint32 count = 0, itemId = 0;
        MailPersistenceStatus status = TakeMailAttachment(player, mail, 11, dest, count, itemId);
        check(status == MailPersistenceStatus::OutcomeUnknown, "unknown classified");
        check(g_recoveryRecords.size() == 2 &&
              g_recoveryRecords[0].guids == std::vector<uint32>({500}) &&
              g_recoveryRecords[1].guids == std::vector<uint32>({800}) &&
              g_recoveryRecords[1].accountIds == std::vector<uint32>({42}),
              "initiator and counterparty blocked");
        check(mail->COD == 30, "no publication on unknown");
        Item* held = player.GetItemByPos(0, 0);
        check(!held || master.GetMItem(11) != held,
              "unknown take must not leave the same Item owned by both destructors");
        tearDown(player, master);
        check(g_deletedItems == 1, "unknown empty-slot Item destroyed exactly once");
    }
    else if (scenario == "take_merge_unknown_quarantine") {
        resetState();
        CharacterPersistenceContext::g_result.outcome = SqlWriteOutcome::OutcomeUnknown;
        Player player;
        MasterPlayer master;
        player.GetSession()->master = &master;
        sObjectMgr.accounts[800] = 42;
        Item* existing = makeItem(99, 111, 500);
        existing->count = 5;
        player.slots[0] = existing;
        Item* mailItem = makeItem(11, 111, 500);
        mailItem->count = 2;
        master.AddMItem(mailItem);
        Mail* mail = makeMail(37, 800, 500, 0);
        ItemPosCountVec dest(1);
        uint32 count = 0, itemId = 0;
        MailPersistenceStatus status =
            TakeMailAttachment(player, mail, 11, dest, count, itemId);
        check(status == MailPersistenceStatus::OutcomeUnknown, "merge unknown");
        check(master.GetMItem(11) == nullptr && existing->count == 7,
              "source quarantined, merged stack retained once");
        tearDown(player, master);
        check(g_deletedItems == 2, "merge unknown destroys source and stack once");
    }
    else if (scenario == "take_split_unknown_quarantine") {
        resetState();
        CharacterPersistenceContext::g_result.outcome = SqlWriteOutcome::OutcomeUnknown;
        Player player;
        MasterPlayer master;
        player.GetSession()->master = &master;
        sObjectMgr.accounts[800] = 42;
        Item* mailItem = makeItem(11, 111, 500);
        mailItem->count = 3;
        master.AddMItem(mailItem);
        Mail* mail = makeMail(38, 800, 500, 0);
        ItemPosCountVec dest;
        dest.emplace_back(0, 1);
        dest.emplace_back(1, 1);
        dest.emplace_back(2, 1);
        uint32 count = 0, itemId = 0;
        MailPersistenceStatus status =
            TakeMailAttachment(player, mail, 11, dest, count, itemId);
        check(status == MailPersistenceStatus::OutcomeUnknown, "split unknown");
        check(master.GetMItem(11) == nullptr && player.slots.size() == 3,
              "split parts live only in the Player");
        tearDown(player, master);
        check(g_deletedItems == 3, "split unknown destroys every part once");
    }
    else if (scenario == "take_merge_rejected_restores_stack") {
        resetState();
        CharacterPersistenceContext::g_result.outcome = SqlWriteOutcome::Failed;
        Player player;
        MasterPlayer master;
        player.GetSession()->master = &master;
        sObjectMgr.accounts[800] = 42;
        Item* existing = makeItem(99, 111, 500);
        existing->count = 5;
        existing->state = ITEM_UNCHANGED;
        player.slots[0] = existing;
        Item* mailItem = makeItem(11, 111, 500);
        mailItem->count = 2;
        master.AddMItem(mailItem);
        Mail* mail = makeMail(35, 800, 500, 30);
        ItemPosCountVec dest(1);
        uint32 count = 0, itemId = 0;
        MailPersistenceStatus status = TakeMailAttachment(player, mail, 11, dest, count, itemId);
        check(status == MailPersistenceStatus::Rejected, "merge case rejected");
        check(existing->count == 5 && existing->state == ITEM_UNCHANGED,
              "merged stack count/state restored");
        check(player.slots[0] == existing, "existing stack remains in its slot");
        check(master.GetMItem(11) == mailItem && mailItem->state != ITEM_REMOVED,
              "mailed item registry ownership restored");
        check(mailItem->GetOwnerGuid().GetCounter() == 500,
              "mailed item owner restored");
        check(player.questRemoved == 1, "inventory quest counter restored");
        check(player.GetMoney() == 1000 && mail->COD == 30,
              "money and mail unchanged");
    }
    else if (scenario == "take_retry_after_rejection") {
        resetState();
        Player player;
        MasterPlayer master;
        player.GetSession()->master = &master;
        sObjectMgr.accounts[800] = 42;
        Item* mailItem = makeItem(11, 111, 500);
        mailItem->count = 2;
        master.AddMItem(mailItem);
        Mail* mail = makeMail(36, 800, 500, 30);
        ItemPosCountVec dest(1);
        uint32 count = 0, itemId = 0;
        CharacterPersistenceContext::g_result.outcome = SqlWriteOutcome::Failed;
        MailPersistenceStatus first =
            TakeMailAttachment(player, mail, 11, dest, count, itemId);
        check(first == MailPersistenceStatus::Rejected, "first attempt rejected");
        check(master.GetMItem(11) == mailItem && mail->COD == 30 &&
              player.GetMoney() == 1000 && player.slots.empty(),
              "rollback leaves retryable state");
        CharacterPersistenceContext::g_result.outcome = SqlWriteOutcome::Succeeded;
        MailPersistenceStatus second =
            TakeMailAttachment(player, mail, 11, dest, count, itemId);
        check(second == MailPersistenceStatus::Committed, "retry commits");
        check(player.GetMoney() == 970 && mail->COD == 0 &&
              master.GetMItem(11) == nullptr && count == 2,
              "retry pays COD once and moves the item once");
    }
    else if (scenario == "take_missing_item") {
        resetState();
        Player player;
        MasterPlayer master;
        player.GetSession()->master = &master;
        Mail* mail = makeMail(34, 800, 500, 30);
        ItemPosCountVec dest(1);
        uint32 count = 0, itemId = 0;
        MailPersistenceStatus status = TakeMailAttachment(player, mail, 11, dest, count, itemId);
        check(status == MailPersistenceStatus::Rejected, "missing attachment rejected");
        check(CharacterPersistenceContext::g_executeCalls == 0, "no execution attempt");
        check(player.GetMoney() == 1000, "no money movement");
    }
    else { std::cerr << "unknown\n"; return 2; }

    if (failures) { std::cerr << "failures=" << failures << "\n"; return 1; }
    std::cout << "mail-take " << scenario << ": PASS\n";
    return 0;
}
'''


def extract():
    source = SOURCE.read_text(encoding="utf-8")
    namespace_end = source.index("\n}\n\nMailPersistenceStatus ClassifyMailBatch")
    helpers = source[source.index("struct MailRow"):namespace_end]
    classify = source[source.index("MailPersistenceStatus ClassifyMailBatch"):
                      source.index("bool AppendItemTextSave(")]
    ret = source[source.index("MailPersistenceStatus ReturnMailToSender("):
                 source.index("MailPersistenceStatus TakeMailAttachment(")]
    quarantine = source.index("void QuarantineUnknownTake")
    take_start = source.rindex("namespace\n{\n", 0, quarantine)
    take = source[take_start:
                  source.index("MailPersistenceStatus TakeMailMoney(")]
    return helpers, classify, ret, take


@unittest.skipUnless(shutil.which("cmake"), "cmake not available")
class MailReturnExecutionTests(unittest.TestCase):
    def test_mail_return(self):
        helpers, classify, ret, _ = extract()
        program = (PREAMBLE + "\nnamespace mail_helpers {\n" + helpers + "\n}\n"
                   "using namespace mail_helpers;\n" + classify + "\n" + ret + "\n" + MAIN)
        with tempfile.TemporaryDirectory(prefix="fn05h-mail-ret-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(mail_return_exec CXX)\n"
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
            for scenario in ["return_online_two_items", "return_offline_two_items",
                             "return_missing_sender_disposes", "return_non_normal_disposes",
                             "return_rejected_publishes_nothing", "return_unknown_blocks"]:
                r = subprocess.run([str(exe), scenario], text=True,
                                   capture_output=True, timeout=30)
                if r.returncode != 0 or ("mail-return " + scenario + ": PASS") not in r.stdout:
                    failures.append(scenario + "\n" + r.stdout + r.stderr)
            self.assertFalse(failures, "\n".join(failures))

    def test_mail_take_attachment(self):
        helpers, classify, _, take = extract()
        program = (PREAMBLE + "\nnamespace mail_helpers {\n" + helpers + "\n}\n"
                   "using namespace mail_helpers;\n" + classify + "\n" + take + "\n" + TAKE_MAIN)
        with tempfile.TemporaryDirectory(prefix="fn05h-mail-take-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(mail_take_exec CXX)\n"
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
            for scenario in ["take_with_cod", "take_zero_cod",
                             "take_rejected_refunds", "take_unknown_blocks",
                             "take_merge_unknown_quarantine",
                             "take_split_unknown_quarantine",
                             "take_merge_rejected_restores_stack",
                             "take_retry_after_rejection",
                             "take_missing_item"]:
                r = subprocess.run([str(exe), scenario], text=True,
                                   capture_output=True, timeout=30)
                if r.returncode != 0 or ("mail-take " + scenario + ": PASS") not in r.stdout:
                    failures.append(scenario + "\n" + r.stdout + r.stderr)
            self.assertFalse(failures, "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
