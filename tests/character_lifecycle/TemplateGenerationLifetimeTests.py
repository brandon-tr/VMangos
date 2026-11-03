"""Runtime lifetime check for Mail::prepareTemplateItems.

The real body is extracted; admission, context, and generated Item ownership are
instrumented so assembly failure, known rejection and uncertainty are visible.
"""

import pathlib
import subprocess
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "src/game/Mail/MailPersistence.cpp").read_text(encoding="utf-8")


def extract():
    start = SOURCE.index("void Mail::prepareTemplateItems(")
    end = SOURCE.index("\n}\n", SOURCE.index("Mail template item generation failed", start)) + 3
    return SOURCE[start:end]


PREAMBLE = r'''
#include <cstdint>
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
using time_t = long long;

enum { HIGHGUID_PLAYER = 0 };
enum { LOG_BASIC = 0, LOG_LVL_ERROR = 1 };
enum { LootTemplates_Mail = 1 };
enum MailCheckMask { MAIL_CHECK_MASK_NONE = 0 };
enum class MailPersistenceStatus { Committed, Rejected, OutcomeUnknown };
enum class CharacterRecoveryKind { WholeSave };
enum class AccountLifecycleKind { Mutation, Deletion };
enum class SqlAffectedRowsPolicy { Any, ExactlyOne };
enum ItemUpdateState { ITEM_UNCHANGED = 0, ITEM_CHANGED = 1, ITEM_NEW = 2, ITEM_REMOVED = 3 };

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
bool g_admit = true;

bool BlockCharacterRecovery(CharacterRecoveryRecord const& record) {
    g_recovery.push_back(record);
    g_blockHeld = g_admissionHeld;
    return true;
}

class AccountLifecycleLease;
struct CharacterPersistenceAdmission {
    bool admit = true;
    bool Admit(std::vector<uint32> const&, AccountLifecycleKind,
               AccountLifecycleLease const* = nullptr) {
        if (!admit || !g_admit) return false;
        g_admissionHeld = true;
        return true;
    }
    ~CharacterPersistenceAdmission() { g_admissionHeld = false; }
};

struct SqlStatementID {};
struct SqlBatch;
struct SqlStatement {
    template<typename T> SqlStatement& addUInt32(T) { return *this; }
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
    void OnCommit(std::function<void()> action) { actions.push_back(std::move(action)); }
    SqlBatchResult Execute() {
        if (g_status == MailPersistenceStatus::Committed)
            for (auto& action : actions)
                action();
        return {};
    }
    SqlBatch batch;
    std::vector<std::function<void()>> actions;
};

struct MailItemInfo { uint32 itemGuid = 0; uint32 itemId = 0; };
struct Player;
int g_destroyed = 0;
uint32 g_nextGuid = 100;
bool g_appendOk = true;

struct Item {
    uint32 guid = 0;
    uint32 entry = 0;
    uint32 count = 1;
    ObjectGuid owner;
    int state = 0;
    uint32 GetGUIDLow() const { return guid; }
    uint32 GetEntry() const { return entry; }
    uint32 GetCount() const { return count; }
    ObjectGuid const& GetOwnerGuid() const { return owner; }
    void SetOwnerGuid(ObjectGuid value) { owner = value; }
    void SetState(int value) { state = value; }
    void FSetState(int value) { state = value; }
    bool IsInWorld() const { return false; }
    void RemoveFromWorld() {}
    bool AppendSaveToDB(CharacterPersistenceContext&) { return g_appendOk; }
    ~Item() { ++g_destroyed; }
    static Item* CreateItem(uint32 itemid, uint32 count, ObjectGuid owner) {
        Item* item = new Item();
        item->guid = ++g_nextGuid;
        item->entry = itemid;
        item->count = count;
        item->owner = owner;
        return item;
    }
};

struct LootItem { uint32 itemid = 100; uint32 count = 1; };
struct Loot {
    explicit Loot(void*) {}
    void FillLoot(uint32, int, Player*, bool, bool) {}
    uint32 GetMaxSlotInLootFor(uint32) const { return 1; }
    LootItem* LootItemInSlot(uint32, uint32) {
        static LootItem item;
        return &item;
    }
};

struct WorldSession;
struct MasterPlayer {
    uint32 guid = 500;
    std::vector<Item*> items;
    std::map<uint32, Item*> byItemGuid;
    uint32 GetGUIDLow() const { return guid; }
    ObjectGuid GetObjectGuid() const { return ObjectGuid(HIGHGUID_PLAYER, guid); }
    void AddMItem(Item* item) {
        items.push_back(item);
        byItemGuid[item->GetGUIDLow()] = item;
    }
    Item* GetMItem(uint32 guid) {
        auto itr = byItemGuid.find(guid);
        return itr == byItemGuid.end() ? nullptr : itr->second;
    }
};

struct WorldSession {
    MasterPlayer* master = nullptr;
    MasterPlayer* GetMasterPlayer() const { return master; }
    uint32 GetAccountId() const { return 42; }
};

struct Player {
    ObjectGuid guid;
    WorldSession* session = nullptr;
    Player() : guid(HIGHGUID_PLAYER, 500), session(new WorldSession()) {}
    ~Player() { delete session; }
    uint32 GetGUIDLow() const { return guid.GetCounter(); }
    uint64 GetIdentityGeneration() const { return 9; }
    WorldSession* GetSession() const { return session; }
    ObjectGuid const& GetObjectGuid() const { return guid; }
};

struct Mail {
    uint32 messageID = 0;
    uint16 mailTemplateId = 0;
    bool has_items = false;
    std::vector<MailItemInfo> items;
    void AddItem(uint32 guid, uint32 id) { items.push_back(MailItemInfo{guid, id}); }
    void prepareTemplateItems(Player* receiver,
                              AccountLifecycleLease const* lease = nullptr);
};

int g_links = 0;
bool AppendMailItemLink(CharacterPersistenceContext&, uint32, uint32, uint32, uint32) {
    ++g_links;
    return true;
}

struct LogStub { template<typename... A> void Out(A&&...) {} } sLog;
'''

