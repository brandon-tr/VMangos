#!/usr/bin/env python3
"""Actual rename SQL callback -> World continuation with real recovery registry."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HANDLER = ROOT / "src/game/Handlers/CharacterHandler.cpp"
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
// REQUEST_DECL
#include "Database/CharacterPersistenceRecovery.h"
#include <algorithm>
#include <cstddef>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <limits>
#include <memory>
#include <string>
#include <utility>
#include <vector>

struct Field {
    uint32 guid = 0; std::string name; uint64 generation = 0;
    uint32 GetUInt32() const { return guid; }
    std::string GetCppString() const { return name; }
    uint64 GetUInt64() const { return generation; }
};
int g_rowCount = 1, g_fieldCount = 3; bool g_hasRow = true; int g_fetchCalls = 0;
std::vector<std::string> g_charBindings, g_ownBindings, g_appendOrder;
int g_charAppends = 0, g_ownAppends = 0;
bool g_charAppendOk = true, g_ownAppendOk = true;
bool g_execExcluded = false, g_cacheExcluded = false, g_responseExcluded = false;
std::vector<std::string> g_events;
int g_executions = 0, g_cachePublications = 0, g_responses = 0, g_invalidations = 0;
bool g_isPlayer = true;
uint32 g_sqlAccount = 0; uint32 g_sqlNonce = 0;
std::vector<std::string> g_sqlArgs;
static bool admissibleForTest();
struct QueryResult {
    Field fields[3];
    int GetRowCount() const { return g_rowCount; }
    int GetFieldCount() const { return g_fieldCount; }
    Field* Fetch() { ++g_fetchCalls; return g_hasRow ? fields : nullptr; }
};
struct ObjectGuid {
    uint32 value = 0;
    operator uint32() const { return value; }
    ObjectGuid() {}
    ObjectGuid(int, uint32 v) : value(v) {}
    uint32 GetCounter() const { return value; }
    bool IsPlayer() const { return g_isPlayer; }
};
enum { HIGHGUID_PLAYER = 0 };
bool isCanonicalFullPlayerName(std::string const& name) {
    return !name.empty() && name.find(' ') != std::string::npos;
}

struct SqlBatchResult;
enum class SqlWriteOutcome { Succeeded, Failed, DuplicateKey, OutcomeUnknown };
enum class SqlBatchStage { NotStarted, Begin, Statement, RowCount, Commit, Complete };
struct SqlWriteResult { SqlWriteOutcome outcome = SqlWriteOutcome::Succeeded; };
struct SqlBatchResult {
    SqlWriteOutcome outcome = SqlWriteOutcome::Succeeded;
    SqlBatchStage stage = SqlBatchStage::Complete;
    std::size_t statementIndex = 0;
    bool rollbackAttempted = false;
    SqlWriteResult rollbackResult;
    SqlWriteResult cause;
};
SqlBatchResult g_rename_result;
// NAME_STATUS
// CLASSIFIER

struct SqlStatementID {};
struct SqlCheckedBatch {};
enum class SqlAffectedRowsPolicy { Any, ExactlyOne };
extern int g_appendCalls;
struct SqlStatement {
    std::string text; std::vector<std::string> bindings;
    void addString(std::string const& v) { bindings.push_back(v); }
    void addUInt32(uint32 v) { bindings.push_back(std::to_string(v)); }
    void addUInt64(uint64 v) { bindings.push_back(std::to_string(v)); }
    bool AppendToCheckedBatch(SqlCheckedBatch&, SqlAffectedRowsPolicy policy) {
        ++g_appendCalls;
        if (text.find("UPDATE `characters`") != std::string::npos) {
            g_charBindings = bindings; ++g_charAppends; g_appendOrder.push_back("character");
            return g_charAppendOk && policy == SqlAffectedRowsPolicy::ExactlyOne;
        }
        if (text.find("character_name_ownership") != std::string::npos) {
            g_ownBindings = bindings; ++g_ownAppends; g_appendOrder.push_back("ownership");
            return g_ownAppendOk && policy == SqlAffectedRowsPolicy::ExactlyOne;
        }
        g_appendOrder.push_back("other"); return true;
    }
};



int g_appendCalls = 0;
int g_execCalls = 0;


struct RenameResponse {
    uint8 result = 0; uint32 renamedCharacterGuid = 0; std::string newName;
};
enum { RESPONSE_SUCCESS = 0, CHAR_CREATE_ERROR = 1, CHAR_CREATE_NAME_IN_USE = 2 };
namespace WorldPackets { namespace Character {
struct CharRenameResponse : RenameResponse {};
struct CharRename { ObjectGuid guid; std::string newname; };
}}
enum { CHAR_NAME_SUCCESS = 0, CHAR_NAME_RESERVED = 3 };
struct PlayerNameLists {};
PlayerNameLists const& GetPlayerNameLists() { static PlayerNameLists l; return l; }
uint8 g_forRegion = 0;
std::size_t g_forLocale = 0;
struct PlayerNameCharsets { static PlayerNameCharsets ForClient(uint8 r, std::size_t l) { g_forRegion = r; g_forLocale = l; return {}; } };
bool g_prepareOk = true;
uint8 prepareFullPlayerName(std::string& name, PlayerNameLists const&, PlayerNameCharsets const&) {
    if (!g_prepareOk) return 1; name = "Alice Smith"; return 0;
}
bool g_reservedName = false;
struct WorldPacketData {};

struct WorldSession;
struct World {
    WorldSession* session = nullptr;
    int invalidations = 0;
    struct Messager {
        std::function<void(World*)> message;
        template<typename F> void AddMessage(F&& fn) { message = std::forward<F>(fn); }
    } messager;
    Messager& GetMessager() { return messager; }
    WorldSession* FindSession(uint32) { return session; }
    void InvalidatePlayerDataToAllClient(ObjectGuid) { ++invalidations; ++g_invalidations; g_events.push_back("invalidate"); }
};
World sWorld;

struct WorldSession {
    using CharacterRenameRequest = ExtractedCharacterRenameRequest;
    uint32 accountId = 0;
    uint64 guidNonce = 0;
    int sent = 0;
    RenameResponse lastResponse;
    bool player = false;
    bool loading = false;
    uint32 GetAccountId() const { return accountId; }
    uint32 GetGUID() const { return static_cast<uint32>(guidNonce); }
    bool GetPlayer() const { return player; }
    bool PlayerLoading() const { return loading; }
    std::string GetRemoteAddress() const { return "test"; }
    uint8 region = 2; uint32 locale = 3; int security = 0;
    uint8 GetClientRegion() const { return region; }
    uint32 GetClientLocale() const { return locale; }
    int GetSecurity() const { return security; }
    void HandleCharRenameOpcode(WorldPackets::Character::CharRename const& packet);
    template<typename T> void SendPacket(std::unique_ptr<T> packet) {
        ++sent; ++g_responses; lastResponse = *packet;
        g_events.push_back("response");
        g_responseExcluded = !admissibleForTest();
    }
    static void HandleChangePlayerNameOpcodeCallBack(
        std::unique_ptr<QueryResult>, uint32, uint32,
        CharacterRenameRequest);
};

bool g_scheduleOk = true;
bool g_scheduled = false;
WorldSession::CharacterRenameRequest g_pendingRequest;
std::function<void(std::unique_ptr<QueryResult>, uint32, uint32,
    WorldSession::CharacterRenameRequest)> g_pendingCallback;

struct Database {
    SqlStatement CreateStatement(SqlStatementID&, char const* fmt) { SqlStatement s; s.text = fmt; return s; }
    void escape_string(std::string&) {}
    template<typename F>
    bool AsyncPQuery(F fn, uint32 account, uint32 nonce,
                     WorldSession::CharacterRenameRequest request, char const*,
                     uint32 a1, uint32 a2, uint32 a3, uint32 a4,
                     char const* a5, uint32 a6) {
        g_scheduled = g_scheduleOk;
        g_sqlAccount = account; g_sqlNonce = nonce;
        g_sqlArgs = {std::to_string(a1), std::to_string(a2), std::to_string(a3),
                     std::to_string(a4), a5, std::to_string(a6)};
        if (!g_scheduleOk) return false;
        g_pendingCallback = fn; g_pendingRequest = request;
        return true;
    }
};
Database CharacterDatabase;
struct CharacterPersistenceContext {
    Database& database; SqlCheckedBatch batch;
    CharacterPersistenceContext(Database& db, uint32) : database(db) {}
    SqlCheckedBatch& Batch() { return batch; }
    SqlBatchResult Execute() {
        ++g_execCalls; ++g_executions; g_events.push_back("execute");
        g_execExcluded = !admissibleForTest();
        return g_rename_result;
    }
};
bool g_changeOk = true;
bool g_loadOk = true;
int g_changeCalls = 0;
int g_loadCalls = 0;
struct ObjectMgr {
    bool IsReservedName(std::string const&) { return g_reservedName; }
    bool ChangePlayerNameInCache(uint32, std::string const&, std::string const&) {
        ++g_changeCalls; ++g_cachePublications; g_events.push_back("cache");
        g_cacheExcluded = !admissibleForTest();
        return g_changeOk;
    }
    bool LoadPlayerCacheData(uint32) { ++g_loadCalls; return g_loadOk; }
};
ObjectMgr sObjectMgr;
static bool admissibleForTest() { return GetAccountLifecycleRegistry().TryAcquire({3010}, AccountLifecycleKind::Mutation) != nullptr; }

enum { LOG_BASIC = 0, LOG_CHAR = 1, LOG_LVL_ERROR = 0, LOG_LVL_BASIC = 1 };
struct Log {
    template<typename... Args> void Out(Args&&...) {}
    template<typename... Args> void Player(Args&&...) {}
};
Log sLog;
enum { CHARACTER_FLAG_RENAME = 1u, CHARACTER_FLAG_RENAME_NEEDS_GM_REVIEW = 2u };
'''

MAIN = r'''
static int failures = 0;
static void check(bool value, std::string const& label) {
    if (!value) { std::cerr << "FAIL " << label << "\n"; ++failures; }
}
static bool hasRenameRecord(uint32 guid, uint32 account, uint64 generation) {
    for (CharacterRecoveryRecord const& record : GetCharacterRecoveryRecords())
        if (record.kind == CharacterRecoveryKind::Rename &&
            std::find(record.guids.begin(), record.guids.end(), guid) != record.guids.end() &&
            std::find(record.accountIds.begin(), record.accountIds.end(), account) != record.accountIds.end() &&
            record.expectedGeneration == generation) return true;
    return false;
}
static std::unique_ptr<QueryResult> validResult(uint32 guid, char const* name, uint64 gen) {
    std::unique_ptr<QueryResult> result(new QueryResult());
    result->fields[0].guid = guid;
    result->fields[1].name = name;
    result->fields[2].generation = gen;
    return result;
}
static bool admissible(uint32 account) {
    return GetAccountLifecycleRegistry().TryAcquire(
        {account}, AccountLifecycleKind::Mutation) != nullptr;
}
int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";
    uint32 account = 3010;
    uint32 guid = 77;
    uint64 generation = 9;
    uint64 nonce = 0x1234;

    WorldSession session;
    session.accountId = account; session.guidNonce = nonce;
    sWorld.session = &session;
    auto lease = GetAccountLifecycleRegistry().TryAcquire(
        {account}, AccountLifecycleKind::Mutation);
    WorldSession::CharacterRenameRequest request{guid, "Alice Smith", lease};

    if (scenario == "cache_failure_block") {
        g_changeOk = g_loadOk = false;
        session.HandleChangePlayerNameOpcodeCallBack(
            validResult(guid, "Old Smith", generation), account, nonce, request);
        check(sWorld.messager.message != nullptr, "message queued");
        lease.reset(); request.lease.reset();
        check(!admissible(account), "queued message alone holds admission");
        sWorld.messager.message(&sWorld);
        check(session.sent == 1 && session.lastResponse.result == CHAR_CREATE_ERROR,
              "cache failure response");
        check(sWorld.invalidations == 0, "no success invalidation");
        check(hasRenameRecord(guid, account, generation), "rename recovery record");
        lease.reset(); request.lease.reset(); sWorld.messager.message = nullptr;
        check(!admissible(account), "cache failure survives lease release");
    } else if (scenario == "success") {
        session.HandleChangePlayerNameOpcodeCallBack(
            validResult(guid, "Old Smith", generation), account, nonce, request);
        sWorld.messager.message(&sWorld);
        check(session.sent == 1 && session.lastResponse.result == RESPONSE_SUCCESS,
              "rename success response");
        check(sWorld.invalidations == 1, "one invalidation");
        check(GetCharacterRecoveryRecords().empty(), "success no recovery");
        lease.reset(); request.lease.reset(); sWorld.messager.message = nullptr;
        check(admissible(account), "success releases admission");
    } else if (scenario == "fallback_success") {
        g_changeOk = false; g_loadOk = true;
        session.HandleChangePlayerNameOpcodeCallBack(
            validResult(guid, "Old Smith", generation), account, nonce, request);
        sWorld.messager.message(&sWorld);
        check(session.lastResponse.result == RESPONSE_SUCCESS, "fallback success");
        check(g_changeCalls == 1 && g_loadCalls == 1, "fallback calls");
        check(GetCharacterRecoveryRecords().empty(), "fallback no recovery");
    } else if (scenario == "replacement_session") {
        session.guidNonce = nonce + 1;
        session.HandleChangePlayerNameOpcodeCallBack(
            validResult(guid, "Old Smith", generation), account, nonce, request);
        sWorld.messager.message(&sWorld);
        check(session.sent == 0 && g_changeCalls == 0 && g_loadCalls == 0 &&
              g_appendCalls == 0 && g_execCalls == 0,
              "replacement nonce no persistence/publication");
    } else if (scenario == "missing_session") {
        sWorld.session = nullptr;
        session.HandleChangePlayerNameOpcodeCallBack(
            validResult(guid, "Old Smith", generation), account, nonce, request);
        sWorld.messager.message(&sWorld);
        check(session.sent == 0 && g_changeCalls == 0 && g_loadCalls == 0 &&
              g_appendCalls == 0 && g_execCalls == 0,
              "missing session no persistence/publication");
    } else if (scenario == "known_rollback") {
        g_rename_result.outcome = SqlWriteOutcome::Failed;
        g_rename_result.stage = SqlBatchStage::Statement;
        g_rename_result = {SqlWriteOutcome::Failed, SqlBatchStage::Statement, 0};
        session.HandleChangePlayerNameOpcodeCallBack(
            validResult(guid, "Old Smith", generation), account, nonce, request);
        sWorld.messager.message(&sWorld);
        check(session.lastResponse.result == CHAR_CREATE_ERROR, "rollback failure");
        check(GetCharacterRecoveryRecords().empty(), "known rollback no recovery");
    } else if (scenario == "unknown_commit") {
        g_rename_result.outcome = SqlWriteOutcome::OutcomeUnknown;
        g_rename_result = {SqlWriteOutcome::OutcomeUnknown, SqlBatchStage::Commit, 0};
        session.HandleChangePlayerNameOpcodeCallBack(
            validResult(guid, "Old Smith", generation), account, nonce, request);
        sWorld.messager.message(&sWorld);
        lease.reset(); request.lease.reset(); sWorld.messager.message = nullptr;
        check(!admissible(account), "unknown commit blocks after release");
        check(hasRenameRecord(guid, account, generation), "unknown record");
    } else if (scenario == "pending_destroy") {
        session.HandleChangePlayerNameOpcodeCallBack(
            validResult(guid, "Old Smith", generation), account, nonce, request);
        lease.reset(); request.lease.reset();
        check(!admissible(account), "pending message alone holds admission");
        sWorld.messager.message = nullptr;
        check(admissible(account), "destroyed pending message releases admission");
        check(session.sent == 0, "destroyed message not executed");
    } else { std::cerr << "unknown scenario\n"; return 2; }

    if (failures) { std::cerr << "failures=" << failures << "\n"; return 1; }
    std::cout << "rename scenario " << scenario << ": PASS\n";
    return 0;
}
'''

def generated_program(body, main=None):
    header = (ROOT / "src/game/Server/WorldSession.h").read_text(encoding="utf-8")
    decl = extract(header, "struct CharacterRenameRequest")
    decl = decl.replace("struct CharacterRenameRequest",
                        "struct ExtractedCharacterRenameRequest", 1) + ";"
    identity_header = (ROOT / "src/game/Database/CharacterIdentityPersistence.h").read_text(encoding="utf-8")
    identity_source = (ROOT / "src/game/Database/CharacterIdentityPersistence.cpp").read_text(encoding="utf-8")
    status_enum = extract(identity_header, "enum class CharacterNameWriteStatus") + ";"
    classifier = extract(identity_source, "CharacterNameWriteStatus ClassifyCharacterNameClaim")
    program = PREAMBLE.replace("// REQUEST_DECL\n", "").replace(
        "struct WorldSession {", decl + "\nstruct WorldSession {")
    program = program.replace("// NAME_STATUS", status_enum).replace("// CLASSIFIER", classifier)
    handler = extract((ROOT / "src/game/Handlers/CharacterHandler.cpp").read_text(encoding="utf-8"),
                      "void WorldSession::HandleCharRenameOpcode")
    return program + "\n" + body + "\n" + handler + "\n" + (main if main is not None else MAIN)


ENTRY_MAIN = r"""
static int failures = 0;
static void check(bool value, std::string const& label) {
    if (!value) { std::cerr << "FAIL " << label << "\n"; ++failures; }
}
static bool hasRenameRecord(uint32 guid, uint32 account, uint64 generation) {
    for (CharacterRecoveryRecord const& record : GetCharacterRecoveryRecords())
        if (record.kind == CharacterRecoveryKind::Rename &&
            std::find(record.guids.begin(), record.guids.end(), guid) != record.guids.end() &&
            std::find(record.accountIds.begin(), record.accountIds.end(), account) != record.accountIds.end() &&
            record.expectedGeneration == generation) return true;
    return false;
}
static bool admissible(uint32 account) {
    return GetAccountLifecycleRegistry().TryAcquire(
        {account}, AccountLifecycleKind::Mutation) != nullptr;
}
static std::unique_ptr<QueryResult> validResult(uint32 guid, char const* name, uint64 gen) {
    std::unique_ptr<QueryResult> result(new QueryResult());
    result->fields[0].guid = guid; result->fields[1].name = name;
    result->fields[2].generation = gen; return result;
}
int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";
    WorldSession session; session.accountId = 3010; session.guidNonce = 0x55;
    sWorld.session = &session;
    WorldPackets::Character::CharRename packet;
    packet.guid = ObjectGuid(HIGHGUID_PLAYER, 77);
    packet.newname = "aLICE sMITH";
    if (scenario == "entry_busy") {
        auto hold = GetAccountLifecycleRegistry().TryAcquire(
            {3010}, AccountLifecycleKind::Mutation);
        session.HandleCharRenameOpcode(packet);
        check(session.sent == 1 && session.lastResponse.result == CHAR_CREATE_ERROR &&
              !g_scheduled && !admissible(3010), "busy rejects/no schedule");
    } else if (scenario == "entry_invalid") {
        g_prepareOk = false;
        session.HandleCharRenameOpcode(packet);
        check(session.sent == 1 && !g_scheduled && admissible(3010),
              "invalid name rejects/no schedule");
    } else if (scenario == "entry_reserved") {
        g_reservedName = true;
        session.HandleCharRenameOpcode(packet);
        check(session.sent == 1 && session.lastResponse.result == CHAR_NAME_RESERVED &&
              !g_scheduled && admissible(3010), "reserved rejects/no schedule");
    } else if (scenario == "entry_schedule_failure") {
        g_scheduleOk = false;
        session.HandleCharRenameOpcode(packet);
        check(session.sent == 1 && admissible(3010), "schedule failure releases admission");
        g_scheduleOk = true;
    } else if (scenario == "entry_discard_sql") {
        session.HandleCharRenameOpcode(packet);
        check(g_scheduled && g_pendingRequest.guid == 77 &&
              g_pendingRequest.newName == "Alice Smith" &&
              g_pendingRequest.lease && g_pendingRequest.lease->IsActive() &&
              !admissible(3010), "SQL queue owns admission");
        g_pendingCallback = nullptr;
        g_pendingRequest = WorldSession::CharacterRenameRequest{};
        check(admissible(3010), "SQL discard releases admission");
        check(session.sent == 0 && sWorld.messager.message == nullptr,
              "SQL discard no response/world");
    } else if (scenario == "entry_discard_world") {
        session.HandleCharRenameOpcode(packet);
        auto callback = g_pendingCallback;
        auto request = g_pendingRequest;
        g_pendingCallback = nullptr;
        g_pendingRequest = WorldSession::CharacterRenameRequest{};
        callback(validResult(77, "Old Smith", 9), g_sqlAccount, g_sqlNonce, request);
        request = WorldSession::CharacterRenameRequest{};
        check(sWorld.messager.message != nullptr && !admissible(3010),
              "world queue owns admission");
        sWorld.messager.message = nullptr;
        check(admissible(3010) && session.sent == 0,
              "world discard releases admission/no response");
    } else if (scenario == "entry_success") {
        session.HandleCharRenameOpcode(packet);
        check(g_forRegion == 2 && g_forLocale == 3 &&
              g_pendingRequest.guid == 77 && g_pendingRequest.newName == "Alice Smith",
              "profile/canonical request captured");
        check(g_sqlAccount == 3010 && g_sqlNonce == 0x55 &&
              g_sqlArgs.size() == 6 && g_sqlArgs[0] == "77" &&
              g_sqlArgs[1] == "3010" &&
              g_sqlArgs[2] == std::to_string(CHARACTER_FLAG_RENAME) &&
              g_sqlArgs[3] == std::to_string(CHARACTER_FLAG_RENAME) &&
              g_sqlArgs[4] == "Alice Smith" &&
              g_sqlArgs[5] == "77", "SQL routing/identity/flags/exclusions captured");
        auto callback = g_pendingCallback;
        auto request = g_pendingRequest;
        g_pendingCallback = nullptr;
        g_pendingRequest = WorldSession::CharacterRenameRequest{};
        callback(validResult(77, "Old Smith", 9), g_sqlAccount, g_sqlNonce, request);
        request = WorldSession::CharacterRenameRequest{};
        check(sWorld.messager.message != nullptr && !admissible(3010),
              "world queue owns admission");
        sWorld.messager.message(&sWorld);
        check(session.sent == 1 && session.lastResponse.result == RESPONSE_SUCCESS &&
              session.lastResponse.renamedCharacterGuid == 77 &&
              session.lastResponse.newName == "Alice Smith" &&
              sWorld.invalidations == 1 && g_execCalls == 1 &&
              g_cachePublications == 1, "world continuation publishes success");
        check(g_appendOrder.size() >= 2 && g_appendOrder[0] == "character" &&
              g_appendOrder[1] == "ownership", "character then ownership append");
        std::vector<std::string> expectedChar = {
            "Alice Smith", "1", "2", "77", "3010", "Old Smith", "9",
            "1", "1", "77", "Old Smith"};
        check(g_charBindings == expectedChar, "exact character bindings");
        std::vector<std::string> expectedOwn = {"Alice Smith", "77", "Old Smith"};
        check(g_ownBindings == expectedOwn, "exact ownership bindings");
        check(g_execExcluded && g_cacheExcluded && g_responseExcluded,
              "admission held at execute/cache/response");
        std::vector<std::string> expectedEvents = {
            "execute", "cache", "response", "invalidate"};
        check(g_events == expectedEvents, "exact publication event order");
        check(!admissible(3010), "message closure still owns admission");
        sWorld.messager.message = nullptr;
        check(admissible(3010), "world completion releases admission");
    } else if (scenario == "entry_blocked") {
        BlockCharacterRecovery(CharacterRecoveryRecord{
            CharacterRecoveryKind::WholeSave, {}, {3010}, 0, "blocked"});
        session.HandleCharRenameOpcode(packet);
        check(session.sent == 1 && !g_scheduled && g_appendCalls == 0,
              "blocked no scheduling/writes");
    } else if (scenario == "entry_bad_state") {
        g_isPlayer = false;
        session.HandleCharRenameOpcode(packet);
        check(session.sent == 1 && !g_scheduled, "non-player rejected");
        g_isPlayer = true;
        session.player = true;
        session.HandleCharRenameOpcode(packet);
        check(session.sent == 2 && !g_scheduled && admissible(3010), "online rejected");
        session.player = false; session.loading = true;
        session.HandleCharRenameOpcode(packet);
        check(session.sent == 3 && !g_scheduled, "loading rejected");
    } else if (scenario == "entry_state_changes") {
        session.HandleCharRenameOpcode(packet);
        auto callback = g_pendingCallback; auto request = g_pendingRequest;
        g_pendingCallback = nullptr; g_pendingRequest = WorldSession::CharacterRenameRequest{};
        callback(validResult(77, "Old Smith", 9), g_sqlAccount, g_sqlNonce, request);
        request = WorldSession::CharacterRenameRequest{};
        session.player = true;
        sWorld.messager.message(&sWorld);
        check(session.sent == 1 && session.lastResponse.result == CHAR_CREATE_ERROR &&
              g_appendCalls == 0 && g_execCalls == 0 &&
              g_cachePublications == 0 && sWorld.invalidations == 0,
              "state change rejects before persistence");
        sWorld.messager.message = nullptr;
        check(admissible(3010), "state change releases admission");
    } else if (scenario == "entry_replaced" || scenario == "entry_missing") {
        session.HandleCharRenameOpcode(packet);
        auto callback = g_pendingCallback; auto request = g_pendingRequest;
        g_pendingCallback = nullptr; g_pendingRequest = WorldSession::CharacterRenameRequest{};
        callback(validResult(77, "Old Smith", 9), g_sqlAccount, g_sqlNonce, request);
        request = WorldSession::CharacterRenameRequest{};
        if (scenario == "entry_replaced") session.guidNonce = 0x99;
        else sWorld.session = nullptr;
        sWorld.messager.message(&sWorld);
        check(session.sent == 0 && g_appendCalls == 0 && g_execCalls == 0,
              "session replacement/missing no persistence");
        sWorld.messager.message = nullptr;
        sWorld.session = &session;
        check(admissible(3010), "replacement/missing releases admission");
    } else if (scenario == "entry_invalid_result") {
        int handled = 0, writesOk = 0, releaseOk = 0, writesMask = 0;
        for (int mode = 0; mode < 8; ++mode) {
            session.sent = 0; g_appendCalls = 0; g_execCalls = 0;
            g_cachePublications = 0; sWorld.invalidations = 0;
            session.HandleCharRenameOpcode(packet);
            auto callback = g_pendingCallback; auto request = g_pendingRequest;
            g_pendingCallback = nullptr; g_pendingRequest = WorldSession::CharacterRenameRequest{};
            std::unique_ptr<QueryResult> result;
            if (mode != 0) {
                result = validResult(mode == 1 ? 78 : 77, "Old Smith",
                    mode == 3 ? 0 : (mode == 4 ? std::numeric_limits<uint64>::max() : 9));
            }
            g_rowCount = mode == 5 ? 0 : (mode == 6 ? 2 : 1);
            g_fieldCount = mode == 7 ? 2 : 3;
            g_hasRow = mode != 2;
            g_fetchCalls = 0;
            callback(std::move(result), g_sqlAccount, g_sqlNonce, request);
            request = WorldSession::CharacterRenameRequest{};
            sWorld.messager.message(&sWorld);
            if (session.sent >= 1 && session.lastResponse.result == CHAR_CREATE_ERROR &&
                g_appendCalls == 0 && g_execCalls == 0 &&
                g_cachePublications == 0 && sWorld.invalidations == 0)
                { ++writesOk; writesMask |= (1 << mode); }
            if (mode == 2)
                check(g_fetchCalls == 1, "missing-row reaches Fetch once");
            if (mode == 7)
                check(g_fetchCalls == 0, "field-count failure skips Fetch");
            sWorld.messager.message = nullptr;
            if (admissible(3010)) ++releaseOk;
            g_events.clear();
        }
        check(writesOk == 8 && releaseOk == 8, "eight malformed result subcases writes/release=" + std::to_string(writesOk) + "/" + std::to_string(releaseOk) + " mask=" + std::to_string(writesMask));
    } else if (scenario == "entry_same_name") {
        session.HandleCharRenameOpcode(packet);
        auto callback = g_pendingCallback; auto request = g_pendingRequest;
        g_pendingCallback = nullptr; g_pendingRequest = WorldSession::CharacterRenameRequest{};
        callback(validResult(77, "Alice Smith", 9), g_sqlAccount, g_sqlNonce, request);
        request = WorldSession::CharacterRenameRequest{};
        sWorld.messager.message(&sWorld);
        check(session.lastResponse.result == RESPONSE_SUCCESS &&
              g_charAppends == 1 && g_ownAppends == 0 && g_execCalls == 1 &&
              g_cachePublications == 1, "same-name one update/no ownership");
        sWorld.messager.message = nullptr;
    } else if (scenario == "entry_ownership_append_failure") {
        g_ownAppendOk = false;
        session.HandleCharRenameOpcode(packet);
        auto callback = g_pendingCallback; auto request = g_pendingRequest;
        g_pendingCallback = nullptr; g_pendingRequest = WorldSession::CharacterRenameRequest{};
        callback(validResult(77, "Old Smith", 9), g_sqlAccount, g_sqlNonce, request);
        request = WorldSession::CharacterRenameRequest{};
        sWorld.messager.message(&sWorld);
        check(session.lastResponse.result == CHAR_CREATE_ERROR && g_execCalls == 0 &&
              g_cachePublications == 0 && sWorld.invalidations == 0,
              "ownership append failure no execute/publication");
        sWorld.messager.message = nullptr;
        check(admissible(3010), "ownership append failure releases admission");
    } else if (scenario == "entry_duplicate") {
        g_rename_result.outcome = SqlWriteOutcome::DuplicateKey;
        g_rename_result.stage = SqlBatchStage::Statement;
        g_rename_result.statementIndex = 1;
        g_rename_result.cause.outcome = SqlWriteOutcome::DuplicateKey;
        g_rename_result.rollbackAttempted = true;
        g_rename_result.rollbackResult.outcome = SqlWriteOutcome::Succeeded;
        session.HandleCharRenameOpcode(packet);
        auto callback = g_pendingCallback; auto request = g_pendingRequest;
        g_pendingCallback = nullptr; g_pendingRequest = WorldSession::CharacterRenameRequest{};
        callback(validResult(77, "Old Smith", 9), g_sqlAccount, g_sqlNonce, request);
        request = WorldSession::CharacterRenameRequest{};
        sWorld.messager.message(&sWorld);
        check(session.lastResponse.result == CHAR_CREATE_NAME_IN_USE &&
              g_cachePublications == 0 && sWorld.invalidations == 0 &&
              !hasRenameRecord(77, 3010, 9), "duplicate name-in-use/no recovery");
        sWorld.messager.message = nullptr;
        check(admissible(3010), "duplicate releases admission");
    } else if (scenario == "entry_loading_change") {
        session.HandleCharRenameOpcode(packet);
        auto callback = g_pendingCallback; auto request = g_pendingRequest;
        g_pendingCallback = nullptr; g_pendingRequest = WorldSession::CharacterRenameRequest{};
        callback(validResult(77, "Old Smith", 9), g_sqlAccount, g_sqlNonce, request);
        request = WorldSession::CharacterRenameRequest{};
        session.loading = true;
        sWorld.messager.message(&sWorld);
        check(session.sent == 1 && session.lastResponse.result == CHAR_CREATE_ERROR &&
              g_appendCalls == 0 && g_execCalls == 0 &&
              g_cachePublications == 0 && sWorld.invalidations == 0,
              "loading change rejects before persistence");
        sWorld.messager.message = nullptr;
        check(admissible(3010), "loading change releases admission");
    } else { std::cerr << "unknown entry scenario\n"; return 2; }
    if (failures) { std::cerr << "failures=" << failures << "\n"; return 1; }
    std::cout << "entry " << scenario << ": PASS\n"; return 0;
}
"""

SCENARIOS = [
    "cache_failure_block", "success", "fallback_success", "replacement_session",
    "missing_session", "known_rollback", "unknown_commit", "pending_destroy",
]


@unittest.skipUnless(shutil.which("cmake"), "cmake not available")
class AsyncIdentityAdmissionExecutionTests(unittest.TestCase):
    def test_actual_rename_callback_real_registry(self):
        source = HANDLER.read_text(encoding="utf-8")
        body = extract(source, "void WorldSession::HandleChangePlayerNameOpcodeCallBack")
        program = generated_program(body)
        with tempfile.TemporaryDirectory(prefix="fn05h-v32-rename-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(rename_callback_exec CXX)\n"
                "set(CMAKE_CXX_STANDARD 14)\n"
                f"add_executable(rename probe.cpp \"{RECOVERY.as_posix()}\")\n"
                f"target_include_directories(rename PRIVATE "
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
            executable = build / "Release" / "rename.exe"
            if not executable.exists():
                executable = build / "rename"
            for scenario in SCENARIOS:
                result = subprocess.run([str(executable), scenario],
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0,
                                 scenario + "\n" + result.stdout + result.stderr)
                self.assertIn("rename scenario " + scenario + ": PASS", result.stdout)


    def test_cache_recovery_variant_is_sensitive(self):
        source = HANDLER.read_text(encoding="utf-8")
        body = extract(source, "void WorldSession::HandleChangePlayerNameOpcodeCallBack")
        old = ("                    BlockCharacterRecovery(CharacterRecoveryRecord{\n"
               "                        CharacterRecoveryKind::Rename, {guidLow}, {accountId},\n"
               "                        generation,\n"
               "                        \"rename committed but cache publication failed\"});\n")
        self.assertIn(old, body)
        variant = body.replace(old, "", 1)
        program = generated_program(variant)
        with tempfile.TemporaryDirectory(prefix="fn05h-v32-variant-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(rename_variant_exec CXX)\n"
                "set(CMAKE_CXX_STANDARD 14)\n"
                f"add_executable(rename probe.cpp \"{RECOVERY.as_posix()}\")\n"
                f"target_include_directories(rename PRIVATE "
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
            executable = build / "Release" / "rename.exe"
            if not executable.exists():
                executable = build / "rename"
            result = subprocess.run([str(executable), "cache_failure_block"],
                                    text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0,
                                "cache-recovery variant unexpectedly passed: " +
                                result.stdout + result.stderr)
            self.assertIn("FAIL rename recovery record",
                          result.stdout + result.stderr)


    def test_actual_rename_entry_queues(self):
        handler = HANDLER.read_text(encoding="utf-8")
        callback = extract(handler, "void WorldSession::HandleChangePlayerNameOpcodeCallBack")
        callback = callback.replace(
            "void WorldSession::HandleChangePlayerNameOpcodeCallBack",
            "void WorldSession::HandleChangePlayerNameOpcodeCallBack", 1)
        program = generated_program(callback, ENTRY_MAIN)
        with tempfile.TemporaryDirectory(prefix="fn05h-v56-entry-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(rename_entry CXX)\n"
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
            for scenario in ["entry_busy", "entry_invalid", "entry_reserved",
                             "entry_schedule_failure", "entry_discard_sql",
                             "entry_discard_world", "entry_success", "entry_blocked",
                             "entry_bad_state", "entry_state_changes", "entry_loading_change", "entry_replaced",
                             "entry_missing", "entry_invalid_result", "entry_same_name",
                             "entry_ownership_append_failure", "entry_duplicate"]:
                result = subprocess.run([str(executable), scenario], text=True,
                                        capture_output=True, timeout=30)
                if result.returncode != 0 or (
                        "entry " + scenario + ": PASS") not in result.stdout:
                    failures.append(scenario + "\n" + result.stdout + result.stderr)
            self.assertFalse(failures, "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
