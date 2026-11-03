"""Composed inventory-completion regression for non-world owners.

The real Player::AppendInventoryToDB body and Item queue/acknowledgment methods
are extracted.  Item::AppendSaveToDB is a small model that registers the same
commit acknowledgment; it is not used to prove the queue contract.
"""

import pathlib
import subprocess
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SAVE = (ROOT / "src/game/Database/CharacterSavePersistence.cpp").read_text(encoding="utf-8")
ITEM = (ROOT / "src/game/Objects/Item.cpp").read_text(encoding="utf-8")


def extract_save():
    start = SAVE.index("bool Player::AppendInventoryToDB(")
    end = SAVE.index("void Player::AppendIdentityGenerationPublication(", start)
    return SAVE[start:end]


def extract_item(signature, end_signature):
    start = ITEM.index(signature)
    end = ITEM.index(end_signature, start)
    return ITEM[start:end]


PREAMBLE = r'''
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <iostream>
#include <map>
#include <string>
#include <vector>

using uint8 = unsigned char;
using uint16 = unsigned short;
using uint32 = unsigned int;
using uint64 = unsigned long long;
using int16 = short;

enum { HIGHGUID_PLAYER = 0, INVENTORY_SLOT_BAG_0 = 0 };
enum { PLAYER_SLOTS_COUNT = 100, BUYBACK_SLOT_START = 74, BUYBACK_SLOT_END = 86 };
enum { LOG_BASIC = 0, LOG_LVL_ERROR = 1 };
enum { CHEAT_ACTION_LOG = 1, CHEAT_ACTION_REPORT_GMS = 2 };
enum ItemUpdateState { ITEM_UNCHANGED = 0, ITEM_CHANGED = 1, ITEM_NEW = 2, ITEM_REMOVED = 3 };
enum class SqlAffectedRowsPolicy { Any, ExactlyOne };
enum class SqlWriteOutcome { Succeeded, Failed, OutcomeUnknown };
enum class SqlBatchStage { Complete, Incomplete };

struct ObjectGuid {
    uint32 value = 0;
    ObjectGuid() = default;
    ObjectGuid(int, uint32 v) : value(v) {}
    uint32 GetCounter() const { return value; }
    std::string GetString() const { return std::to_string(value); }
    bool operator==(ObjectGuid const& o) const { return value == o.value; }
    bool operator!=(ObjectGuid const& o) const { return value != o.value; }
};

struct CharacterIdentitySnapshot {
    uint32 guid = 0;
    uint32 accountId = 0;
    std::string canonicalName;
    uint64 generation = 0;
};

struct SqlStatementID {};
struct SqlBatch;
struct SqlStatement {
    template<typename T> SqlStatement& addUInt8(T) { return *this; }
    template<typename T> SqlStatement& addUInt16(T) { return *this; }
    template<typename T> SqlStatement& addUInt32(T) { return *this; }
    template<typename T> SqlStatement& addUInt64(T) { return *this; }
    SqlStatement& addString(std::string const&) { return *this; }
    bool AppendToCheckedBatch(SqlBatch&, SqlAffectedRowsPolicy) { return true; }
};
std::vector<std::string> g_sql;
struct Database {
    SqlStatement CreateStatement(SqlStatementID&, char const* sql) {
        g_sql.push_back(sql);
        return {};
    }
};
Database CharacterDatabase;
struct SqlBatch {
    Database* pointer = &CharacterDatabase;
    explicit SqlBatch(Database&) {}
    Database* GetDatabase() { return pointer; }
};
struct SqlBatchResult {
    SqlWriteOutcome outcome = SqlWriteOutcome::Succeeded;
    SqlBatchStage stage = SqlBatchStage::Complete;
};

enum class CharacterRecoveryKind { WholeSave };
struct CharacterRecoveryRecord {
    CharacterRecoveryKind kind = CharacterRecoveryKind::WholeSave;
    std::vector<uint32> guids;
    std::vector<uint32> accountIds;
    uint64 expectedGeneration = 0;
    std::string detail;
};
std::vector<CharacterRecoveryRecord> g_recovery;
bool BlockCharacterRecovery(CharacterRecoveryRecord const& record) {
    g_recovery.push_back(record);
    return true;
}

struct Bag { uint32 GetGUIDLow() const { return 0; } };
struct Item;
struct CharacterPersistenceContext;
int g_destroyed = 0;
struct LogStub { template<typename... A> void Out(A&&...) {} } sLog;
class Player;

struct Item {
    uint32 guid = 0;
    uint32 entry = 0;
    uint32 count = 1;
    ObjectGuid ownerGuid;
    int16 uQueuePos = -1;
    ItemUpdateState uState = ITEM_UNCHANGED;
    bool inWorld = false;
    uint32 GetGUIDLow() const { return guid; }
    std::string GetGuidStr() const { return std::to_string(guid); }
    uint32 GetEntry() const { return entry; }
    uint32 GetCount() const { return count; }
    uint8 GetBagSlot() const { return 0; }
    uint8 GetSlot() const { return 0; }
    ObjectGuid const& GetOwnerGuid() const { return ownerGuid; }
    Player* GetOwner() const { return nullptr; }   // non-world owner lookup fails
    Bag* GetContainer() const { return nullptr; }
    void SetEnchantmentDuration(uint32, uint32) {}
    ItemUpdateState GetState() const { return uState; }
    void FSetState(ItemUpdateState state) { uState = state; }
    bool IsInUpdateQueue() const { return uQueuePos != -1; }
    int16 GetQueuePos() const { return uQueuePos; }
    bool IsInWorld() const { return inWorld; }
    void RemoveFromWorld() { inWorld = false; }
    void SetState(ItemUpdateState state, Player* player = nullptr);
    void AddToUpdateQueueOf(Player* player);
    void RemoveFromUpdateQueueOf(Player* player);
    bool AcknowledgeSave(Player* owner = nullptr);
    bool AppendSaveToDB(CharacterPersistenceContext& context, Player* owner = nullptr);
    ~Item() { ++g_destroyed; }
};

struct EnchantDuration {
    Item* item = nullptr;
    uint32 slot = 0;
    uint32 leftduration = 0;
};
struct WorldSession {
    void ProcessAnticheatAction(char const*, char const*, uint32) {}
};
class Player {
public:
    Player() : m_guid(HIGHGUID_PLAYER, 500), m_session(new WorldSession()) {}
    ~Player() { delete m_session; }
    uint32 GetGUIDLow() const { return m_guid.GetCounter(); }
    ObjectGuid const& GetObjectGuid() const { return m_guid; }
    char const* GetName() const { return "NonWorld"; }
    std::string GetGuidStr() const { return "500"; }
    uint64 GetIdentityGeneration() const { return 9; }
    WorldSession* GetSession() const { return m_session; }
    Item* GetItemByPos(uint8 bag, uint8 slot) {
        if (bag != INVENTORY_SLOT_BAG_0) return nullptr;
        return slot < PLAYER_SLOTS_COUNT ? m_items[slot] : nullptr;
    }
    std::vector<Item*>& GetItemUpdateQueue() { return m_itemUpdateQueue; }
    bool IsItemUpdateQueueBlocked() const { return m_itemUpdateQueueBlocked; }
    bool AppendInventoryToDB(CharacterPersistenceContext& context);
    ObjectGuid m_guid;
    WorldSession* m_session;
    Item* m_items[PLAYER_SLOTS_COUNT] = {nullptr};
    std::vector<Item*> m_itemUpdateQueue;
    bool m_itemUpdateQueueBlocked = false;
    std::vector<EnchantDuration> m_enchantDuration;
};

CharacterIdentitySnapshot SaveIdentity(Player const& player) {
    CharacterIdentitySnapshot identity;
    identity.guid = player.GetGUIDLow();
    identity.accountId = 42;
    identity.canonicalName = player.GetName();
    identity.generation = player.GetIdentityGeneration();
    return identity;
}
bool SaveIdentityMatches(Player const&, CharacterIdentitySnapshot const&) { return true; }

SqlWriteOutcome g_status = SqlWriteOutcome::Succeeded;

struct CharacterPersistenceContext {
    explicit CharacterPersistenceContext(Database& d) : db(d), batch{d} {}
    bool HasIdentityGuard(CharacterIdentitySnapshot const&) const { return true; }
    bool WasSubmitted() const { return false; }
    SqlBatch& Batch() { return batch; }
    void Invalidate() { invalid = true; }
    void OnCommit(std::function<void()> action) { actions.push_back(std::move(action)); }
    SqlBatchResult Execute() {
        if (g_status == SqlWriteOutcome::Succeeded)
            for (auto& action : actions)
                action();
        return SqlBatchResult{g_status, SqlBatchStage::Complete};
    }
    Database& db;
    SqlBatch batch;
    bool invalid = false;
    std::vector<std::function<void()>> actions;
};
bool Item::AppendSaveToDB(CharacterPersistenceContext& context, Player* owner) {
    if (uState == ITEM_UNCHANGED) {
        if (IsInUpdateQueue())
            context.OnCommit([this, owner]() { AcknowledgeSave(owner); });
        return true;
    }
    if (uState == ITEM_REMOVED) {
        context.OnCommit([this, owner]() {
            if (IsInUpdateQueue() && !AcknowledgeSave(owner))
                return;
            delete this;
        });
        return true;
    }
    context.OnCommit([this, owner]() { AcknowledgeSave(owner); });
    return true;
}
'''