MAIN = r'''
int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";
    Player receiver;
    MasterPlayer master;
    receiver.session->master = &master;
    Mail mail;
    mail.mailTemplateId = 5;

    if (scenario == "committed") {
        mail.prepareTemplateItems(&receiver);
        if (g_destroyed != 0 || master.items.size() != 1 || mail.items.size() != 1)
            return 1;
        delete master.items.front();
        return 0;
    }
    if (scenario == "rejected") {
        g_status = MailPersistenceStatus::Rejected;
        mail.prepareTemplateItems(&receiver);
        if (g_destroyed != 1 || !master.items.empty() || !g_recovery.empty())
            return 1;
        return 0;
    }
    if (scenario == "unknown") {
        g_status = MailPersistenceStatus::OutcomeUnknown;
        mail.prepareTemplateItems(&receiver);
        if (g_destroyed != 0 || master.items.size() != 1)
            return 1;
        if (g_recovery.size() != 1 || !g_blockHeld || g_admissionHeld)
            return 2;
        delete master.items.front();
        return 0;
    }
    if (scenario == "assembly_fail") {
        g_appendOk = false;
        mail.prepareTemplateItems(&receiver);
        if (g_destroyed != 1 || !master.items.empty() || !g_recovery.empty())
            return 1;
        return 0;
    }
    if (scenario == "admission_fail") {
        g_admit = false;
        mail.prepareTemplateItems(&receiver);
        if (g_destroyed != 1 || !master.items.empty() || !g_recovery.empty())
            return 1;
        return 0;
    }
    std::cerr << "unknown\n";
    return 2;
}
'''


class TemplateGenerationLifetimeTests(unittest.TestCase):
    def test_generated_items_have_one_owner(self):
        with tempfile.TemporaryDirectory(prefix="fn05h-template-life-") as td:
            work = pathlib.Path(td)
            (work / "probe.cpp").write_text(
                PREAMBLE + "\n" + extract() + "\n" + MAIN, encoding="utf-8")
            (work / "CMakeLists.txt").write_text(textwrap.dedent("""
                cmake_minimum_required(VERSION 3.20)
                project(template_life_probe CXX)
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
            for scenario in ("committed", "rejected", "unknown", "assembly_fail", "admission_fail"):
                run = subprocess.run([str(exe), scenario], stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True)
                self.assertEqual(run.returncode, 0, scenario + "\n" + run.stdout)


if __name__ == "__main__":
    unittest.main()
