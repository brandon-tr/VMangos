"""Runtime lifetime contract for Item save acknowledgment + sender removal.

The real Item queue/state methods and Player::MoveItemFromInventory are
extracted; the probe models only the surrounding inventory and receiver.
"""

import pathlib
import subprocess
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ITEM_CPP = (ROOT / "src/game/Objects/Item.cpp").read_text(encoding="utf-8")
PLAYER_CPP = (ROOT / "src/game/Objects/Player.cpp").read_text(encoding="utf-8")


def item_function(signature, end_signature):
    start = ITEM_CPP.index(signature)
    end = ITEM_CPP.index(end_signature, start)
    return ITEM_CPP[start:end]


def player_function(signature, end_signature):
    start = PLAYER_CPP.index(signature)
    end = PLAYER_CPP.index(end_signature, start)
    return PLAYER_CPP[start:end]


PREAMBLE = r'''
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <map>
#include <string>
#include <vector>

using uint8 = unsigned char;
using uint16 = unsigned short;
using uint32 = unsigned int;
using uint64 = unsigned long long;
using int16 = short;

enum { HIGHGUID_PLAYER = 0 };
enum { INVENTORY_SLOT_BAG_0 = 0 };
enum { LOG_BASIC = 0, LOG_LVL_ERROR = 1 };
enum ItemUpdateState { ITEM_UNCHANGED = 0, ITEM_CHANGED = 1, ITEM_NEW = 2, ITEM_REMOVED = 3 };

struct ObjectGuid {
    uint32 value = 0;
    ObjectGuid() = default;
    ObjectGuid(int, uint32 v) : value(v) {}
    uint32 GetCounter() const { return value; }
    std::string GetString() const { return std::to_string(value); }
    bool operator==(ObjectGuid const& other) const { return value == other.value; }
    bool operator!=(ObjectGuid const& other) const { return value != other.value; }
};

struct Item;
class Player;

struct ObjectMgrStub {
    Player* owner = nullptr;
    Player* GetPlayer(ObjectGuid) { return owner; }
} sObjectMgr;

struct LogStub { template<typename... A> void Out(A&&...) {} } sLog;

struct Item {
    uint32 guid = 0;
    uint32 entry = 0;
    uint32 count = 1;
    ObjectGuid ownerGuid;
    Player* owner = nullptr;
    int16 uQueuePos = -1;
    ItemUpdateState uState = ITEM_UNCHANGED;
    bool inWorld = false;
    bool destroyed = false;

    uint32 GetGUIDLow() const { return guid; }
    uint32 GetEntry() const { return entry; }
    uint32 GetCount() const { return count; }
    ObjectGuid const& GetOwnerGuid() const { return ownerGuid; }
    void SetOwnerGuid(ObjectGuid value) { ownerGuid = value; }
    Player* GetOwner() const { return owner; }
    bool IsInUpdateQueue() const { return uQueuePos != -1; }
    int16 GetQueuePos() const { return uQueuePos; }
    void FSetState(ItemUpdateState state) { uState = state; }
    uint8 GetBagSlot() const { return 0; }
    uint8 GetSlot() const { return 0; }
    bool IsInWorld() const { return inWorld; }
    void RemoveFromWorld() { inWorld = false; }
    void DestroyForPlayer(Player*) {}
    std::string GetGuidStr() const { return std::to_string(guid); }
    void SetState(ItemUpdateState state, Player* player = nullptr);
    void AddToUpdateQueueOf(Player* player);
    void RemoveFromUpdateQueueOf(Player* player);
    bool AcknowledgeSave(Player* owner = nullptr);
};

class Player {
public:
    ObjectGuid GetObjectGuid() const { return ObjectGuid(HIGHGUID_PLAYER, 500); }
    std::string GetGuidStr() const { return "500"; }
    std::vector<Item*>& GetItemUpdateQueue() { return m_itemUpdateQueue; }
    bool IsItemUpdateQueueBlocked() const { return m_itemUpdateQueueBlocked; }
    Item* GetItemByPos(uint8 bag, uint8 slot) {
        auto itr = items.find(uint16((bag << 8) | slot));
        return itr == items.end() ? nullptr : itr->second;
    }
    void RemoveItem(uint8 bag, uint8 slot, bool) {
        items.erase(uint16((bag << 8) | slot));
    }
    void ItemRemovedQuestCheck(uint32, uint32) {}
    void MoveItemFromInventory(uint8 bag, uint8 slot, bool update);

    std::map<uint16, Item*> items;
    std::vector<Item*> m_itemUpdateQueue;
    bool m_itemUpdateQueueBlocked = false;
};

int g_destroyed = 0;

int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";
    Player player;
    Item item;
    item.guid = 11; item.entry = 111; item.count = 1;
    item.ownerGuid = player.GetObjectGuid();
    item.owner = &player;
    sObjectMgr.owner = &player;

    if (scenario == "online") {
        player.items[0] = &item;
        item.SetState(ITEM_CHANGED, &player);             // ITEM_CHANGED queues
        Item unrelated;
        unrelated.guid = 22; unrelated.ownerGuid = player.GetObjectGuid();
        unrelated.owner = &player;
        unrelated.SetState(ITEM_CHANGED, &player);
        player.m_itemUpdateQueue.push_back(&item);  // legacy duplicate entry
        item.AcknowledgeSave();                // commit ack
        player.MoveItemFromInventory(0, 0, true); // publishSender
        bool stale = false;
        for (Item* queued : player.m_itemUpdateQueue)
            if (queued == &item) stale = true;
        if (stale) { std::cerr << "stale item pointer\n"; return 1; }
        bool unrelatedKept = false;
        for (Item* queued : player.m_itemUpdateQueue)
            if (queued == &unrelated) unrelatedKept = true;
        if (!unrelatedKept) { std::cerr << "unrelated lost\n"; return 1; }
        if (player.items.count(0) != 0) { std::cerr << "inventory not removed\n"; return 1; }
        // Safe re-add after ack: no stale slot causes duplicate ownership.
        player.items[0] = &item;
        item.SetState(ITEM_CHANGED, &player);
        int count = 0;
        for (Item* queued : player.m_itemUpdateQueue)
            if (queued == &item) ++count;
        if (count != 1) { std::cerr << "re-add duplicate\n"; return 1; }
        return 0;
    }
    if (scenario == "new_online") {
        player.items[0] = &item;
        item.SetState(ITEM_NEW, &player);
        item.AcknowledgeSave();
        player.MoveItemFromInventory(0, 0, true);
        for (Item* queued : player.m_itemUpdateQueue)
            if (queued == &item) { std::cerr << "stale new item\n"; return 1; }
        return 0;
    }
    if (scenario == "non_world_owner") {
        player.items[0] = &item;
        item.owner = nullptr;                  // FindPlayer/GetOwner cannot resolve
        sObjectMgr.owner = nullptr;
        item.SetState(ITEM_CHANGED, &player);
        if (item.AcknowledgeSave()) { std::cerr << "owner lookup unexpectedly succeeded\n"; return 1; }
        if (!item.IsInUpdateQueue()) { std::cerr << "failed ack left queue\n"; return 2; }
        if (!item.AcknowledgeSave(&player)) { std::cerr << "explicit owner failed\n"; return 3; }
        for (Item* queued : player.m_itemUpdateQueue)
            if (queued == &item) { std::cerr << "explicit ack left queue\n"; return 4; }
        return 0;
    }
    if (scenario == "offline") {
        Item* destroyed = new Item();
        destroyed->guid = 12; destroyed->entry = 112; destroyed->count = 1;
        destroyed->ownerGuid = player.GetObjectGuid();
        destroyed->owner = &player;
        player.items[0] = destroyed;
        destroyed->SetState(ITEM_CHANGED, &player);
        destroyed->AcknowledgeSave();
        player.MoveItemFromInventory(0, 0, true);
        for (Item* queued : player.m_itemUpdateQueue)
            if (queued == destroyed) { std::cerr << "stale destroyed item\n"; return 1; }
        delete destroyed;                      // offline publication destroys it
        return 0;
    }
    std::cerr << "unknown\n";
    return 2;
}
'''