MAIN = r'''
int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";
    Player player;
    Database db;
    CharacterPersistenceContext context(db);

    Item* item = new Item();
    item->guid = 11; item->entry = 111; item->count = 1;
    item->ownerGuid = player.GetObjectGuid();
    player.m_items[0] = item;

    if (scenario == "non_world_new") {
        item->SetState(ITEM_NEW, &player);
        if (!player.AppendInventoryToDB(context)) return 1;
        context.Execute();
        if (item->uState != ITEM_UNCHANGED || item->IsInUpdateQueue())
            return 2;
        if (!g_recovery.empty()) return 3;
        if (player.m_itemUpdateQueue.size() != 0) return 4;
        delete item;
        return 0;
    }
    if (scenario == "unrelated_survives") {
        item->SetState(ITEM_NEW, &player);
        if (!player.AppendInventoryToDB(context)) return 1;
        Item* unrelated = new Item();
        unrelated->guid = 22; unrelated->ownerGuid = player.GetObjectGuid();
        unrelated->SetState(ITEM_NEW, &player);      // newer, not staged
        SqlBatchResult result = context.Execute();
        if (result.outcome != SqlWriteOutcome::Succeeded) return 2;
        bool kept = false;
        for (Item* queued : player.m_itemUpdateQueue)
            if (queued == unrelated) kept = true;
        if (!kept) return 3;
        delete unrelated;
        delete item;
        return 0;
    }
    if (scenario == "removed_once") {
        item->SetState(ITEM_REMOVED, &player);
        if (!player.AppendInventoryToDB(context)) return 1;
        context.Execute();
        if (g_destroyed != 1) return 2;
        return 0;
    }
    if (scenario == "second_save") {
        item->SetState(ITEM_NEW, &player);
        if (!player.AppendInventoryToDB(context)) return 1;
        context.Execute();
        g_sql.clear();
        item->SetState(ITEM_CHANGED, &player);
        if (!player.AppendInventoryToDB(context)) return 2;
        context.Execute();
        bool insert = false, update = false;
        for (std::string const& sql : g_sql) {
            if (sql.find("INSERT INTO `character_inventory`") != std::string::npos)
                insert = true;
            if (sql.find("UPDATE `character_inventory`") != std::string::npos)
                update = true;
        }
        if (insert || !update) return 3;
        delete item;
        return 0;
    }
    if (scenario == "ack_failure") {
        item->SetState(ITEM_NEW, &player);
        player.m_itemUpdateQueueBlocked = true;
        if (!player.AppendInventoryToDB(context)) return 1;
        context.Execute();
        if (g_recovery.empty()) return 2;
        if (player.m_itemUpdateQueue.empty()) return 3;
        delete item;
        return 0;
    }
    if (scenario == "unchanged_rejection") {
        item->ownerGuid = player.GetObjectGuid();
        player.m_items[0] = item;
        player.m_itemUpdateQueue.push_back(item);
        item->uQueuePos = 0;
        item->uState = ITEM_UNCHANGED;
        g_status = SqlWriteOutcome::Failed;
        if (!player.AppendInventoryToDB(context)) return 1;
        context.Execute();
        if (!item->IsInUpdateQueue() || item->uState != ITEM_UNCHANGED) {
            std::cerr << "unchanged state=" << int(item->uState)
                      << " queue=" << int(item->uQueuePos)
                      << " size=" << player.m_itemUpdateQueue.size() << "\n";
            return 2;
        }
        if (!g_recovery.empty()) return 3;
        delete item;
        return 0;
    }
    if (scenario == "unknown_outcome") {
        item->SetState(ITEM_NEW, &player);
        g_status = SqlWriteOutcome::OutcomeUnknown;
        if (!player.AppendInventoryToDB(context)) return 1;
        context.Execute();
        if (item->uState != ITEM_NEW || !item->IsInUpdateQueue())
            return 2;
        delete item;
        return 0;
    }
    if (scenario == "known_rejection") {
        item->SetState(ITEM_NEW, &player);
        g_status = SqlWriteOutcome::Failed;
        if (!player.AppendInventoryToDB(context)) return 1;
        context.Execute();
        if (item->uState != ITEM_NEW || !item->IsInUpdateQueue()) return 2;
        if (!g_recovery.empty()) return 3;
        delete item;
        return 0;
    }
    std::cerr << "unknown\n";
    return 2;
}
'''


