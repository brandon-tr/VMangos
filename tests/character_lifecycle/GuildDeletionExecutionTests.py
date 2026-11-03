#!/usr/bin/env python3
"""Executable regression for guild deletion relationship plan/apply.

The real `Guild::AppendDeletionMemberRemoval` and `Guild::ApplyDeletionMemberRemoval`
bodies are extracted from Guild.cpp and compiled against recording stubs.  The
scenarios drive leader succession, plain member removal, and empty-guild disband:
relationship SQL must be staged before any local change, and the local changes
(succession, broadcast, removal) must happen only in Apply.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/game/Guild/Guild.cpp"

PREAMBLE = r'''
#include <algorithm>
#include <functional>
#include <iostream>
#include <map>
#include <string>
#include <unordered_map>
#include <vector>

using uint8 = unsigned char;
using uint32 = unsigned int;
using uint64 = unsigned long long;

enum { HIGHGUID_PLAYER = 0 };
enum GuildEvents { GE_DISBANDED, GE_LEADER_CHANGED, GE_LEFT };
enum { GR_GUILDMASTER = 0 };

struct ObjectGuid {
    uint32 value = 0;
    ObjectGuid() {}
    ObjectGuid(int, uint32 v) : value(v) {}
    uint32 GetCounter() const { return value; }
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
int g_sqlStatements = 0;
struct Database {
    SqlStatement CreateStatement(SqlStatementID&, char const* sql) {
        ++g_sqlStatements;
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

struct GuildDeletionMemberPlan {
    struct GuildDummy { int x; };
    void* guild = nullptr;
    uint32 guildId = 0;
    uint32 memberGuid = 0;
    bool leaderRemoved = false;
    uint32 newLeaderGuid = 0;
    std::string oldLeaderName;
    std::string newLeaderName;
    bool becomesEmpty = false;
};

struct MemberSlot {
    std::string Name;
    uint32 RankId = 0;
    void ChangeRank(uint32 rank) { RankId = rank; }
};

struct Player {
    void SetInGuild(uint32) {}
    void SetRank(uint32) {}
};

struct GuildMgrStub {
    std::vector<uint32> removedMembers;
    std::vector<uint32> removedGuilds;
    std::vector<std::string> events;
    void GuildMemberRemoved(ObjectGuid guid) { removedMembers.push_back(guid.GetCounter()); }
    void RemoveGuild(uint32 id) { removedGuilds.push_back(id); }
} sGuildMgr;

struct ObjectMgrStub {
    std::map<uint32, Player*> players;
    Player* GetPlayer(ObjectGuid guid) {
        auto itr = players.find(guid.GetCounter());
        return itr == players.end() ? nullptr : itr->second;
    }
} sObjectMgr;

struct LogStub { template<typename... A> void Out(A&&...) {} } sLog;

class Guild {
public:
    bool AppendDeletionMemberRemoval(CharacterPersistenceContext& context,
                                     ObjectGuid guid,
                                     GuildDeletionMemberPlan& plan);
    void ApplyDeletionMemberRemoval(GuildDeletionMemberPlan const& plan);

    MemberSlot* GetMemberSlot(ObjectGuid guid) {
        auto itr = members.find(guid.GetCounter());
        return itr == members.end() ? nullptr : &itr->second;
    }
    void UpdateAccountsNumber() { ++accountsUpdates; }
    void BroadcastEvent(GuildEvents event) { sGuildMgr.events.push_back("event" + std::to_string(event)); }
    void BroadcastEvent(GuildEvents event, char const*, char const*) {
        sGuildMgr.events.push_back("event2-" + std::to_string(event));
    }
    void BroadcastEvent(GuildEvents event, ObjectGuid, char const*) {
        sGuildMgr.events.push_back("event3-" + std::to_string(event));
    }

    uint32 m_Id = 7;
    ObjectGuid m_LeaderGuid = ObjectGuid(HIGHGUID_PLAYER, 500);
    std::unordered_map<uint32, MemberSlot> members;
    int accountsUpdates = 0;
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
static void addMember(Guild& guild, uint32 guid, uint32 rank, char const* name) {
    MemberSlot slot;
    slot.RankId = rank;
    slot.Name = name;
    guild.members[guid] = slot;
}

int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";

    if (scenario == "succession") {
        Database database;
        CharacterPersistenceContext context(database, 500);
        Guild guild;
        addMember(guild, 500, 0, "Old Leader");
        addMember(guild, 501, 1, "New Leader");
        addMember(guild, 502, 2, "Third");
        GuildDeletionMemberPlan plan;
        check(guild.AppendDeletionMemberRemoval(context, ObjectGuid(HIGHGUID_PLAYER, 500), plan),
              "assembly");
        check(plan.leaderRemoved && plan.newLeaderGuid == 501 && plan.oldLeaderName == "Old Leader" &&
              plan.newLeaderName == "New Leader" && !plan.becomesEmpty, "successor plan");
        BoundWrite const* leader = findWrite(context, "UPDATE `guild` SET `leader_guid`");
        check(leader && leader->params == std::vector<std::string>({"501", "7", "500"}),
              "guarded guild leader update");
        BoundWrite const* member = findWrite(context, "DELETE FROM `guild_member` WHERE `guid` = ?");
        check(member && member->params == std::vector<std::string>({"500", "7"}),
              "guarded member delete");
        BoundWrite const* successorRank = findWrite(context, "UPDATE `guild_member` SET `rank` = ?");
        check(successorRank &&
              successorRank->params == std::vector<std::string>({"0", "501", "7", "1"}),
              "successor rank staged with old-rank guard");
        check(guild.members.size() == 3 && guild.m_LeaderGuid.GetCounter() == 500,
              "no local mutation during assembly");
        int const sqlBeforeCompletion = g_sqlStatements;
        guild.ApplyDeletionMemberRemoval(plan);
        check(g_sqlStatements == sqlBeforeCompletion,
              "completion issues zero SQL statements");
        check(guild.members.size() == 2 && guild.members.find(500) == guild.members.end(),
              "member removed locally");
        check(guild.m_LeaderGuid.GetCounter() == 501 && guild.GetMemberSlot(ObjectGuid(HIGHGUID_PLAYER, 501))->RankId == GR_GUILDMASTER,
              "successor installed locally");
        check(sGuildMgr.removedMembers == std::vector<uint32>({500}), "membership cache updated");
        check(!sGuildMgr.events.empty(), "succession broadcast");
        check(guild.accountsUpdates == 1, "accounts refreshed");
    }
    else if (scenario == "plain_member") {
        Database database;
        CharacterPersistenceContext context(database, 500);
        Guild guild;
        addMember(guild, 500, 0, "Leader");
        addMember(guild, 501, 1, "Member");
        GuildDeletionMemberPlan plan;
        check(guild.AppendDeletionMemberRemoval(context, ObjectGuid(HIGHGUID_PLAYER, 501), plan),
              "assembly");
        check(!plan.leaderRemoved && !plan.becomesEmpty, "no succession");
        check(countWrites(context, "UPDATE `guild` SET `leader_guid`") == 0, "no leader update");
        check(countWrites(context, "DELETE FROM `guild_member` WHERE `guid` = ?") == 1,
              "member delete staged");
        guild.ApplyDeletionMemberRemoval(plan);
        check(guild.members.size() == 1 && guild.m_LeaderGuid.GetCounter() == 500,
              "leader and remaining member unchanged");
        check(sGuildMgr.events.empty(), "no succession broadcast");
    }
    else if (scenario == "empty_guild_disband") {
        Database database;
        CharacterPersistenceContext context(database, 500);
        Guild guild;
        addMember(guild, 500, 0, "Only Member");
        GuildDeletionMemberPlan plan;
        check(guild.AppendDeletionMemberRemoval(context, ObjectGuid(HIGHGUID_PLAYER, 500), plan),
              "assembly");
        check(plan.becomesEmpty, "empty-guild plan");
        check(countWrites(context, "DELETE FROM `guild_member` WHERE `guild_id` = ?") == 1,
              "all membership rows staged");
        check(countWrites(context, "DELETE FROM `guild` WHERE `guild_id` = ?") == 1 &&
              countWrites(context, "DELETE FROM `guild_rank` WHERE `guild_id` = ?") == 1 &&
              countWrites(context, "DELETE FROM `guild_eventlog` WHERE `guild_id` = ?") == 1,
              "guild tables staged");
        guild.ApplyDeletionMemberRemoval(plan);
        check(guild.members.empty(), "members cleared");
        check(sGuildMgr.removedGuilds == std::vector<uint32>({7}), "guild removed from manager");
        check(!sGuildMgr.events.empty(), "disband broadcast");
    }
    else if (scenario == "missing_member") {
        Database database;
        CharacterPersistenceContext context(database, 500);
        Guild guild;
        GuildDeletionMemberPlan plan;
        check(!guild.AppendDeletionMemberRemoval(context, ObjectGuid(HIGHGUID_PLAYER, 999), plan),
              "unknown member rejected");
        check(context.IsInvalid(), "context invalidated");
        check(context.m_batch.writes.empty(), "no SQL staged");
    }
    else { std::cerr << "unknown\n"; return 2; }

    if (failures) { std::cerr << "failures=" << failures << "\n"; return 1; }
    std::cout << "guild-deletion " << scenario << ": PASS\n";
    return 0;
}
'''


def extract():
    source = SOURCE.read_text(encoding="utf-8")
    append = source[source.index("bool Guild::AppendDeletionMemberRemoval("):
                    source.index("void Guild::ApplyDeletionMemberRemoval(")]
    apply = source[source.index("void Guild::ApplyDeletionMemberRemoval("):
                   source.index("void Guild::BroadcastToGuild(")]
    return append, apply


@unittest.skipUnless(shutil.which("cmake"), "cmake not available")
class GuildDeletionExecutionTests(unittest.TestCase):
    def test_guild_deletion_relationship(self):
        append, apply = extract()
        program = PREAMBLE + "\n" + append + "\n" + apply + "\n" + MAIN
        with tempfile.TemporaryDirectory(prefix="fn05h-guild-del-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(guild_del_exec CXX)\n"
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
            for scenario in ["succession", "plain_member", "empty_guild_disband",
                             "missing_member"]:
                r = subprocess.run([str(exe), scenario], text=True,
                                   capture_output=True, timeout=30)
                if r.returncode != 0 or ("guild-deletion " + scenario + ": PASS") not in r.stdout:
                    failures.append(scenario + "\n" + r.stdout + r.stderr)
            self.assertFalse(failures, "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
