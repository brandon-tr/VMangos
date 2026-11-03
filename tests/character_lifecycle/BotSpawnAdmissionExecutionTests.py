#!/usr/bin/env python3
"""Actual PlayerBotAI::SpawnNewPlayer + actual PlayerNameReservation lifetime."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CPP = ROOT / "src/game/PlayerBots/PlayerBotAI.cpp"
HEADER = ROOT / "src/game/ObjectMgr.h"
OBJMGR_CPP = ROOT / "src/game/ObjectMgr.cpp"
RECOVERY = ROOT / "src/game/Database/CharacterPersistenceRecovery.cpp"


def extract(source, signature):
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError("unterminated " + signature)


PREAMBLE = r'''
#include "Database/CharacterPersistenceRecovery.h"
#include <algorithm>
#include <iostream>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <type_traits>
#include <vector>

using uint32 = unsigned int;
using uint64 = unsigned long long;
using uint8 = unsigned char;

bool g_createOk = true, g_mapOk = true, g_addOk = true, g_cacheOk = true;
int g_created = 0, g_mapAdds = 0, g_cacheInserts = 0, g_sessionPublications = 0;
int g_masterLive = 0, g_playerLive = 0, g_playerDestroyed = 0, g_socialLoads = 0;
int g_partCalls = 0, g_removeCalls = 0;
bool g_removeFlag = false;
std::vector<std::string> g_events;
bool g_createHeld = false, g_mapHeld = false, g_cacheHeld = false;
bool g_sessionHeld = false, g_accessorHeld = false, g_updateHeld = false;
bool g_cacheAdmission = false, g_sessionAdmission = false, g_accessorAdmission = false;
std::string g_createdName, g_cacheName;
uint32 g_account = 0;
bool admissible();
static uint32 urand(uint32 a, uint32) { return a; }
#define ASSERT(x) do { } while (0)
struct Log { template<typename... A> void Out(A&&...) {} } sLog;
enum { LOG_BASIC=0, LOG_LVL_ERROR=0 };
enum { UNIT_FIELD_BYTES_0=0, UNIT_BYTES_0_OFFSET_GENDER=0, PLAYER_BYTES=0,
       PLAYER_BYTES_OFFSET_SKIN_ID=0, PLAYER_BYTES_OFFSET_FACE_ID=0,
       PLAYER_BYTES_OFFSET_HAIR_STYLE_ID=0, PLAYER_BYTES_OFFSET_HAIR_COLOR_ID=0,
       PLAYER_BYTES_2=0, PLAYER_BYTES_2_OFFSET_FACIAL_STYLE=0 };

// RES_CLASS

namespace {
std::mutex g_playerNameReservationMutex;
std::set<std::string> g_reservedPlayerNames;
int g_generatedNameIndex = 0;
}
struct MotionMaster { void Initialize() {} };
struct MapEntry {};
struct DungeonPersistentState {};
struct Player;
struct MasterPlayer;
struct Map { bool Add(Player*); void Remove(Player*, bool); };
struct MapStorage { template<typename T> T* LookupEntry(uint32) { return nullptr; } };
MapStorage sMapStorage;
struct MapMgr { Map* FindMap(uint32, uint32) { return g_mapOk ? &map : nullptr; } Map map; } sMapMgr;
struct PersistentMgr {
    DungeonPersistentState* AddPersistentState(MapEntry*, uint32, long, bool, bool) { return nullptr; }
} sMapPersistentStateMgr;

struct WorldSession {
    uint32 account = 0;
    Player* player = nullptr;
    MasterPlayer* master = nullptr;
    uint32 GetAccountId() const { return account; }
    void SetPlayer(Player*);
    void SetMasterPlayer(MasterPlayer*);
};
struct Player {
    WorldSession* session;
    std::string name;
    explicit Player(WorldSession* s) : session(s) { ++g_playerLive; }
    ~Player() { --g_playerLive; ++g_playerDestroyed; }
    static void SelectRandomAppearance(uint32, uint8&, uint8&, uint8&, uint8&, uint8&, uint8&) {}
    bool Create(uint32, std::string const& newName, uint32, uint8, uint8, uint8, uint8, uint8, uint8, uint8) {
        name = newName; g_createdName = newName;
        g_createHeld = (g_reservedPlayerNames.count(newName) != 0) && !admissible();
        ++g_created; return g_createOk;
    }
    uint8 GetByteValue(uint32, uint32) const { return 0; }
    uint64 GetObjectGuid() const { return 900; }
    void SetLocationMapId(uint32) {}
    void SetLocationInstanceId(uint32) {}
    void SetAutoInstanceSwitch(bool) {}
    MotionMaster* GetMotionMaster() { return &motion; }
    MotionMaster motion;
    void BindToInstance(DungeonPersistentState*, bool, bool) {}
    void Relocate(float, float, float, float) {}
    void SetMap(Map*) {}
    void SaveRecallPosition() {}
    void CreatePacketBroadcaster() {}
    Map* GetMap() { return &sMapMgr.map; }
    void SetCanModifyStats(bool) {}
    void UpdateAllStats() { g_updateHeld = (g_reservedPlayerNames.count(name) != 0) && !admissible(); }
};
struct MasterPlayer {
    explicit MasterPlayer(WorldSession*) { ++g_masterLive; g_events.push_back("master"); }
    ~MasterPlayer() { --g_masterLive; }
    void LoadPlayer(Player*) {}
    void SetSocial(void*) {}
};
struct SocialMgr { void* LoadFromDB(void*, uint64) { ++g_socialLoads; g_events.push_back("social"); return nullptr; } } sSocialMgr;
bool Map::Add(Player* player) {
    ++g_mapAdds; g_events.push_back("map_add");
    g_mapHeld = (g_reservedPlayerNames.count(player->name) != 0) && !admissible();
    return g_addOk;
}
void Map::Remove(Player* player, bool remove) {
    ++g_removeCalls; g_removeFlag = remove;
    if (remove) delete player;
}
void WorldSession::SetPlayer(Player* value) {
    player = value; ++g_sessionPublications; g_events.push_back("session_player");
    g_sessionHeld = (g_reservedPlayerNames.count(value->name) != 0) && !admissible();
}
void WorldSession::SetMasterPlayer(MasterPlayer* value) {
    master = value; g_events.push_back("session_master");
    g_sessionAdmission = !admissible();
}
struct ObjectAccessor {
    void AddObject(Player* player) {
        g_events.push_back("accessor");
        g_accessorHeld = (g_reservedPlayerNames.count(player->name) != 0) && !admissible();
        g_accessorAdmission = !admissible();
    }
} sObjectAccessor;
bool admissible() {
    return GetAccountLifecycleRegistry().TryAcquire(
        {g_account}, AccountLifecycleKind::Mutation) != nullptr;
}
struct PlayerBotEntry { uint32 playerGUID = 900; };
struct ObjectGuid { uint32 value = 0; explicit operator bool() const { return value != 0; } };
struct ObjectMgr;
static std::string GeneratePlayerName() {
    static char const* names[] = {"alpha", "alpha", "beta", "beta", "gamma", "gamma"};
    ++g_partCalls;
    return names[g_generatedNameIndex++ % 6];
}
bool isCanonicalFullPlayerName(std::string const&) { return true; }
struct ObjectMgr {
    ObjectGuid GetPlayerGuidByName(std::string const&) const { return ObjectGuid{}; }
    PlayerNameReservation ReserveFreePlayerName();
    bool InsertPlayerInCache(Player* player) {
        ++g_cacheInserts; g_events.push_back("cache");
        if (!g_cacheOk) return false;
        g_cacheName = player->name;
        g_cacheHeld = (g_reservedPlayerNames.count(player->name) != 0) && !admissible();
        g_cacheAdmission = !admissible();
        return true;
    }
};
// RES_METHODS
// RESERVE_BODY
ObjectMgr sObjectMgr;
struct PlayerBotAI {
    PlayerBotEntry* botEntry;
    bool SpawnNewPlayer(WorldSession*, uint8, uint32, uint32, uint32, float, float, float, float, Player*);
};
'''

MAIN = r'''
static int failures = 0;
static void check(bool value, std::string const& label) {
    if (!value) { std::cerr << "FAIL " << label << "\n"; ++failures; }
}
static void resetGenerator() { g_generatedNameIndex = 0; }
static bool noEffects() {
    return g_created == 0 && g_mapAdds == 0 && g_cacheInserts == 0 &&
           g_masterLive == 0 && g_socialLoads == 0 && g_sessionPublications == 0 &&
           g_partCalls == 0 && g_reservedPlayerNames.empty();
}
int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";
    PlayerBotAI ai; PlayerBotEntry entry; ai.botEntry = &entry;
    WorldSession session; session.account = 8010; g_account = 8010;
    bool result = false;
    if (scenario == "null") {
        result = ai.SpawnNewPlayer(nullptr, 1, 1, 1, 0, 0, 0, 0, 0, nullptr);
        check(!result && noEffects(), "null no generation/effects");
    } else if (scenario == "zero") {
        session.account = 0; g_account = 0;
        result = ai.SpawnNewPlayer(&session, 1, 1, 1, 0, 0, 0, 0, 0, nullptr);
        check(!result && noEffects(), "zero no generation/effects");
    } else if (scenario == "blocked") {
        BlockCharacterRecovery(CharacterRecoveryRecord{
            CharacterRecoveryKind::WholeSave, {}, {g_account}, 0, "blocked"});
        result = ai.SpawnNewPlayer(&session, 1, 1, 1, 0, 0, 0, 0, 0, nullptr);
        check(!result && noEffects(), "blocked no generation/effects");
    } else if (scenario == "exhaustion") {
        g_reservedPlayerNames = {"Alpha Alpha", "Beta Beta", "Gamma Gamma"};
        g_partCalls = 0;
        result = ai.SpawnNewPlayer(&session, 1, 1, 1, 0, 0, 0, 0, 0, nullptr);
        check(!result && g_partCalls == 200 && g_reservedPlayerNames.size() == 3,
              "actual 100-attempt exhaustion");
        check(admissible() && g_created == 0 && g_playerLive == 0,
              "exhaustion no player/admission release");
    } else if (scenario == "create_failure") {
        g_createOk = false;
        result = ai.SpawnNewPlayer(&session, 1, 1, 1, 0, 0, 0, 0, 0, nullptr);
        check(!result && g_created == 1 && g_playerLive == 0 &&
              g_playerDestroyed == 1 && g_reservedPlayerNames.empty(),
              "create failure destroys Player/releases name");
        check(admissible(), "create failure releases admission");
    } else if (scenario == "map_missing") {
        g_mapOk = false;
        result = ai.SpawnNewPlayer(&session, 1, 1, 1, 0, 0, 0, 0, 0, nullptr);
        check(!result && g_playerLive == 0 && g_playerDestroyed == 1 &&
              g_reservedPlayerNames.empty(), "missing map destroys Player");
        check(admissible(), "missing map releases admission");
    } else if (scenario == "map_add_failure") {
        g_addOk = false;
        result = ai.SpawnNewPlayer(&session, 1, 1, 1, 0, 0, 0, 0, 0, nullptr);
        check(!result && g_mapAdds == 1 && g_playerLive == 0 &&
              g_playerDestroyed == 1 && g_masterLive == 0 && g_socialLoads == 0 &&
              g_reservedPlayerNames.empty(), "map-add cleanup");
        check(g_removeCalls == 0 && session.player == nullptr && session.master == nullptr,
              "map-add no session/remove");
    } else if (scenario == "cache_failure") {
        g_cacheOk = false;
        result = ai.SpawnNewPlayer(&session, 1, 1, 1, 0, 0, 0, 0, 0, nullptr);
        check(!result && g_removeCalls == 1 && g_removeFlag &&
              g_playerDestroyed == 1 && g_playerLive == 0 &&
              g_masterLive == 0 && g_socialLoads == 0 &&
              g_sessionPublications == 0 && g_reservedPlayerNames.empty(),
              "cache failure Remove(true) destroys Player");
        check(session.player == nullptr && session.master == nullptr,
              "cache failure no session publication");
        check(admissible(), "cache failure releases admission");
    } else if (scenario == "reserve_lifecycle") {
        { PlayerNameReservation inactive;
          check(!inactive && g_reservedPlayerNames.empty(), "default inactive harmless"); }
        resetGenerator();
        PlayerNameReservation alpha = sObjectMgr.ReserveFreePlayerName();
        check(alpha && alpha.Name() == "Alpha Alpha", "alpha reserved");
        resetGenerator();
        PlayerNameReservation beta = sObjectMgr.ReserveFreePlayerName();
        check(beta && beta.Name() == "Beta Beta" && g_partCalls >= 4 &&
              g_reservedPlayerNames.count("Alpha Alpha") == 1,
              "held name skipped after real attempt");
        alpha.Release();
        resetGenerator();
        PlayerNameReservation alpha2 = sObjectMgr.ReserveFreePlayerName();
        check(alpha2 && alpha2.Name() == "Alpha Alpha", "released name reusable");
        PlayerNameReservation destination;
        { PlayerNameReservation source = std::move(alpha2); destination = std::move(source); }
        check(destination && destination.Name() == "Alpha Alpha" && !alpha2 &&
              g_reservedPlayerNames.count("Alpha Alpha") == 1,
              "moved-from source destruction keeps destination");
        PlayerNameReservation gamma = sObjectMgr.ReserveFreePlayerName();
        destination = std::move(gamma);
        check(destination && destination.Name() == "Gamma Gamma" &&
              g_reservedPlayerNames.count("Alpha Alpha") == 0,
              "move assignment releases old destination");
        PlayerNameReservation& alias = destination;
        destination = std::move(alias);
        check(destination && g_reservedPlayerNames.count("Gamma Gamma") == 1,
              "self move preserves reservation");
        destination.Release();
        beta.Release();
        resetGenerator();
        PlayerNameReservation replacement;
        {
            PlayerNameReservation old = sObjectMgr.ReserveFreePlayerName();
            check(old && old.Name() == "Alpha Alpha", "old alpha");
            old.Release();
            resetGenerator();
            replacement = sObjectMgr.ReserveFreePlayerName();
            check(replacement && replacement.Name() == "Alpha Alpha", "replacement alpha");
            old.Release();
            check(g_reservedPlayerNames.count("Alpha Alpha") == 1,
                  "old receipt cannot erase replacement");
        }
        check(g_reservedPlayerNames.count("Alpha Alpha") == 1,
              "replacement survives old destruction");
        beta.Release();
        g_reservedPlayerNames = {"Alpha Alpha", "Beta Beta", "Gamma Gamma"};
        resetGenerator(); g_partCalls = 0;
        PlayerNameReservation exhausted = sObjectMgr.ReserveFreePlayerName();
        check(!exhausted && g_partCalls == 200 && g_reservedPlayerNames.size() == 3,
              "100 attempts generate 200 parts");
        static_assert(!std::is_copy_constructible<PlayerNameReservation>::value,
                      "noncopy constructible");
        static_assert(!std::is_copy_assignable<PlayerNameReservation>::value,
                      "noncopy assignable");
        static_assert(std::is_move_constructible<PlayerNameReservation>::value,
                      "move constructible");
        static_assert(std::is_move_assignable<PlayerNameReservation>::value,
                      "move assignable");
        result = true;
    } else if (scenario == "success") {
        result = ai.SpawnNewPlayer(&session, 1, 1, 1, 0, 0, 0, 0, 0, nullptr);
        check(result && g_createdName == "Alpha Alpha" && g_cacheName == "Alpha Alpha",
              "canonical name reaches Create/cache");
        check(g_createHeld && g_mapHeld && g_cacheHeld && g_sessionHeld &&
              g_accessorHeld && g_updateHeld, "reservation held at every boundary");
        check(g_cacheAdmission && g_sessionAdmission && g_accessorAdmission,
              "account excluded at every boundary");
        check(g_masterLive == 1 && g_socialLoads == 1 &&
              session.player != nullptr && session.master != nullptr,
              "one published Player/master");
        auto index = [](std::string const& name) {
            for (std::size_t i = 0; i < g_events.size(); ++i)
                if (g_events[i] == name) return i;
            return std::size_t(-1);
        };
        check(index("map_add") < index("cache") && index("cache") < index("master") &&
              index("master") < index("social") && index("social") < index("session_player") &&
              index("session_player") < index("session_master") &&
              index("session_master") < index("accessor"), "publication order");
        check(g_reservedPlayerNames.empty(), "reservation released after return");
        check(admissible(), "admission released after return");
        Player* publishedPlayer = session.player;
        MasterPlayer* publishedMaster = session.master;
        session.player = nullptr; session.master = nullptr;
        delete publishedPlayer; delete publishedMaster;
        check(g_playerLive == 0 && g_masterLive == 0, "published fakes disposed once");
    } else { std::cerr << "unknown scenario\n"; return 2; }
    if (failures) { std::cerr << "failures=" << failures << "\n"; return 1; }
    std::cout << "bot " << scenario << ": PASS\n";
    return 0;
}
'''


@unittest.skipUnless(shutil.which("cmake"), "cmake not available")
class BotSpawnAdmissionExecutionTests(unittest.TestCase):
    def test_actual_spawn(self):
        cpp = CPP.read_text(encoding="utf-8")
        header = HEADER.read_text(encoding="utf-8")
        objmgr = OBJMGR_CPP.read_text(encoding="utf-8")
        body = extract(cpp, "bool PlayerBotAI::SpawnNewPlayer")
        reservation_class = extract(header, "class PlayerNameReservation") + ";"
        reservation_methods = "\n".join(extract(objmgr, sig) for sig in [
            "PlayerNameReservation::PlayerNameReservation(PlayerNameReservation&& other) noexcept",
            "PlayerNameReservation& PlayerNameReservation::operator=(",
            "PlayerNameReservation::~PlayerNameReservation()",
            "void PlayerNameReservation::Release()"])
        capitalize = extract(objmgr, "static std::string CapitalizeAscii")
        reserve_body = extract(objmgr, "PlayerNameReservation ObjectMgr::ReserveFreePlayerName()")
        program = (PREAMBLE.replace("// RES_CLASS", reservation_class)
                   .replace("// RES_METHODS", reservation_methods)
                   .replace("// RESERVE_BODY", capitalize + "\n" + reserve_body) +
                   "\n" + body + "\n" + MAIN)
        with tempfile.TemporaryDirectory(prefix="fn05h-v52-bot-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(bot_exec CXX)\n"
                "set(CMAKE_CXX_STANDARD 14)\n"
                f"add_executable(probe probe.cpp \"{RECOVERY.as_posix()}\")\n"
                f"target_include_directories(probe PRIVATE "
                f"\"{(ROOT / 'src/game').as_posix()}\" "
                f"\"{(ROOT / 'src/shared').as_posix()}\" "
                f"\"{(ROOT / 'src/framework').as_posix()}\")\n",
                encoding="utf-8")
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
            executable = build / "Release" / "probe.exe"
            if not executable.exists():
                executable = build / "probe"
            failures = []
            for scenario in ["null", "zero", "blocked", "exhaustion",
                             "create_failure", "map_missing", "map_add_failure",
                             "cache_failure", "reserve_lifecycle", "success"]:
                result = subprocess.run([str(executable), scenario],
                                        text=True, capture_output=True, timeout=30)
                if result.returncode != 0 or (
                        "bot " + scenario + ": PASS") not in result.stdout:
                    failures.append(scenario + "\n" + result.stdout + result.stderr)
            self.assertFalse(failures, "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
