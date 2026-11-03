#!/usr/bin/env python3
"""Actual Player::SaveNewPlayer committed-cache-failure recovery regression."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAYER = ROOT / "src/game/Objects/Player.cpp"
IDENTITY = ROOT / "src/game/Database/CharacterIdentityPersistence.cpp"
RECOVERY = ROOT / "src/game/Database/CharacterPersistenceRecovery.cpp"
HANDLER = ROOT / "src/game/Handlers/CharacterHandler.cpp"
IDENTITY_HEADER = ROOT / "src/game/Database/CharacterIdentityPersistence.h"


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
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

using uint32 = unsigned int;
using uint64 = unsigned long long;
using uint8 = unsigned char;
using int8 = signed char;

uint8 g_forRegion = 0;
std::size_t g_forLocale = 0;
std::vector<std::string> g_characterBindings, g_ownershipBindings, g_actionBindings;
std::vector<std::string> g_bindings;
uint32 g_cacheGuid = 0, g_cacheAccount = 0;
std::string g_cacheName;
int g_positionUpdates = 0, g_worldMaskUpdates = 0;
bool g_execExcluded = false, g_cacheExcluded = false;
bool g_tutorialExcluded = false;
std::vector<std::string> g_createEvents;
bool g_canonicalOk = true;
static bool admissibleForTest();

enum { CHAR_CREATE_SUCCESS = 0, CHAR_CREATE_ERROR = 1, CHAR_CREATE_NAME_IN_USE = 2 };
enum { CONFIG_UINT32_START_GM_LEVEL = 1, CONFIG_UINT32_START_PLAYER_LEVEL = 2,
       CONFIG_UINT32_START_PLAYER_MONEY = 3 };
enum { GENDER_MALE = 0, GENDER_FEMALE = 1 };
enum { POWER_MANA = 0, POWER_ENERGY = 3, MAX_POWERS = 5 };
enum { STAT_STAMINA = 3, STAT_INTELLECT = 4 };
enum { INVENTORY_SLOT_BAG_START = 19 };
enum { PLAYER_EXPLORED_ZONES_SIZE = 64 };
enum { WORLD_DEFAULT_CHAR = 1, PLAYER_EXTRA_GM_ACCEPT_TICKETS = 2, PLAYER_EXTRA_ACCEPT_WHISPERS = 4 };
enum { HIGHGUID_PLAYER = 1, NULL_SLOT=255, INVTYPE_AMMO=14, LOG_CHAR=0, LOG_LVL_BASIC=0 };
enum { LOG_BASIC=0, LOG_LVL_ERROR=0 };
#define MAKE_PAIR32(a,b) (((uint32)(a)<<16)|(uint16)(b))

struct ObjectGuid { uint32 value; ObjectGuid(int, uint32 v) : value(v) {} explicit operator bool() const { return value != 0; } };
struct Log { template<typename... A> void Out(A&&...) {} template<typename... A> void Player(A&&...) {} } sLog;

enum class SqlWriteOutcome { Succeeded, DuplicateKey, Failed, OutcomeUnknown };
enum class SqlBatchStage { Statement, Complete };
enum class SqlAffectedRowsPolicy { ExactlyOne };
struct SqlWriteResult { SqlWriteOutcome outcome = SqlWriteOutcome::Succeeded; };
struct SqlBatchResult {
    SqlWriteOutcome outcome = SqlWriteOutcome::Succeeded;
    SqlBatchStage stage = SqlBatchStage::Complete;
    bool rollbackAttempted = false;
    SqlWriteResult rollbackResult;
    std::size_t statementIndex = 0;
    SqlWriteResult cause;
};
// NAME_STATUS

extern bool g_ownershipOk, g_actionOk, g_tutorialOk;
extern int g_executions;
extern std::vector<std::string> g_staged;
extern bool g_reserved, g_collision, g_countOk, g_realmCountOk;
extern uint32 g_count, g_publishedCount;
extern int g_countCalls, g_guidCalls, g_cacheCalls, g_realmCountCalls, g_responseSuccess, g_responseResult;
extern AccountLifecycleLease const* g_realmLease;
enum { CONFIG_UINT32_CHARACTERS_CREATING_DISABLED = 10,
       CONFIG_UINT32_CHARACTERS_PER_REALM = 11,
       CONFIG_BOOL_ALLOW_TWO_SIDE_ACCOUNTS = 12,
       CHAR_CREATE_DISABLED = 3, CHAR_CREATE_FAILED = 4, CHAR_CREATE_SERVER_LIMIT = 5,
       CHAR_CREATE_PVP_TEAMS_VIOLATION = 6, CHAR_NAME_RESERVED = 7 };

struct SqlStatement;
struct Database;
struct SqlStatementID {};
struct SqlCheckedBatch {
    bool append(std::string const& text) { statements.push_back(text); return g_appendOk; }
    std::vector<std::string> statements;
    bool g_appendOk = true;
};

struct SqlStatement {
    std::string text;
    std::string params;
    std::vector<std::string> bindings;
    void bind(std::string const& v) { bindings.push_back(v); params += v + ","; }
    template<typename T> void addUInt32(T v) { bind(std::to_string(v)); }
    template<typename T> void addUInt64(T v) { bind(std::to_string(v)); }
    template<typename T> void addUInt8(T v) { bind(std::to_string(int(v))); }
    template<typename T> void addInt8(T v) { bind(std::to_string(int(v))); }
    template<typename T> void addFloat(T v) { bind(std::to_string(v)); }
    void addString(std::string const& v) { bind(v); }
    void addString(std::ostringstream& v) { bind(v.str()); }
    bool AppendToCheckedBatch(SqlCheckedBatch& batch, SqlAffectedRowsPolicy) {
        bool ok = batch.g_appendOk;
        if (text.find("character_name_ownership") != std::string::npos) { g_staged.push_back("ownership"); g_ownershipBindings = bindings; ok = ok && g_ownershipOk; }
        else if (text.find("character_action") != std::string::npos) { g_staged.push_back("action"); g_actionBindings = bindings; ok = ok && g_actionOk; }
        else if (text.find("characters") != std::string::npos) { g_staged.push_back("character"); g_characterBindings = bindings; }
        else g_staged.push_back("other");
        g_bindings = bindings;
        batch.statements.push_back(text + "|" + params); return ok;
    }
};
struct CharacterPersistenceContext {
    CharacterPersistenceContext(Database&, uint32) {}
    SqlCheckedBatch& Batch() { return batch; }
    SqlBatchResult Execute() {
        ++g_executions; g_createEvents.push_back("execute");
        g_execExcluded = !admissibleForTest();
        return g_batch;
    }
    SqlCheckedBatch batch;
    static SqlBatchResult g_batch;
};
SqlBatchResult CharacterPersistenceContext::g_batch;

class Player;

struct WorldSession;
struct Database {
    SqlStatement CreateStatement(SqlStatementID&, char const* fmt) { SqlStatement s; s.text = fmt; return s; }
} CharacterDatabase;
struct ActionButton { uint32 action = 1; uint32 type = 0; uint32 GetAction() const { return action; } uint32 GetType() const { return type; } };
struct MasterPlayer {
    explicit MasterPlayer(WorldSession*) {}
    void Create(ObjectGuid, uint8, uint8) { actions[1] = ActionButton{}; }
    std::map<uint32, ActionButton> const& GetActionButtons() const { return actions; }
    std::map<uint32, ActionButton> actions;
};
struct PlayerTaxi { void InitTaxiNodes(uint8, uint8) {} std::string SaveTaxiDestinationsToString() const { return {}; } };
std::ostream& operator<<(std::ostream& out, PlayerTaxi const&) { return out << "0 "; }

struct PlayerLevelInfo { uint32 stats[5] = {}; };
struct PlayerClassLevelInfo { uint32 basehealth = 100, basemana = 100; };
struct ChrClassesEntry { uint32 powerType = POWER_MANA; };
struct ChrClassesStore { ChrClassesEntry entry; ChrClassesEntry const* LookupEntry(uint32) { return &entry; } } sChrClassesStore;
struct PlayerInfoItem { uint32 item_id = 0; };
struct PlayerInfo { uint32 mapId = 1; float positionX = 1, positionY = 2, positionZ = 3, orientation = 0; std::vector<PlayerInfoItem> item; };
struct ItemPrototype { uint32 InventoryType = 0; void GetAllowedEquipSlots(uint8*, uint32, bool) const {} };
bool g_cacheOk = false;
struct PlayerCacheSnapshot { explicit operator bool() const { return ok; } PlayerCacheSnapshot* operator->() { return this; } bool ok = true; uint32 uiRace = 1; };

struct Terrain { void GetZoneAndAreaId(uint32&, uint32&, float, float, float) {} };
struct Map { Terrain* GetTerrain() { static Terrain t; return &t; } };
struct MapMgr { Map* FindMap(uint32) { return &map; } Map map; } sMapMgr;
extern bool g_realmLeaseValid, g_realmExcluded;
static bool admissibleForTest();
struct World {
    uint32 getConfig(int id) {
        if (id == CONFIG_UINT32_START_PLAYER_LEVEL) return 1;
        if (id == CONFIG_UINT32_CHARACTERS_PER_REALM) return 10;
        return 0;
    }
    bool IsPvPRealm() { return false; }
    bool UpdateRealmCharCount(uint32, AccountLifecycleLease const* lease, uint32* out) {
        ++g_realmCountCalls; g_realmLease = lease; g_createEvents.push_back("count");
        g_realmLeaseValid = lease && lease->IsActive() &&
            GetAccountLifecycleRegistry().Allows(*lease, 8010, AccountLifecycleKind::Mutation);
        g_realmExcluded = !admissibleForTest();
        if (!g_realmCountOk) {
            BlockCharacterRecovery(CharacterRecoveryRecord{
                CharacterRecoveryKind::WholeSave, {}, {8010}, 0,
                "fake realm count publication failed"});
            return false;
        }
        g_publishedCount = 7; if (out) *out = 7; return true;
    }
} sWorld;
struct ObjectMgr { PlayerInfo info; ChrClassesEntry classEntry;
    PlayerInfo const* GetPlayerInfo(uint8, uint8) { return &info; }
    void GetPlayerLevelInfo(uint8, uint8, uint32, PlayerLevelInfo* out) { *out = PlayerLevelInfo{}; }
    void GetPlayerClassLevelInfo(uint8, uint32, PlayerClassLevelInfo* out) { *out = PlayerClassLevelInfo{}; }
    ItemPrototype const* GetItemPrototype(uint32) { return nullptr; }
    PlayerCacheSnapshot InsertPlayerInCache(uint32 guid, uint32, uint32, uint32, uint32 account, std::string const& name, uint32, uint32) {
        ++g_cacheCalls; g_createEvents.push_back("cache");
        g_cacheGuid = guid; g_cacheAccount = account; g_cacheName = name;
        g_cacheExcluded = !admissibleForTest();
        PlayerCacheSnapshot s; s.ok = g_cacheOk; return s;
    }
    void UpdatePlayerCachedPosition(uint32, uint32, float, float, float, float, bool) { ++g_positionUpdates; g_createEvents.push_back("position"); }
    void SetPlayerWorldMask(uint32, uint32) { ++g_worldMaskUpdates; g_createEvents.push_back("mask"); }
    bool IsReservedName(std::string const&) { return g_reserved; }
    ObjectGuid GetPlayerGuidByName(std::string const&) { return g_collision ? ObjectGuid(HIGHGUID_PLAYER, 1) : ObjectGuid(HIGHGUID_PLAYER, 0); }
    void GetPlayerDataForAccount(uint32, std::vector<PlayerCacheSnapshot>&) {}
    uint32 GeneratePlayerLowGuid() { ++g_guidCalls; return 4242; }
} sObjectMgr;
struct AccountMgr { bool TryGetCharactersCount(uint32, uint32& c) { ++g_countCalls; if (!g_countOk) return false; c = g_count; return true; } } sAccountMgr;

namespace WorldPackets { namespace Character {
struct CharCreate {
    std::string name; uint8 race=1, class_=1, gender=0, skin=0, face=0;
    uint8 hairStyle=0, hairColor=0, facialHair=0;
};
struct CharCreateResponse { uint8 result = 0; };
}}

struct WorldSession {
    uint32 account = 8010;
    uint8 region = 2; uint32 locale = 3; int security = SEC_PLAYER;
    int tutorialSaves = 0; uint32 m_charactersCount = 0;
    std::vector<uint8> responses;
    uint32 GetAccountId() const { return account; }
    int GetSecurity() const { return security; }
    uint8 GetClientRegion() const { return region; }
    uint32 GetClientLocale() const { return locale; }
    bool AppendTutorialsDataToCheckedBatch(SqlCheckedBatch& b) { g_staged.push_back("tutorial"); b.statements.push_back("tutorial"); return g_tutorialOk; }
    void MarkTutorialsDataSaved() { ++tutorialSaves; g_createEvents.push_back("tutorial_saved"); g_tutorialExcluded = !admissibleForTest(); }
    template<typename T> void SendPacket(std::unique_ptr<T> p) { responses.push_back(p->result); if (p->result == CHAR_CREATE_SUCCESS) ++g_responseSuccess; g_responseResult = p->result; }
    void HandleCharCreateOpcode(WorldPackets::Character::CharCreate const& packet);
};
bool isCanonicalFullPlayerName(std::string const&) { return g_canonicalOk; }
uint32 GetHealthBonusFromStamina(uint32 v) { return v; }
uint32 GetManaBonusFromIntellect(uint32 v) { return v; }


enum Team { ALLIANCE = 0, HORDE = 1 };
enum { CHRRACES_FLAGS_NOT_PLAYABLE = 1 };
struct ChrRacesEntry { bool HasFlag(int) const { return false; } };
struct ChrRacesStore { ChrRacesEntry entry; ChrRacesEntry const* LookupEntry(uint32) { return &entry; } } sChrRacesStore;
struct PlayerNameLists {};
PlayerNameLists const& GetPlayerNameLists() { static PlayerNameLists l; return l; }
struct PlayerNameCharsets { static PlayerNameCharsets ForClient(uint8 r, std::size_t l) { g_forRegion = r; g_forLocale = l; return {}; } };
enum { CHAR_NAME_SUCCESS = 0 };
bool g_nameOk = true; std::string g_nameResult;
uint8 prepareFullPlayerName(std::string& name, PlayerNameLists const&, PlayerNameCharsets const&) {
    g_nameResult = name; if (!g_nameOk || !g_canonicalOk) return 1; name = "Alice Smith"; return 0;
}
bool g_reserved = false; bool g_collision = false;
uint32 g_count = 0; bool g_countOk = true;
bool g_tutorialOk = true, g_actionOk = true, g_ownershipOk = true;
bool g_realmCountOk = true;
int g_countCalls = 0, g_guidCalls = 0, g_executions = 0, g_cacheCalls = 0;
int g_realmCountCalls = 0, g_responseSuccess = 0, g_responseResult = -1;
std::vector<std::string> g_staged;
AccountLifecycleLease const* g_realmLease = nullptr;
uint32 g_publishedCount = 0;
bool g_realmLeaseValid = false; bool g_realmExcluded = false;
enum { CHEAT_ACTION_LOG = 0 };
static bool admissibleForTest() { return GetAccountLifecycleRegistry().TryAcquire({8010}, AccountLifecycleKind::Mutation) != nullptr; }
void ProcessAnticheatAction(char const*, char const*, int) {}

struct Player {
    static Team TeamForRace(uint8) { return ALLIANCE; }
    static bool ValidateAppearance(uint8,uint8,uint8,uint8,uint8,uint8,uint8) { return true; }
    static uint32 GetHealthBonusFromStamina(uint32 v) { return v; }
    static uint32 GetManaBonusFromIntellect(uint32 v) { return v; }
    static uint8 SaveNewPlayer(WorldSession*, uint32, std::string const&, uint8, uint8, uint8, uint8, uint8, uint8, uint8, uint8, AccountLifecycleLease const*);
};
'''

MAIN = r'''
static int failures = 0;
static void check(bool value, std::string const& label) {
    if (!value) { std::cerr << "FAIL " << label << "\n"; ++failures; }
}
static bool hasRecovery(uint32 guid, uint32 account, uint64 generation) {
    for (auto const& record : GetCharacterRecoveryRecords())
        if (record.kind == CharacterRecoveryKind::WholeSave &&
            record.guids.size() == 1 && record.guids[0] == guid &&
            record.accountIds.size() == 1 && record.accountIds[0] == account &&
            record.expectedGeneration == generation)
            return true;
    return false;
}
int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "success";
    WorldSession session;
    WorldPackets::Character::CharCreate packet;
    packet.name = "aLICE sMITH";
    if (scenario == "handler_success") {
        g_cacheOk = true;
        session.HandleCharCreateOpcode(packet);
        check(g_responseSuccess == 1 && g_responseResult == CHAR_CREATE_SUCCESS,
              "success response");
        check(g_nameResult == "aLICE sMITH" && g_forRegion == 2 && g_forLocale == 3,
              "canonicalization captures original profile arguments");
        check(g_characterBindings.size() >= 3 &&
              g_characterBindings[0] == "4242" &&
              g_characterBindings[1] == "8010" &&
              g_characterBindings[2] == "Alice Smith",
              "character exact identity bindings");
        check(g_ownershipBindings.size() == 2 &&
              g_ownershipBindings[0] == "Alice Smith" &&
              g_ownershipBindings[1] == "4242", "ownership exact bindings");
        check(!g_actionBindings.empty() && g_actionBindings[0] == "4242",
              "action exact guid");
        check(g_cacheGuid == 4242 && g_cacheAccount == 8010 &&
              g_cacheName == "Alice Smith", "cache exact identity");
        check(g_countCalls == 1 && g_guidCalls == 1 && g_executions == 1 &&
              g_cacheCalls == 1 && session.tutorialSaves == 1 && g_realmCountCalls == 1,
              "one count/guid/execute/cache/tutorial/count publication");
        check(g_staged.size() >= 4 && g_staged[0] == "character" &&
              g_staged[1] == "ownership" && g_staged[2] == "tutorial" &&
              g_staged[3] == "action", "staging order");
        check(g_realmLeaseValid && g_realmExcluded, "realm count valid same-account lease");
        check(session.m_charactersCount == 7, "session count output");
        auto at = [](std::string const& n) {
            for (std::size_t i = 0; i < g_createEvents.size(); ++i)
                if (g_createEvents[i] == n) return i;
            return std::size_t(-1);
        };
        check(at("execute") < at("cache") && at("cache") < at("position") &&
              at("position") < at("mask") && at("mask") < at("tutorial_saved") &&
              at("tutorial_saved") < at("count"), "publication/tutorial order");
        check(g_execExcluded && g_cacheExcluded && g_tutorialExcluded,
              "admission excluded at execute/cache/tutorial");
        check(!hasRecovery(4242, session.account, 0) &&
              !hasRecovery(4242, session.account, 1) &&
              GetAccountLifecycleRegistry().TryAcquire({session.account},
                  AccountLifecycleKind::Mutation) != nullptr,
              "success no recovery/admission available");
    } else if (scenario == "handler_invalid_name") {
        g_nameOk = false;
        session.HandleCharCreateOpcode(packet);
        check(g_responseResult != CHAR_CREATE_SUCCESS && g_countCalls == 0 &&
              g_guidCalls == 0 && g_executions == 0, "invalid name no effects");
    } else if (scenario == "handler_busy") {
        auto hold = GetAccountLifecycleRegistry().TryAcquire(
            {session.account}, AccountLifecycleKind::Mutation);
        session.HandleCharCreateOpcode(packet);
        check(g_responseResult == CHAR_CREATE_ERROR && g_countCalls == 0 &&
              g_guidCalls == 0 && g_executions == 0, "busy no effects");
        hold.reset();
    } else if (scenario == "handler_blocked") {
        BlockCharacterRecovery(CharacterRecoveryRecord{
            CharacterRecoveryKind::WholeSave, {}, {session.account}, 0, "blocked"});
        session.HandleCharCreateOpcode(packet);
        check(g_responseResult == CHAR_CREATE_ERROR && g_countCalls == 0 &&
              g_guidCalls == 0 && g_executions == 0, "blocked no effects");
    } else if (scenario == "handler_reserved") {
        g_reserved = true;
        session.HandleCharCreateOpcode(packet);
        check(g_responseResult == CHAR_NAME_RESERVED && g_guidCalls == 0 &&
              g_executions == 0, "reserved no persistence");
    } else if (scenario == "handler_collision") {
        g_collision = true;
        session.HandleCharCreateOpcode(packet);
        check(g_responseResult == CHAR_CREATE_NAME_IN_USE && g_guidCalls == 0 &&
              g_executions == 0, "collision no persistence");
    } else if (scenario == "handler_count_failure") {
        g_countOk = false;
        session.HandleCharCreateOpcode(packet);
        check(g_responseResult == CHAR_CREATE_ERROR && g_guidCalls == 0 &&
              g_executions == 0, "count failure no persistence");
    } else if (scenario == "handler_limit") {
        g_count = 10;
        session.HandleCharCreateOpcode(packet);
        check(g_responseResult == CHAR_CREATE_SERVER_LIMIT && g_guidCalls == 0 &&
              g_executions == 0, "limit no persistence");
    } else if (scenario == "ownership_append_failure" || scenario == "tutorial_append_failure") {
        g_cacheOk = true;
        if (scenario == "ownership_append_failure") g_ownershipOk = false;
        else g_tutorialOk = false;
        session.HandleCharCreateOpcode(packet);
        check(g_responseResult == CHAR_CREATE_ERROR && g_executions == 0 &&
              g_cacheCalls == 0 && session.tutorialSaves == 0 &&
              g_realmCountCalls == 0, "append failure no execute/publication");
        check(GetAccountLifecycleRegistry().TryAcquire({session.account},
            AccountLifecycleKind::Mutation) != nullptr,
            "append failure releases admission");
    } else if (scenario == "known_rejection" || scenario == "ownership_duplicate" ||
               scenario == "unknown_outcome" || scenario == "cache_failure") {
        g_cacheOk = scenario == "cache_failure" ? false : true;
        if (scenario == "known_rejection") {
            CharacterPersistenceContext::g_batch.outcome = SqlWriteOutcome::Failed;
            CharacterPersistenceContext::g_batch.stage = SqlBatchStage::Statement;
        } else if (scenario == "ownership_duplicate") {
            CharacterPersistenceContext::g_batch.outcome = SqlWriteOutcome::DuplicateKey;
            CharacterPersistenceContext::g_batch.stage = SqlBatchStage::Statement;
            CharacterPersistenceContext::g_batch.statementIndex = 1;
            CharacterPersistenceContext::g_batch.cause.outcome = SqlWriteOutcome::DuplicateKey;
            CharacterPersistenceContext::g_batch.rollbackAttempted = true;
            CharacterPersistenceContext::g_batch.rollbackResult.outcome = SqlWriteOutcome::Succeeded;
        } else if (scenario == "unknown_outcome") {
            CharacterPersistenceContext::g_batch.outcome = SqlWriteOutcome::OutcomeUnknown;
        }
        session.HandleCharCreateOpcode(packet);
        if (scenario == "ownership_duplicate")
            check(g_responseResult == CHAR_CREATE_NAME_IN_USE && !hasRecovery(4242, session.account, 0),
                  "duplicate name in use no recovery");
        else if (scenario == "unknown_outcome")
            check(g_responseResult == CHAR_CREATE_ERROR && hasRecovery(4242, session.account, 0),
                  "unknown outcome exact recovery");
        else if (scenario == "cache_failure")
            check(g_responseResult == CHAR_CREATE_ERROR &&
                  hasRecovery(4242, session.account, 1) &&
                  session.tutorialSaves == 0 && g_realmCountCalls == 0,
                  "cache failure generation-1 recovery");
        else
            check(g_responseResult == CHAR_CREATE_ERROR &&
                  !hasRecovery(4242, session.account, 0) &&
                  g_cacheCalls == 0, "known rejection no cache/recovery");
        check(g_executions == 1, "one context execute");
        if (scenario == "known_rejection" || scenario == "ownership_duplicate")
            check(GetAccountLifecycleRegistry().TryAcquire({session.account},
                AccountLifecycleKind::Mutation) != nullptr,
                "known failure releases admission");
        if (scenario == "unknown_outcome" || scenario == "cache_failure")
            check(GetAccountLifecycleRegistry().TryAcquire({session.account},
                AccountLifecycleKind::Mutation) == nullptr,
                "recovery blocks admission after lease release");
    } else if (scenario == "realm_count_failure") {
        g_cacheOk = true; g_realmCountOk = false;
        session.m_charactersCount = 3;
        session.HandleCharCreateOpcode(packet);
        check(g_responseResult == CHAR_CREATE_ERROR && session.m_charactersCount == 3 &&
              g_responseSuccess == 0, "realm count failure no success/count change");
        check(g_realmLeaseValid && g_realmExcluded,
              "realm failure attempt under admission");
        check(GetAccountLifecycleRegistry().TryAcquire({session.account},
            AccountLifecycleKind::Mutation) == nullptr,
            "modeled count recovery blocks account");
    } else if (scenario.rfind("direct_", 0) == 0) {
        g_cacheOk = true;
        uint32 guid = 4242;
        if (scenario == "direct_owned_lease") {
            uint8 r = Player::SaveNewPlayer(&session, guid, "Alice Smith", 1, 1, 0, 0, 0, 0, 0, 0, nullptr);
            check(r == CHAR_CREATE_SUCCESS && g_executions == 1 && g_cacheCalls == 1 &&
                  g_execExcluded && g_cacheExcluded && g_tutorialExcluded &&
                  session.tutorialSaves == 1 &&
                  GetAccountLifecycleRegistry().TryAcquire({8010}, AccountLifecycleKind::Mutation) != nullptr,
                  "owned lease success/released");
        } else if (scenario == "direct_borrowed_lease") {
            auto lease = GetAccountLifecycleRegistry().TryAcquire({8010}, AccountLifecycleKind::Mutation);
            uint8 r = Player::SaveNewPlayer(&session, guid, "Alice Smith", 1, 1, 0, 0, 0, 0, 0, 0, lease.get());
            check(r == CHAR_CREATE_SUCCESS && lease->IsActive() &&
                  GetAccountLifecycleRegistry().Allows(*lease, 8010, AccountLifecycleKind::Mutation),
                  "borrowed lease success/retained");
            lease.reset();
            check(GetAccountLifecycleRegistry().TryAcquire({8010}, AccountLifecycleKind::Mutation) != nullptr,
                  "borrowed released after caller");
        } else if (scenario == "direct_wrong_account" || scenario == "direct_wrong_kind") {
            auto lease = GetAccountLifecycleRegistry().TryAcquire(
                {scenario == "direct_wrong_account" ? 8011u : 8010u},
                scenario == "direct_wrong_account" ? AccountLifecycleKind::Mutation
                                                    : AccountLifecycleKind::Deletion);
            uint8 r = Player::SaveNewPlayer(&session, guid, "Alice Smith", 1, 1, 0, 0, 0, 0, 0, 0, lease.get());
            check(r == CHAR_CREATE_ERROR && g_executions == 0 && g_cacheCalls == 0 &&
                  lease->IsActive(), "wrong lease rejected before persistence");
            lease.reset();
        } else if (scenario == "direct_foreign_registry") {
            AccountLifecycleRegistry foreign;
            auto lease = foreign.TryAcquire({8010}, AccountLifecycleKind::Mutation);
            uint8 r = Player::SaveNewPlayer(&session, guid, "Alice Smith", 1, 1, 0, 0, 0, 0, 0, 0, lease.get());
            check(r == CHAR_CREATE_ERROR && g_executions == 0 && g_cacheCalls == 0 &&
                  lease->IsActive(), "foreign registry lease rejected");
            lease.reset();
        } else if (scenario == "direct_null_session") {
            uint8 r = Player::SaveNewPlayer(nullptr, guid, "Alice Smith", 1, 1, 0, 0, 0, 0, 0, 0, nullptr);
            check(r == CHAR_CREATE_ERROR && g_executions == 0 && g_cacheCalls == 0, "null session rejected");
        } else if (scenario == "direct_zero_guid") {
            uint8 r = Player::SaveNewPlayer(&session, 0, "Alice Smith", 1, 1, 0, 0, 0, 0, 0, 0, nullptr);
            check(r == CHAR_CREATE_ERROR && g_executions == 0 && g_cacheCalls == 0, "zero guid rejected");
        } else if (scenario == "direct_zero_account") {
            session.account = 0;
            uint8 r = Player::SaveNewPlayer(&session, guid, "Alice Smith", 1, 1, 0, 0, 0, 0, 0, 0, nullptr);
            check(r == CHAR_CREATE_ERROR && g_executions == 0 && g_cacheCalls == 0, "zero account rejected");
        } else if (scenario == "direct_noncanonical") {
            g_canonicalOk = false;
            uint8 r = Player::SaveNewPlayer(&session, guid, "Alice Smith", 1, 1, 0, 0, 0, 0, 0, 0, nullptr);
            check(r == CHAR_CREATE_ERROR && g_executions == 0 && g_cacheCalls == 0, "noncanonical rejected");
        } else { std::cerr << "unknown direct\n"; return 2; }
    } else { std::cerr << "unknown scenario\n"; return 2; }
    if (failures) { std::cerr << "failures=" << failures << "\n"; return 1; }
    std::cout << "creation " << scenario << ": PASS\n"; return 0;
}
'''


@unittest.skipUnless(shutil.which("cmake"), "cmake not available")
class CharacterCreationExecutionTests(unittest.TestCase):
    def test_committed_cache_failure_records_recovery(self):
        source = PLAYER.read_text(encoding="utf-8")
        handler_source = HANDLER.read_text(encoding="utf-8")
        identity_header = IDENTITY_HEADER.read_text(encoding="utf-8")
        body = extract(source, "uint8 Player::SaveNewPlayer")
        handler = extract(handler_source, "void WorldSession::HandleCharCreateOpcode")
        status_enum = extract(identity_header, "enum class CharacterNameWriteStatus") + ";"
        classifier = extract(IDENTITY.read_text(encoding="utf-8"),
                             "CharacterNameWriteStatus ClassifyCharacterNameClaim")
        program = (PREAMBLE.replace("// NAME_STATUS", status_enum) + "\n" +
                   classifier + "\n" + body + "\n" + handler + "\n" + MAIN)
        with tempfile.TemporaryDirectory(prefix="fn05h-v53-create-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(create_exec CXX)\n"
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
            for scenario in ["handler_success", "handler_invalid_name", "handler_busy",
                             "handler_blocked", "handler_reserved", "handler_collision",
                             "handler_count_failure", "handler_limit",
                             "ownership_append_failure", "tutorial_append_failure",
                             "known_rejection", "ownership_duplicate", "unknown_outcome",
                             "cache_failure", "realm_count_failure",
                             "direct_owned_lease", "direct_borrowed_lease",
                             "direct_wrong_account", "direct_wrong_kind",
                             "direct_foreign_registry", "direct_null_session",
                             "direct_zero_guid", "direct_zero_account",
                             "direct_noncanonical"]:
                result = subprocess.run([str(executable), scenario], text=True,
                                        capture_output=True, timeout=30)
                if result.returncode != 0 or (
                        "creation " + scenario + ": PASS") not in result.stdout:
                    failures.append(scenario + "\n" + result.stdout + result.stderr)
            self.assertFalse(failures, "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
