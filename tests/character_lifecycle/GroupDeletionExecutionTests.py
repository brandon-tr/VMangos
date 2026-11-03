#!/usr/bin/env python3
"""Executable regression for the group deletion relationship plan.

The real `Group::AppendDeletionMemberRemoval` body is extracted from Group.cpp and
compiled against recording stubs for the group/instance/player surfaces.  The
scenarios cover leader succession instance pruning/copying, plain member removal,
disband with the legacy instance hand-off to a remaining member, disband reset
deletes, and BG groups that stage no group_member SQL.

Database execution and local Apply are stubbed by design; this is the planning
and SQL-staging half of the contract exercised as production code.
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
#include <functional>
#include <iostream>
#include <map>
#include <string>
#include <vector>

using uint8 = unsigned char;
using uint32 = unsigned int;
using uint64 = unsigned long long;

enum { HIGHGUID_PLAYER = 0 };
struct ObjectGuid {
    uint32 value = 0;
    ObjectGuid() {}
    ObjectGuid(int, uint32 v) : value(v) {}
    uint32 GetCounter() const { return value; }
    bool IsEmpty() const { return value == 0; }
    void Clear() { value = 0; }
    bool operator==(ObjectGuid const& other) const { return value == other.value; }
    bool operator!=(ObjectGuid const& other) const { return value != other.value; }
};

enum class SqlAffectedRowsPolicy { Any, ExactlyOne };
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
struct CharacterPersistenceContext {
    CharacterPersistenceContext(Database& database, uint32) { m_batch.db = &database; }
    SqlCheckedBatch& Batch() { return m_batch; }
    bool IsInvalid() const { return m_invalid; }
    void Invalidate() { m_invalid = true; }
    SqlCheckedBatch m_batch;
    bool m_invalid = false;
};

class Group;
struct DungeonPersistentState {
    uint32 instanceId = 1;
    uint32 mapId = 1;
    bool canReset = true;
    uint32 GetInstanceId() const { return instanceId; }
    uint32 GetMapId() const { return mapId; }
    bool CanReset() const { return canReset; }
    void AddGroup(Group*) {}
    void RemoveGroup(Group*) {}
};
struct InstanceGroupBind {
    DungeonPersistentState* state = nullptr;
    bool perm = false;
};
struct InstancePlayerBind {
    DungeonPersistentState* state = nullptr;
    bool perm = false;
};

struct MapEntry { uint32 mapType = 1; };
struct MapStorageStub {
    template<class T> T* LookupEntry(uint32) {
        static T entry;
        return &entry;
    }
} sMapStorage;

struct MemberSlot {
    ObjectGuid guid;
    std::string name;
    uint8 assistant = 0;
    uint8 group = 0;
};

struct Player {
    uint32 guid = 0;
    uint32 mapId = 1;
    uint32 GetGUIDLow() const { return guid; }
    Group* group = nullptr;
    std::map<uint32, InstancePlayerBind> binds;
    uint32 GetMapId() const { return mapId; }
    Group* GetGroup() const { return group; }
    InstancePlayerBind* GetBoundInstance(uint32 mapId) {
        auto itr = binds.find(mapId);
        return itr == binds.end() ? nullptr : &itr->second;
    }
    std::map<uint32, InstancePlayerBind>& GetBoundInstances() { return binds; }
};

struct ObjectMgrStub {
    std::map<uint32, Player*> players;
    Player* GetPlayer(ObjectGuid guid) {
        auto itr = players.find(guid.GetCounter());
        return itr == players.end() ? nullptr : itr->second;
    }
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
    Group* group = nullptr;
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
    bool AppendDeletionMemberRemoval(CharacterPersistenceContext& context,
                                     ObjectGuid guid,
                                     GroupDeletionMemberPlan& plan);
    using member_citerator = std::vector<MemberSlot>::const_iterator;
    member_citerator _getMemberCSlot(ObjectGuid guid) const {
        for (auto itr = m_memberSlots.begin(); itr != m_memberSlots.end(); ++itr)
            if (itr->guid == guid)
                return itr;
        return m_memberSlots.end();
    }
    uint32 GetMembersCount() const { return uint32(m_memberSlots.size()); }
    uint32 GetMembersMinCount() const { return isBGGroup() ? 1 : 2; }
    bool isRaidGroup() const { return raid; }
    bool isBGGroup() const { return bg; }
    bool IsInLFG() const { return m_LFGAreaId > 0; }

    uint32 m_Id = 7;
    ObjectGuid m_leaderGuid = ObjectGuid(HIGHGUID_PLAYER, 500);
    std::vector<MemberSlot> m_memberSlots;
    std::map<uint32, InstanceGroupBind> m_boundInstances;
    bool raid = false;
    bool bg = false;
    uint32 m_LFGAreaId = 0;
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
static BoundWrite const* findWrite(CharacterPersistenceContext& context, char const* needle) {
    for (auto const& write : context.m_batch.writes)
        if (write.sql.find(needle) != std::string::npos)
            return &write;
    return nullptr;
}
static void addMember(Group& group, uint32 guid, char const* name, bool assistant = false) {
    MemberSlot slot;
    slot.guid = ObjectGuid(HIGHGUID_PLAYER, guid);
    slot.name = name;
    slot.assistant = assistant ? 1 : 0;
    group.m_memberSlots.push_back(slot);
}

int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";

    if (scenario == "succession_with_binds") {
        Database database;
        CharacterPersistenceContext context(database, 500);
        Group group;
        addMember(group, 500, "Old Leader");
        addMember(group, 501, "New Leader");
        addMember(group, 502, "Third");
        Player newLeader;
        newLeader.group = &group;
        sObjectMgr.players[501] = &newLeader;
        DungeonPersistentState groupState;
        groupState.instanceId = 41;
        InstancePlayerBind personal;
        personal.state = &groupState;
        personal.perm = false;
        newLeader.binds[10] = personal;
        InstanceGroupBind groupBind;
        groupBind.state = &groupState;
        groupBind.perm = false;
        group.m_boundInstances[10] = groupBind;

        GroupDeletionMemberPlan plan;
        check(group.AppendDeletionMemberRemoval(context, ObjectGuid(HIGHGUID_PLAYER, 500), plan),
              "assembly");
        check(plan.kind == GroupDeletionMemberPlan::Kind::Succession &&
              plan.newLeaderGuid == 501 && plan.newLeaderName == "New Leader", "successor plan");
        check(countWrites(context, "DELETE FROM `group_member` WHERE `member_guid` = ? AND `group_id` = ?") == 1,
              "member delete staged");
        BoundWrite const* leader = findWrite(context, "UPDATE `groups` SET `leader_guid` = ?");
        check(leader && leader->params == std::vector<std::string>({"501", "7", "500"}),
              "guarded group leader update");
        check(countWrites(context, "DELETE FROM `group_instance` WHERE `leader_guid` = ? AND (`permanent` = 1") == 1,
              "group bind prune staged");
        check(countWrites(context, "DELETE FROM `group_instance` WHERE `leader_guid` = ? AND `instance` = ?") == 1,
              "new leader personal bind removes group bind");
        check(countWrites(context, "UPDATE `group_instance` SET `leader_guid` = ? WHERE `leader_guid` = ?") == 1,
              "remaining group binds move to the new leader");
        check(countWrites(context, "INSERT INTO `group_instance`") == 1,
              "new leader binds copied to the group");
        check(group.m_memberSlots.size() == 3 && group.m_leaderGuid.GetCounter() == 500,
              "no local mutation during assembly");
    }
    else if (scenario == "plain_member") {
        Database database;
        CharacterPersistenceContext context(database, 500);
        Group group;
        addMember(group, 500, "Leader");
        addMember(group, 501, "Member");
        addMember(group, 502, "Third");
        GroupDeletionMemberPlan plan;
        check(group.AppendDeletionMemberRemoval(context, ObjectGuid(HIGHGUID_PLAYER, 502), plan),
              "assembly");
        check(plan.kind == GroupDeletionMemberPlan::Kind::Remove, "plain removal plan");
        check(countWrites(context, "DELETE FROM `group_member` WHERE `member_guid` = ?") == 1,
              "member delete staged");
        check(countWrites(context, "UPDATE `groups`") == 0, "no leader update");
    }
    else if (scenario == "disband_with_transfer") {
        Database database;
        CharacterPersistenceContext context(database, 500);
        Group group;
        addMember(group, 500, "Leader");
        addMember(group, 501, "Remaining");
        sObjectMgr.players[500] = new Player();
        Player remaining;
        remaining.guid = 501;
        remaining.mapId = 20;
        remaining.group = &group;
        sObjectMgr.players[501] = &remaining;
        DungeonPersistentState transferState;
        transferState.instanceId = 77;
        InstanceGroupBind transferBind;
        transferBind.state = &transferState;
        transferBind.perm = false;
        group.m_boundInstances[20] = transferBind;
        DungeonPersistentState resetState;
        resetState.instanceId = 88;
        InstanceGroupBind resetBind;
        resetBind.state = &resetState;
        resetBind.perm = true;
        group.m_boundInstances[21] = resetBind;

        GroupDeletionMemberPlan plan;
        check(group.AppendDeletionMemberRemoval(context, ObjectGuid(HIGHGUID_PLAYER, 500), plan),
              "assembly");
        check(plan.kind == GroupDeletionMemberPlan::Kind::Disband && plan.hasTransferBind &&
              plan.transferPlayerGuid == 501 && plan.transferBind.instanceId == 77,
              "disband hand-off plan");
        check(countWrites(context, "DELETE FROM `groups` WHERE `group_id` = ?") == 1 &&
              countWrites(context, "DELETE FROM `group_member` WHERE `group_id` = ?") == 1,
              "disband rows staged");
        BoundWrite const* transfer = findWrite(context, "INSERT INTO `character_instance`");
        check(transfer && transfer->params == std::vector<std::string>({"501", "77", "0"}),
              "instance handed to the remaining member");
        check(countWrites(context, "DELETE FROM `group_instance` WHERE `leader_guid` = ? AND `instance` = ?") == 1,
              "transferred group bind removed");
        check(countWrites(context, "DELETE FROM `instance` WHERE `id` = ?") == 1,
              "resettable instance rows staged for the non-transferred bind");
        bool transferExcluded = plan.resetGroupBinds.size() == 1 &&
                                plan.resetGroupBinds[0].mapId == 21;
        check(transferExcluded, "transferred bind excluded from the reset list");
    }
    else if (scenario == "disband_no_remaining") {
        Database database;
        CharacterPersistenceContext context(database, 500);
        Group group;
        addMember(group, 500, "Last One");
        GroupDeletionMemberPlan plan;
        check(group.AppendDeletionMemberRemoval(context, ObjectGuid(HIGHGUID_PLAYER, 500), plan),
              "assembly");
        check(plan.kind == GroupDeletionMemberPlan::Kind::Disband && !plan.hasTransferBind,
              "no hand-off without a remaining online member");
        check(countWrites(context, "DELETE FROM `groups` WHERE `group_id` = ?") == 1,
              "guild row staged");
        check(countWrites(context, "INSERT INTO `character_instance`") == 0,
              "no instance transfer");
    }
    else if (scenario == "bg_group_no_member_sql") {
        Database database;
        CharacterPersistenceContext context(database, 500);
        Group group;
        group.bg = true;
        addMember(group, 500, "Leader");
        addMember(group, 501, "Member");
        addMember(group, 502, "Third");
        GroupDeletionMemberPlan plan;
        check(group.AppendDeletionMemberRemoval(context, ObjectGuid(HIGHGUID_PLAYER, 502), plan),
              "assembly");
        check(plan.kind == GroupDeletionMemberPlan::Kind::Remove, "plain removal plan");
        check(context.m_batch.writes.empty(), "BG groups stage no persistent membership SQL");
    }
    else if (scenario == "missing_member") {
        Database database;
        CharacterPersistenceContext context(database, 500);
        Group group;
        GroupDeletionMemberPlan plan;
        check(!group.AppendDeletionMemberRemoval(context, ObjectGuid(HIGHGUID_PLAYER, 999), plan),
              "unknown member rejected");
        check(context.IsInvalid() && context.m_batch.writes.empty(), "invalidated without SQL");
    }
    else { std::cerr << "unknown\n"; return 2; }

    if (failures) { std::cerr << "failures=" << failures << "\n"; return 1; }
    std::cout << "group-deletion " << scenario << ": PASS\n";
    return 0;
}
'''


def extract():
    source = SOURCE.read_text(encoding="utf-8")
    return source[source.index("bool Group::AppendDeletionMemberRemoval("):
                  source.index("void Group::ApplyDeletionMemberRemoval(")]


@unittest.skipUnless(shutil.which("cmake"), "cmake not available")
class GroupDeletionExecutionTests(unittest.TestCase):
    def test_group_deletion_plan(self):
        append = extract()
        program = PREAMBLE + "\n" + append + "\n" + MAIN
        with tempfile.TemporaryDirectory(prefix="fn05h-group-del-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(group_del_exec CXX)\n"
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
            for scenario in ["succession_with_binds", "plain_member",
                             "disband_with_transfer", "disband_no_remaining",
                             "bg_group_no_member_sql", "missing_member"]:
                r = subprocess.run([str(exe), scenario], text=True,
                                   capture_output=True, timeout=30)
                if r.returncode != 0 or ("group-deletion " + scenario + ": PASS") not in r.stdout:
                    failures.append(scenario + "\n" + r.stdout + r.stderr)
            self.assertFalse(failures, "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
