#!/usr/bin/env python3
"""Executable regression for the group deletion local-apply half.

The real `Group::ApplyDeletionMemberRemoval` body is extracted from Group.cpp and
compiled against recording stubs for players, maps, packets, LFG and instance
state.  The scenarios verify that Apply performs only local work after the
checked batch: member/leader publication for succession, plain removal with LFG
update, and disband with the instance hand-off, reset and group destruction.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/game/Group/Group.cpp"

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
using uint32 = unsigned int;
using uint64 = unsigned long long;

enum { HIGHGUID_PLAYER = 0 };
enum { INSTANCE_RESET_GROUP_DISBAND = 1 };
struct ObjectGuid {
    uint32 value = 0;
    ObjectGuid() {}
    ObjectGuid(int, uint32 v) : value(v) {}
    uint32 GetCounter() const { return value; }
    bool operator==(ObjectGuid const& other) const { return value == other.value; }
    bool operator!=(ObjectGuid const& other) const { return value != other.value; }
    bool IsEmpty() const { return value == 0; }
    void Clear() { value = 0; }
};

class Group;
struct DungeonPersistentState {
    uint32 instanceId = 1;
    uint32 mapId = 1;
    bool canReset = true;
    int removedFromGroups = 0;
    int addedToGroups = 0;
    uint32 GetInstanceId() const { return instanceId; }
    uint32 GetMapId() const { return mapId; }
    bool CanReset() const { return canReset; }
    void AddGroup(Group*) { ++addedToGroups; }
    void RemoveGroup(Group*) { ++removedFromGroups; }
};
struct InstanceGroupBind {
    DungeonPersistentState* state = nullptr;
    bool perm = false;
};
struct InstancePlayerBind {
    DungeonPersistentState* state = nullptr;
    bool perm = false;
};

struct Map {
    bool dungeon = true;
    virtual ~Map() = default;
    bool IsDungeon() const { return dungeon; }
};
struct DungeonMap : Map {
    int resets = 0;
    void Reset(int) { ++resets; }
};
struct MapMgrStub {
    Map* map = nullptr;
    Map* FindMap(uint32, uint32) { return map; }
} sMapMgr;

struct LFGQueue {
    std::vector<uint32> removedGroups;
    void RemoveGroupFromQueue(uint32 groupId) { removedGroups.push_back(groupId); }
};
struct MessagerStub {
    LFGQueue* queue = nullptr;
    MessagerStub& GetMessager() { return *this; }
    template<class F> void AddMessage(F&& action) {
        if (queue) {
            LFGQueue* q = queue;
            action(q);
        }
    }
};
struct WorldStub {
    LFGQueue queue;
    MessagerStub messager;
    MessagerStub& GetLFGQueue() { messager.queue = &queue; return messager; }
} sWorld;

struct LFGMgrStub {
    int updates = 0;
    void UpdateGroup(Group*, bool, ObjectGuid) { ++updates; }
} sLFGMgr;

namespace WorldPackets { namespace Group {
struct GroupList {};
struct GroupSetLeaderNotification { std::string leaderName; };
}}

struct WorldSession {
    int packets = 0;
    template<class T> void SendPacket(std::unique_ptr<T>) { ++packets; }
};

struct EventsStub {
    template<class F> void AddLambdaEventAtOffset(F&&, int) {}
};

struct MemberSlot {
    ObjectGuid guid;
    std::string name;
    uint8 assistant = 0;
    uint8 group = 0;
};

struct Player {
    uint32 guid = 0;
    uint32 mapId = 1;
    Group* group = nullptr;
    Group* originalGroup = nullptr;
    int battleGroundRemovals = 0;
    int questUpdates = 0;
    int leaderFlagUpdates = 0;
    int bindCalls = 0;
    std::map<uint32, InstancePlayerBind> binds;
    EventsStub m_Events;
    WorldSession* session = new WorldSession();
    uint32 GetGUIDLow() const { return guid; }
    uint32 GetMapId() const { return mapId; }
    Group* GetGroup() const { return group; }
    Group* GetOriginalGroup() const { return originalGroup; }
    void SetGroup(Group* g) { group = g; }
    void SetOriginalGroup(Group* g) { originalGroup = g; }
    void RemoveFromBattleGroundRaid() { ++battleGroundRemovals; }
    void UpdateForQuestWorldObjects() { ++questUpdates; }
    void UpdateGroupLeaderFlag(bool) { ++leaderFlagUpdates; }
    WorldSession* GetSession() const { return session; }
    InstancePlayerBind* GetBoundInstance(uint32 mapId) {
        auto itr = binds.find(mapId);
        return itr == binds.end() ? nullptr : &itr->second;
    }
    std::map<uint32, InstancePlayerBind>& GetBoundInstances() { return binds; }
    void BindToInstance(DungeonPersistentState*, bool, bool) { ++bindCalls; }
    void UnbindInstance(std::map<uint32, InstancePlayerBind>::iterator& itr, bool) {
        itr = binds.erase(itr);
    }
};

struct ObjectMgrStub {
    std::map<uint32, Player*> players;
    std::vector<Group*> removedGroups;
    Player* GetPlayer(ObjectGuid guid) {
        auto itr = players.find(guid.GetCounter());
        return itr == players.end() ? nullptr : itr->second;
    }
    void RemoveGroup(Group* group) { removedGroups.push_back(group); }
} sObjectMgr;

struct GroupDeletionMemberPlan {
    enum class Kind { None, Remove, Succession, Disband };
    struct BindOp {
        uint32 mapId = 0;
        uint32 instanceId = 0;
        bool permanent = false;
        DungeonPersistentState* state = nullptr;
    };
    Kind kind = Kind::None;
    uint32 groupId = 0;
    uint32 memberGuid = 0;
    uint32 newLeaderGuid = 0;
    std::string newLeaderName;
    bool isRaid = false;
    bool isBg = false;
    bool inLfg = false;
    uint32 lfgAreaId = 0;
    std::vector<BindOp> removeGroupBinds;
    std::vector<BindOp> addGroupBinds;
    std::vector<BindOp> resetGroupBinds;
    BindOp transferBind;
    bool hasTransferBind = false;
    uint32 transferPlayerGuid = 0;
};

struct LogStub { template<typename... A> void Out(A&&...) {} } sLog;

class Group {
public:
    void ApplyDeletionMemberRemoval(GroupDeletionMemberPlan const& plan);
    using member_citerator = std::vector<MemberSlot>::const_iterator;
    using member_witerator = std::vector<MemberSlot>::iterator;
    member_witerator _getMemberWSlot(ObjectGuid guid) {
        for (auto itr = m_memberSlots.begin(); itr != m_memberSlots.end(); ++itr)
            if (itr->guid == guid)
                return itr;
        return m_memberSlots.end();
    }
    void SubGroupCounterDecrease(uint8) {}
    void _removeRolls(ObjectGuid) {}
    struct RollStub { int dummy; };
    using Rolls = std::vector<RollStub*>;
    Rolls RollId;
    void CountTheRoll(Rolls::iterator) {}
    void RemoveAllInvites() { ++invitesRemoved; }
    void _updateLeaderFlag(bool = false) { sObjectMgr.GetPlayer(m_leaderGuid); }
    void SendUpdate() { ++updatesSent; }
    template<class T> void BroadcastPacket(std::unique_ptr<T>, bool) { ++packetsBroadcast; }
    uint32 GetId() const { return m_Id; }
    uint32 GetMembersCount() const { return uint32(m_memberSlots.size()); }
    bool isRaidGroup() const { return raid; }
    bool isBGGroup() const { return bg; }
    bool IsInLFG() const { return m_LFGAreaId > 0; }

    uint32 m_Id = 7;
    ObjectGuid m_leaderGuid = ObjectGuid(HIGHGUID_PLAYER, 500);
    std::string m_leaderName = "Old Leader";
    time_t m_leaderLastOnline = 0;
    std::vector<MemberSlot> m_memberSlots;
    std::map<uint32, InstanceGroupBind> m_boundInstances;
    bool raid = false;
    bool bg = false;
    uint32 m_LFGAreaId = 0;
    int invitesRemoved = 0;
    int updatesSent = 0;
    int packetsBroadcast = 0;
};
'''

MAIN = r'''
static int failures = 0;
static void check(bool value, std::string const& label) {
    if (!value) { std::cerr << "FAIL " << label << "\n"; ++failures; }
}
static void addMember(Group& group, uint32 guid, char const* name) {
    MemberSlot slot;
    slot.guid = ObjectGuid(HIGHGUID_PLAYER, guid);
    slot.name = name;
    group.m_memberSlots.push_back(slot);
}

int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";

    if (scenario == "apply_succession") {
        Group group;
        addMember(group, 500, "Old Leader");
        addMember(group, 501, "New Leader");
        Player newLeader;
        newLeader.guid = 501;
        newLeader.group = &group;
        InstancePlayerBind solo;
        solo.state = nullptr;
        solo.perm = false;
        newLeader.binds[10] = solo;
        Player oldLeader;
        oldLeader.guid = 500;
        sObjectMgr.players[500] = &oldLeader;
        sObjectMgr.players[501] = &newLeader;

        DungeonPersistentState removedState;
        DungeonPersistentState addedState;
        GroupDeletionMemberPlan plan;
        plan.kind = GroupDeletionMemberPlan::Kind::Succession;
        plan.memberGuid = 500;
        plan.newLeaderGuid = 501;
        plan.newLeaderName = "New Leader";
        plan.removeGroupBinds.push_back({11, 1, false, &removedState});
        plan.addGroupBinds.push_back({12, 2, true, &addedState});
        group.m_boundInstances[11].state = &removedState;
        group.m_boundInstances[12].state = nullptr;

        group.ApplyDeletionMemberRemoval(plan);
        check(group.m_memberSlots.size() == 1 && group.m_memberSlots[0].guid.GetCounter() == 501,
              "removed member erased locally");
        check(group.m_leaderGuid.GetCounter() == 501 && group.m_leaderName == "New Leader",
              "successor installed");
        check(group.m_boundInstances.find(11) == group.m_boundInstances.end() &&
              removedState.removedFromGroups == 1, "old bind removed from group");
        check(group.m_boundInstances.find(12) != group.m_boundInstances.end() &&
              group.m_boundInstances[12].state == &addedState &&
              addedState.addedToGroups == 1, "new leader bind copied");
        check(newLeader.binds.empty(), "new leader solo binds unbound");
        check(group.packetsBroadcast == 1, "leader notification broadcast");
        check(group.updatesSent == 1, "group update sent");
        check(!sWorld.queue.removedGroups.empty(), "LFG queue removal scheduled");
        check(sLFGMgr.updates == 0, "no UpdateGroup on leader change");
    }
    else if (scenario == "apply_plain_remove") {
        Group group;
        addMember(group, 500, "Leader");
        addMember(group, 501, "Member");
        group.m_LFGAreaId = 55;
        Player leader;
        leader.guid = 500;
        sObjectMgr.players[500] = &leader;
        GroupDeletionMemberPlan plan;
        plan.kind = GroupDeletionMemberPlan::Kind::Remove;
        plan.memberGuid = 501;
        group.ApplyDeletionMemberRemoval(plan);
        check(group.m_memberSlots.size() == 1, "member erased");
        check(group.m_leaderGuid.GetCounter() == 500, "leader unchanged");
        check(sLFGMgr.updates == 1, "LFG updated for a plain removal");
        check(group.updatesSent == 1, "group update sent");
        check(group.packetsBroadcast == 0, "no leader packet");
    }
    else if (scenario == "apply_disband_transfer") {
        Group group;
        addMember(group, 500, "Leader");
        addMember(group, 501, "Remaining");
        Player remaining;
        remaining.guid = 501;
        remaining.mapId = 20;
        remaining.group = &group;
        sObjectMgr.players[501] = &remaining;
        DungeonMap map;
        Map* dungeon = &map;
        sMapMgr.map = dungeon;

        DungeonPersistentState transferState;
        transferState.instanceId = 77;
        DungeonPersistentState resetState;
        resetState.instanceId = 88;
        GroupDeletionMemberPlan plan;
        plan.kind = GroupDeletionMemberPlan::Kind::Disband;
        plan.memberGuid = 500;
        plan.hasTransferBind = true;
        plan.transferPlayerGuid = 501;
        plan.transferBind = {20, 77, false, &transferState};
        plan.resetGroupBinds.push_back({21, 88, true, &resetState});
        group.m_boundInstances[20].state = &transferState;
        group.m_boundInstances[21].state = &resetState;

        group.ApplyDeletionMemberRemoval(plan);
        check(group.m_memberSlots.empty(), "members cleared");
        check(remaining.bindCalls == 1, "instance handed to the remaining member");
        check(transferState.removedFromGroups == 1 && resetState.removedFromGroups == 1,
              "group membership removed from both instance states");
        check(group.m_boundInstances.empty(), "group binds cleared");
        check(map.resets == 1, "loaded resettable instance reset locally");
        check(group.m_leaderGuid.IsEmpty() && group.m_leaderName.empty(), "leader cleared");
        check(group.invitesRemoved == 1, "invites cleared");
        check(group.updatesSent == 0,
              "disband publishes per-player updates instead of SendUpdate");
    }
    else if (scenario == "apply_disband_no_transfer") {
        Group group;
        addMember(group, 500, "Only");
        GroupDeletionMemberPlan plan;
        plan.kind = GroupDeletionMemberPlan::Kind::Disband;
        plan.memberGuid = 500;
        group.ApplyDeletionMemberRemoval(plan);
        check(group.m_memberSlots.empty() && group.m_leaderGuid.IsEmpty(),
              "empty disband clears group state");
        check(sObjectMgr.removedGroups.empty(), "manager removal is the orchestrator's job");
    }
    else { std::cerr << "unknown\n"; return 2; }

    if (failures) { std::cerr << "failures=" << failures << "\n"; return 1; }
    std::cout << "group-apply " << scenario << ": PASS\n";
    return 0;
}
'''


def extract():
    source = SOURCE.read_text(encoding="utf-8")
    return source[source.index("void Group::ApplyDeletionMemberRemoval("):
                  source.index("void Group::_updateLeaderFlag(")]


@unittest.skipUnless(shutil.which("cmake"), "cmake not available")
class GroupDeletionApplyExecutionTests(unittest.TestCase):
    def test_group_apply(self):
        apply = extract()
        program = PREAMBLE + "\n" + apply + "\n" + MAIN
        with tempfile.TemporaryDirectory(prefix="fn05h-group-apply-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(group_apply_exec CXX)\n"
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
            for scenario in ["apply_succession", "apply_plain_remove",
                             "apply_disband_transfer", "apply_disband_no_transfer"]:
                r = subprocess.run([str(exe), scenario], text=True,
                                   capture_output=True, timeout=30)
                if r.returncode != 0 or ("group-apply " + scenario + ": PASS") not in r.stdout:
                    failures.append(scenario + "\n" + r.stdout + r.stderr)
            self.assertFalse(failures, "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
