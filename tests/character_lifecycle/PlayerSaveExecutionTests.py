#!/usr/bin/env python3
"""Executable regression for ordinary-save orchestration.

The real `Player::SaveToDB`, `Player::AppendSaveToDB` and
`Player::AppendIdentityGenerationPublication` bodies are extracted from
CharacterSavePersistence.cpp and compiled against recording stubs for the
individual appenders.  The scenarios cover one guarded execution, saving-disabled
and deferred-teleport early returns, appender failure, known/unknown outcomes,
generation publication through the commit callback, stats inclusion rules, and
pet save only after a confirmed commit.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/game/Database/CharacterSavePersistence.cpp"

PREAMBLE = r'''
#include <algorithm>
#include <ctime>
#include <functional>
#include <iostream>
#include <limits>
#include <string>
#include <vector>

using uint32 = unsigned int;
using uint64 = unsigned long long;

enum { DELAYED_SAVE_PLAYER = 1, PET_SAVE_AS_CURRENT = 0 };
enum { CONFIG_UINT32_INTERVAL_SAVE = 1, CONFIG_BOOL_STATS_SAVE_ONLY_ON_LOGOUT = 2,
       CONFIG_UINT32_MIN_LEVEL_STAT_SAVE = 3 };
enum { HIGHGUID_PLAYER = 0 };
enum { LOG_BASIC = 0, LOG_LVL_ERROR = 0, LOG_LVL_MINIMAL = 0 };

struct ObjectGuid {
    uint32 value = 0;
    ObjectGuid() {}
    ObjectGuid(int, uint32 v) : value(v) {}
    uint32 GetCounter() const { return value; }
};

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
struct SqlCheckedBatch {
    void* db = nullptr;
    void* GetDatabase() const { return nullptr; }
    bool Empty() const { return true; }
};
struct Database {};
Database CharacterDatabase;


enum class AccountLifecycleKind { Mutation, Deletion };
class AccountLifecycleLease;
struct CharacterPersistenceAdmission {
    bool admitOk = true;
    bool Admit(std::vector<uint32> const&, AccountLifecycleKind,
               AccountLifecycleLease const* = nullptr) { return admitOk; }
};

struct CharacterPersistenceContext {
    CharacterPersistenceContext(Database&, uint32) {}
    SqlCheckedBatch& Batch() { return m_batch; }
    bool IsInvalid() const { return m_invalid; }
    bool WasSubmitted() const { return m_submitted; }
    void Invalidate() { m_invalid = true; m_acks.clear(); }
    bool HasIdentityGuard(CharacterIdentitySnapshot const&) const { return true; }
    bool AppendIdentityGuards(std::vector<CharacterIdentitySnapshot> const& identities) {
        m_identities = identities;
        return g_guardOk;
    }
    std::vector<CharacterIdentitySnapshot> const& StagedIdentities() const { return m_identities; }
    void OnCommit(std::function<void()> action) {
        if (!m_invalid && action)
            m_acks.push_back(std::move(action));
    }
    SqlBatchResult Execute() {
        ++g_executeCalls;
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
    static bool g_guardOk;
    static int g_executeCalls;
    SqlCheckedBatch m_batch;
    bool m_invalid = false;
    bool m_submitted = false;
    std::vector<std::function<void()>> m_acks;
    std::vector<CharacterIdentitySnapshot> m_identities;
};
SqlBatchResult CharacterPersistenceContext::g_result;
bool CharacterPersistenceContext::g_guardOk = true;
int CharacterPersistenceContext::g_executeCalls = 0;

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
bool g_characterBlocked = false;
bool IsCharacterRecoveryBlocked(uint32) { return g_characterBlocked; }

std::vector<std::string> g_events;
struct LogStub {
    template<typename... A> void Out(A&&...) {}
} sLog;

struct WorldStub {
    uint32 interval = 300;
    bool statsOnlyOnLogout = false;
    uint32 minLevelStats = 0;
    uint64 GetGameTime() { return 0; }
    uint32 getConfig(uint32 id) {
        if (id == CONFIG_UINT32_INTERVAL_SAVE) return interval;
        if (id == CONFIG_BOOL_STATS_SAVE_ONLY_ON_LOGOUT) return statsOnlyOnLogout ? 1 : 0;
        if (id == CONFIG_UINT32_MIN_LEVEL_STAT_SAVE) return minLevelStats;
        return 0;
    }
    bool getConfigBool(uint32 id) { return id == CONFIG_BOOL_STATS_SAVE_ONLY_ON_LOGOUT && statsOnlyOnLogout; }
} sWorld;
inline uint32 getConfig(uint32 id) { return sWorld.getConfig(id); }

struct ObjectMgrStub {
    int worldMasks = 0;
    int cachedLevels = 0;
    void SetPlayerWorldMask(uint32, uint32) { ++worldMasks; }
    void UpdatePlayerCachedLevelAndZone(uint32, uint32, uint32) { ++cachedLevels; }
} sObjectMgr;

struct MasterPlayer {
    uint64 identityGeneration = 9;
    bool actionsOk = true;
    bool mailsOk = true;
    int actionSaves = 0;
    int mailSaves = 0;
    void SetIdentityGeneration(uint64 value) { identityGeneration = value; }
    bool AppendActionsToDB(CharacterPersistenceContext&) {
        ++actionSaves;
        g_events.push_back("master_actions");
        return actionsOk;
    }
    bool AppendMailsToDB(CharacterPersistenceContext&) {
        ++mailSaves;
        g_events.push_back("master_mails");
        return mailsOk;
    }
};

struct WorldSession {
    uint32 account = 42;
    bool loggingOut = false;
    MasterPlayer master;
    uint32 GetAccountId() const { return account; }
    bool IsLogingOut() const { return loggingOut; }
    MasterPlayer* GetMasterPlayer() { return &master; }
    bool AppendTutorialsDataToContext(CharacterPersistenceContext&) {
        g_events.push_back("tutorials");
        return true;
    }
};

struct HonorMgrStub {
    void Update() { g_events.push_back("honor_update"); }
    bool AppendSaveToDB(CharacterPersistenceContext&) {
        g_events.push_back("honor");
        return true;
    }
};
struct ReputationMgrStub {
    bool AppendSaveToDB(CharacterPersistenceContext&) {
        g_events.push_back("reputation");
        return true;
    }
};

struct Pet {
    int saved = 0;
    void SavePetToDB(int) { ++saved; g_events.push_back("pet"); }
};

int g_petSaves = 0;
Pet* g_pet = nullptr;

class Player {
public:
    void SaveToDB(bool online = true, bool force = false);
    bool AppendSaveToDB(CharacterPersistenceContext& context, bool online, bool includeStats);
    void AppendIdentityGenerationPublication(CharacterPersistenceContext& context);

    uint32 GetGUIDLow() const { return guid; }
    std::string const& GetName() const { return name; }
    uint32 GetWorldMask() const { return 0; }
    uint32 GetLevel() const { return 60; }
    uint32 GetCachedZoneId() const { return 0; }
    uint64 GetIdentityGeneration() const { return m_identityGeneration; }
    void SetIdentityGeneration(uint64 value) { m_identityGeneration = value; }
    WorldSession* GetSession() const { return session; }
    std::string GetGuidStr() const { return "Player-77"; }
    bool IsSavingDisabled() const { return m_saveDisabled; }
    bool IsBeingTeleportedFar() const { return m_teleporting; }
    void ScheduleDelayedOperation(int) { g_events.push_back("deferred_save"); }

    bool AppendCharacterRowToDB(CharacterPersistenceContext&, bool) { g_events.push_back("row"); return g_rowOk; }
    bool AppendBGData(CharacterPersistenceContext&) { g_events.push_back("bg"); return g_bgOk; }
    bool AppendInventoryToDB(CharacterPersistenceContext&) { g_events.push_back("inventory"); return g_inventoryOk; }
    bool AppendQuestStatus(CharacterPersistenceContext&, uint64) { g_events.push_back("quests"); return true; }
    bool AppendSpells(CharacterPersistenceContext&) { g_events.push_back("spells"); return true; }
    bool AppendSpellCooldowns(CharacterPersistenceContext&) { g_events.push_back("cooldowns"); return true; }
    bool AppendAuras(CharacterPersistenceContext&) { g_events.push_back("auras"); return true; }
    bool AppendSkills(CharacterPersistenceContext&) { g_events.push_back("skills"); return true; }
    bool AppendStats(CharacterPersistenceContext&, uint32) { g_events.push_back("stats"); return g_statsOk; }
    Pet* GetPet() const { return g_pet; }

    uint32 guid = 77;
    std::string name = "Save Name";
    uint64 m_identityGeneration = 9;
    uint32 m_nextSave = 0;
    bool m_saveDisabled = false;
    bool m_teleporting = false;
    bool g_rowOk = true;
    bool g_bgOk = true;
    bool g_inventoryOk = true;
    bool g_statsOk = true;
    WorldSession* session = new WorldSession();
    HonorMgrStub m_honorMgr;
    ReputationMgrStub m_reputationMgr;
};
'''

MAIN = r'''
static int failures = 0;
static void check(bool value, std::string const& label) {
    if (!value) { std::cerr << "FAIL " << label << "\n"; ++failures; }
}
static void resetState() {
    CharacterPersistenceContext::g_result = SqlBatchResult();
    CharacterPersistenceContext::g_guardOk = true;
    CharacterPersistenceContext::g_executeCalls = 0;
    g_recoveryRecords.clear();
    g_events.clear();
    g_characterBlocked = false;
    g_pet = nullptr;
    g_petSaves = 0;
    sWorld.statsOnlyOnLogout = false;
    sWorld.minLevelStats = 0;
    sObjectMgr.worldMasks = 0;
    sObjectMgr.cachedLevels = 0;
}
static bool hasEvent(char const* name) {
    return std::find(g_events.begin(), g_events.end(), std::string(name)) != g_events.end();
}
static std::size_t eventIndex(char const* name) {
    auto itr = std::find(g_events.begin(), g_events.end(), std::string(name));
    return itr == g_events.end() ? g_events.size() : std::size_t(itr - g_events.begin());
}

int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";

    if (scenario == "save_success") {
        resetState();
        Player player;
        Pet pet;
        g_pet = &pet;
        player.SaveToDB(true, false);
        check(CharacterPersistenceContext::g_executeCalls == 1, "one checked execution");
        check(eventIndex("row") < eventIndex("master_actions") &&
              eventIndex("master_actions") < eventIndex("master_mails"), "state then master staged");
        check(player.m_nextSave == 300, "save timer reset");
        check(player.GetIdentityGeneration() == 10 &&
              player.GetSession()->GetMasterPlayer()->identityGeneration == 10,
              "generation published once to both representations");
        check(sObjectMgr.worldMasks == 1 && sObjectMgr.cachedLevels == 1,
              "cache publication after commit");
        check(pet.saved == 1 && eventIndex("pet") > 4, "pet saved after commit");
        check(!hasEvent("deferred_save"), "not deferred");
    }
    else if (scenario == "save_disabled") {
        resetState();
        Player player;
        player.m_saveDisabled = true;
        player.SaveToDB();
        check(CharacterPersistenceContext::g_executeCalls == 0 && player.m_nextSave == 300,
              "disabled save only resets the timer");
        check(player.GetIdentityGeneration() == 9, "no generation publication");
    }
    else if (scenario == "save_deferred_teleport") {
        resetState();
        Player player;
        player.m_teleporting = true;
        player.SaveToDB(true, false);
        check(CharacterPersistenceContext::g_executeCalls == 0 && hasEvent("deferred_save"),
              "teleport defers the save");
        check(player.GetIdentityGeneration() == 9, "no generation publication");
    }
    else if (scenario == "save_generation_zero") {
        resetState();
        Player player;
        player.m_identityGeneration = 0;
        player.SaveToDB();
        check(CharacterPersistenceContext::g_executeCalls == 0, "zero generation rejected");
    }
    else if (scenario == "save_recovery_blocked") {
        resetState();
        Player player;
        g_characterBlocked = true;
        player.SaveToDB();
        check(CharacterPersistenceContext::g_executeCalls == 0, "recovery block prevents save");
    }
    else if (scenario == "save_appender_failure") {
        resetState();
        Player player;
        player.g_inventoryOk = false;
        player.SaveToDB();
        check(CharacterPersistenceContext::g_executeCalls == 0, "no execution on assembly failure");
        check(player.GetIdentityGeneration() == 9 &&
              player.GetSession()->GetMasterPlayer()->identityGeneration == 9,
              "no generation publication");
        check(!hasEvent("master_actions") && !hasEvent("master_mails"),
              "assembly stops before master staging");
    }
    else if (scenario == "save_unknown") {
        resetState();
        Player player;
        CharacterPersistenceContext::g_result.outcome = SqlWriteOutcome::OutcomeUnknown;
        player.SaveToDB();
        check(CharacterPersistenceContext::g_executeCalls == 1, "one attempt");
        check(g_recoveryRecords.size() == 1 &&
              g_recoveryRecords[0].kind == CharacterRecoveryKind::WholeSave &&
              g_recoveryRecords[0].guids == std::vector<uint32>({77}) &&
              g_recoveryRecords[0].accountIds == std::vector<uint32>({42}), "identity blocked");
        check(player.GetIdentityGeneration() == 9, "no generation publication on unknown");
        check(!hasEvent("pet"), "no pet save on unknown");
    }
    else if (scenario == "save_known_failure") {
        resetState();
        Player player;
        CharacterPersistenceContext::g_result.outcome = SqlWriteOutcome::Failed;
        player.SaveToDB();
        check(g_recoveryRecords.empty(), "known failure does not block");
        check(player.GetIdentityGeneration() == 9 && !hasEvent("pet"), "nothing published");
    }
    else if (scenario == "save_stats_rules") {
        resetState();
        Player player;
        sWorld.statsOnlyOnLogout = true;
        player.SaveToDB();
        check(!hasEvent("stats"), "stats skipped when configured for logout only");
        resetState();
        Player player2;
        player2.GetSession()->loggingOut = true;
        sWorld.statsOnlyOnLogout = true;
        player2.SaveToDB();
        check(hasEvent("stats"), "stats saved at logout");
    }
    else { std::cerr << "unknown\n"; return 2; }

    if (failures) { std::cerr << "failures=" << failures << "\n"; return 1; }
    std::cout << "player-save " << scenario << ": PASS\n";
    return 0;
}
'''


def extract():
    source = SOURCE.read_text(encoding="utf-8")
    identity = source[source.index("CharacterIdentitySnapshot SaveIdentity("):
                      source.index("bool AppendUnguardedStatement(")]
    publication = source[source.index("void Player::AppendIdentityGenerationPublication("):
                         source.index("bool Player::AppendSaveToDB(")]
    append = source[source.index("bool Player::AppendSaveToDB("):
                    source.index("void Player::SaveToDB(")]
    save = source[source.index("void Player::SaveToDB("):
                  source.index("void Player::SaveInventoryAndGoldToDB(")]
    return identity, publication, append, save


@unittest.skipUnless(shutil.which("cmake"), "cmake not available")
class PlayerSaveExecutionTests(unittest.TestCase):
    def test_player_save_orchestration(self):
        identity, publication, append, save = extract()
        program = (PREAMBLE + "\n" + identity + "\n" + publication + "\n" + append + "\n" + save + "\n" + MAIN)
        with tempfile.TemporaryDirectory(prefix="fn05h-player-save-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(player_save_exec CXX)\n"
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
            for scenario in ["save_success", "save_disabled", "save_deferred_teleport",
                             "save_generation_zero", "save_recovery_blocked",
                             "save_appender_failure", "save_unknown",
                             "save_known_failure", "save_stats_rules"]:
                r = subprocess.run([str(exe), scenario], text=True,
                                   capture_output=True, timeout=30)
                if r.returncode != 0 or ("player-save " + scenario + ": PASS") not in r.stdout:
                    failures.append(scenario + "\n" + r.stdout + r.stderr)
            self.assertFalse(failures, "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