class NonWorldInventoryCompletionTests(unittest.TestCase):
    def test_composed_completion(self):
        item_body = (
            extract_item("bool Item::AcknowledgeSave(", "void Item::SetState(") +
            extract_item("void Item::SetState(", "void Item::AddToUpdateQueueOf(") +
            extract_item("void Item::AddToUpdateQueueOf(", "void Item::RemoveFromUpdateQueueOf(") +
            extract_item("void Item::RemoveFromUpdateQueueOf(", "uint8 Item::GetBagSlot()")
        )
        with tempfile.TemporaryDirectory(prefix="fn05h-nonworld-") as td:
            work = pathlib.Path(td)
            (work / "probe.cpp").write_text(
                PREAMBLE + "\n" + item_body + "\n" + extract_save() + "\n" + MAIN,
                encoding="utf-8")
            (work / "CMakeLists.txt").write_text(textwrap.dedent("""
                cmake_minimum_required(VERSION 3.20)
                project(nonworld_probe CXX)
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
            for scenario in ("non_world_new", "second_save", "unrelated_survives", "removed_once",
                             "unchanged_rejection", "unknown_outcome",
                             "ack_failure", "known_rejection"):
                run = subprocess.run([str(exe), scenario], stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True)
                self.assertEqual(run.returncode, 0, scenario + "\n" + run.stdout)


if __name__ == "__main__":
    unittest.main()