MAIN_CHECK = r'''
'''


class ItemAcknowledgmentLifetimeTests(unittest.TestCase):
    def test_queue_is_detached_before_ownership_transfer(self):
        body = (
            item_function("bool Item::AcknowledgeSave(Player* owner)",
                          "void Item::SetState(") +
            item_function("void Item::SetState(",
                          "void Item::AddToUpdateQueueOf(") +
            item_function("void Item::AddToUpdateQueueOf(",
                          "void Item::RemoveFromUpdateQueueOf(") +
            item_function("void Item::RemoveFromUpdateQueueOf(",
                          "uint8 Item::GetBagSlot()") +
            player_function("void Player::MoveItemFromInventory(",
                            "void Player::MoveItemToInventory(")
        )
        with tempfile.TemporaryDirectory(prefix="fn05h-item-ack-") as td:
            work = pathlib.Path(td)
            (work / "probe.cpp").write_text(
                PREAMBLE + "\n" + body + "\n" + MAIN_CHECK, encoding="utf-8")
            (work / "CMakeLists.txt").write_text(textwrap.dedent("""
                cmake_minimum_required(VERSION 3.20)
                project(item_ack_probe CXX)
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
            for scenario in ("online", "new_online", "non_world_owner", "offline"):
                run = subprocess.run([str(exe), scenario], stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True)
                self.assertEqual(run.returncode, 0, scenario + "\n" + run.stdout)


if __name__ == "__main__":
    unittest.main()
