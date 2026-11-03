#!/usr/bin/env python3
"""Actual PlayerDumpReader::LoadDump with production-like query semantics."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CPP = ROOT / "src/game/PlayerDump.cpp"
HEADER = ROOT / "src/game/PlayerDump.h"
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
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <map>
#include <memory>
#include <string>
#include <vector>

using uint32 = unsigned int;
using uint64 = unsigned long long;
using uint8 = unsigned char;
using int32 = int;

// DUMP_ENUM
// DUMP_TABLES

enum HighGuid { HIGHGUID_PLAYER = 1 };
struct ObjectGuid { static uint32 GetMaxCounter(HighGuid) { return 0xFFFFFFFFu; } };
struct PlayerDumpNamePolicy { uint8 region; std::size_t locale; bool allowReserved; };

struct Field {
    std::string value;
    bool null = false;
    bool IsNULL() const { return null; }
    std::string GetCppString() const { return null ? "" : value; }
    uint32 GetUInt32() const { return static_cast<uint32>(std::strtoul(value.c_str(), nullptr, 10)); }
};
struct QueryResult {
    std::vector<std::vector<Field>> rows;
    std::size_t index = 0;
    Field* Fetch() const { return rows.empty() ? nullptr : const_cast<Field*>(rows[index].data()); }
    bool NextRow() { if (index + 1 >= rows.size()) return false; ++index; return true; }
    uint32 GetFieldCount() const { return rows.empty() ? 0 : static_cast<uint32>(rows[0].size()); }
    uint64 GetRowCount() const { return rows.size(); }
};
struct Log { template<typename... Args> void Out(Args&&...) {} } sLog;

uint32 g_importAccount = 7010;
std::vector<std::string> g_schema{"guid","account","name","online",
    "deleted_account","deleted_name","deleted_time","identity_generation"};
bool g_accountExists = true, g_guidExists = false;
int g_accountRows = 1, g_accountFields = 1;
std::string g_accountValue = "1"; bool g_accountNull = false;
bool g_reserved = false, g_nameExists = false, g_cacheOk = true, g_countPubOk = true;
bool g_claimOk = true, g_textOk = true, g_commitOk = true, g_beginOk = true;
bool g_collisionQueryOk = true;
bool g_accountQueryOk = true, g_guidQueryOk = true;
bool g_ownershipQueryOk = true, g_nameQueryOk = true, g_firstOwnershipQueryOk = true;
bool g_schemaNull = false;
bool g_canonicalizeAlice = false, g_rejectFixtureName = false;
bool g_execOk = true, g_countOk = true, g_inTransaction = false;
int g_countCalls = 0, g_guidCalls = 0, g_claimCalls = 0, g_cacheCalls = 0, g_countPubCalls = 0;
int g_textCalls = 0, g_highCalls = 0, g_charSetCalls = 0, g_ownershipQueries = 0;
int g_openCalls = 0, g_closeCalls = 0, g_doubleClose = 0;
void* g_openHandle = nullptr;
std::vector<std::string> g_queryLog;
std::string g_rawCharacterSql;
bool g_writeInTxn = false, g_claimInTxn = false;
uint32 g_cacheGuid = 0;
bool g_commitObserved = false, g_textObserved = false, g_highObserved = false;
std::vector<std::string> g_events;
std::string g_lastCharacterWrite;

bool importAdmissible() {
    return GetAccountLifecycleRegistry().TryAcquire(
        {g_importAccount}, AccountLifecycleKind::Mutation) != nullptr;
}

struct GuidAllocator {
    bool isCharacter = false;
    uint32 next = 1000;
    uint32 GetNextAfterMaxUsed() const { return next; }
    void SetMaxUsedGuid(uint32 value, char const*) {
        next = value + 1; g_events.push_back("highwater"); ++g_highCalls;
        if (isCharacter) { ++g_charSetCalls; g_events.push_back("character_highwater"); }
        g_highObserved = g_highObserved || !importAdmissible();
    }
};
struct PlayerNameLists {};
PlayerNameLists const& GetPlayerNameLists() { static PlayerNameLists lists; return lists; }
struct PlayerNameCharsets { static PlayerNameCharsets ForClient(uint8, std::size_t) { return {}; } };
enum { CHAR_NAME_SUCCESS = 0 };
uint8 prepareFullPlayerName(std::string& name, PlayerNameLists const&, PlayerNameCharsets const&) {
    if (g_rejectFixtureName) return 1;
    if (g_canonicalizeAlice && name == "aLICE sMITH") name = "Alice Smith";
    return 0;
}

struct ObjectMgr {
    GuidAllocator m_CharGuids, m_ItemGuids, m_MailIds, m_ItemTextIds;
    ObjectMgr() { m_CharGuids.isCharacter = true; }
    uint32 GeneratePetNumber() { return 500; }
    uint32 GeneratePlayerLowGuid() { ++g_guidCalls; return 4242; }
    bool IsReservedName(std::string const&) { return g_reserved; }
    bool LoadPlayerCacheData(uint32 guid) { ++g_cacheCalls; g_cacheGuid = guid; return g_cacheOk; }
    void AddItemText(uint32, std::string const&) {
        g_events.push_back("text"); ++g_textCalls;
        g_textObserved = g_textObserved || !importAdmissible();
    }
};
ObjectMgr sObjectMgr;

struct AccountMgr {
    bool TryGetCharactersCount(uint32, uint32& count) {
        ++g_countCalls; if (!g_countOk) return false; count = 0; return true;
    }
};
AccountMgr sAccountMgr;
struct World {
    bool UpdateRealmCharCount(uint32, AccountLifecycleLease const*, uint32* = nullptr) {
        ++g_countPubCalls; g_commitObserved = g_commitObserved || !importAdmissible();
        return g_countPubOk;
    }
} sWorld;

struct SqlStatementID { int id = 1; };
struct SqlStatement {
    std::string text;
    std::string name;
    uint32 guid = 0;
    void addString(std::string const& value) { name = value; }
    void addString(char const* value) { name = value; }
    void addUInt32(uint32 value) { guid = value; }
    bool Execute() {
        ++g_claimCalls; g_events.push_back("claim"); g_claimInTxn = g_inTransaction;
        g_lastCharacterWrite += "|claim:" + name + ":" + std::to_string(guid);
        return g_claimOk;
    }
};

struct Database {
    bool loginDatabase = false;
    explicit Database(bool login = false) : loginDatabase(login) {}
    std::unique_ptr<QueryResult> MakeAccount() {
        std::vector<std::vector<Field>> rows;
        for (int i = 0; i < g_accountRows; ++i) {
            std::vector<Field> row;
            for (int j = 0; j < g_accountFields; ++j)
                row.push_back(Field{j == 0 ? g_accountValue : "99", g_accountNull});
            rows.push_back(row);
        }
        return Make(std::move(rows));
    }
    std::unique_ptr<QueryResult> Make(std::vector<std::vector<Field>> rows) {
        auto result = std::make_unique<QueryResult>(); result->rows = std::move(rows); return result;
    }
    std::unique_ptr<QueryResult> PQuery(char const* fmt, ...) {
        std::string query = fmt;
        if (query.find("FROM `account`") != std::string::npos) {
            g_queryLog.push_back("account");
            return loginDatabase && g_accountQueryOk ? MakeAccount() : nullptr;
        }
        if (loginDatabase)
            return nullptr;
        if (query.find("SHOW COLUMNS") != std::string::npos) {
            g_queryLog.push_back("schema");
            std::vector<std::vector<Field>> rows;
            for (auto const& column : g_schema)
                rows.push_back({Field{column, g_schemaNull}});
            return Make(std::move(rows));
        }
        if (query.find("character_name_ownership") != std::string::npos) {
            g_queryLog.push_back("ownership");
            ++g_ownershipQueries;
            bool ok = g_ownershipQueries == 1
                ? g_firstOwnershipQueryOk : g_ownershipQueryOk;
            return ok && g_collisionQueryOk
                ? Make({{Field{g_nameExists ? "1" : "0", false}}}) : nullptr;
        }
        if (query.find("BINARY `name`") != std::string::npos) {
            g_queryLog.push_back("name");
            return g_nameQueryOk && g_collisionQueryOk
                ? Make({{Field{g_nameExists ? "1" : "0", false}}}) : nullptr;
        }
        if (query.find("COUNT(*) FROM `characters`") != std::string::npos) {
            g_queryLog.push_back("guid");
            return g_guidQueryOk
                ? Make({{Field{g_guidExists ? "1" : "0", false}}}) : nullptr;
        }
        return nullptr;
    }
    void escape_string(std::string&) {}
    SqlStatement CreateStatement(SqlStatementID&, char const* fmt) {
        SqlStatement statement; statement.text = fmt; return statement;
    }
    bool BeginTransaction() {
        g_events.push_back("begin");
        if (!g_beginOk || g_inTransaction) return false;
        g_inTransaction = true; return true;
    }
    bool InTransaction() { return g_inTransaction; }
    bool Execute(char const* sql) {
        g_events.push_back("write"); g_writeInTxn = g_inTransaction;
        std::string text = sql;
        if (text.find("INSERT INTO `characters`") != std::string::npos) {
            g_lastCharacterWrite = text;
            g_rawCharacterSql = text;
        }
        return g_execOk;
    }
    bool RollbackTransaction() {
        g_events.push_back("rollback"); g_inTransaction = false; return true;
    }
    bool CommitTransactionDirect() {
        g_events.push_back("commit");
        g_commitObserved = g_commitObserved || !importAdmissible();
        g_inTransaction = false; return g_commitOk;
    }
};
Database CharacterDatabase(false);
Database LoginDatabase(true);
enum { LOG_BASIC = 0, LOG_LVL_ERROR = 0 };
static FILE* TrackOpen(char const* path, char const* mode) {
    FILE* handle = std::fopen(path, mode);
    if (handle) { ++g_openCalls; g_openHandle = handle; }
    return handle;
}
static int TrackClose(FILE* handle) {
    if (handle == g_openHandle) { ++g_closeCalls; g_openHandle = nullptr; }
    else if (handle) ++g_doubleClose;
    return std::fclose(handle);
}
#define fopen TrackOpen
#define fclose TrackClose
#define MANGOS_ASSERT(x) do { if (!(x)) std::abort(); } while (0)
#define ROLLBACK(DR) {CharacterDatabase.RollbackTransaction(); fclose(fin); return (DR);}

class PlayerDumpReader {
    public:
        DumpReturn LoadDump(std::string const& file, uint32 account,
                            std::string name, uint32 guid,
                            PlayerDumpNamePolicy const& policy);
};
'''

MAIN = r'''
static int failures = 0;
static void check(bool value, std::string const& label) {
    if (!value) { std::cerr << "FAIL " << label << "\n"; ++failures; }
}
static bool admissible(uint32 account) {
    return GetAccountLifecycleRegistry().TryAcquire(
        {account}, AccountLifecycleKind::Mutation) != nullptr;
}
static void checkOwnedRejection(DumpReturn actual, DumpReturn expected,
                                int expectedWrites = 0, int expectedClaims = 0) {
    check(actual == expected, "owned rejection status");
    check(std::count(g_events.begin(), g_events.end(), "begin") == 1,
          "owned rejection one begin");
    check(std::count(g_events.begin(), g_events.end(), "rollback") == 1,
          "owned rejection one rollback");
    check(std::count(g_events.begin(), g_events.end(), "commit") == 0,
          "owned rejection no commit");
    check(std::count(g_events.begin(), g_events.end(), "write") == expectedWrites,
          "owned rejection expected writes");
    check(g_claimCalls == expectedClaims, "owned rejection expected claims");
    check(g_textCalls == 0 && g_highCalls == 0 && g_charSetCalls == 0 &&
          g_cacheCalls == 0 && g_countPubCalls == 0,
          "owned rejection no publication");
    check(!g_inTransaction, "owned rejection transaction inactive");
    check(admissible(g_importAccount), "owned rejection admission released");
    check(g_openCalls == 1 && g_closeCalls == 1 && g_openHandle == nullptr &&
          g_doubleClose == 0, "owned rejection file closed once");
}

static bool eventBefore(std::string const& a, std::string const& b) {
    auto ia = std::find(g_events.begin(), g_events.end(), a);
    auto ib = std::find(g_events.begin(), g_events.end(), b);
    return ia != g_events.end() && ib != g_events.end() && ia < ib;
}
int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";
    std::string file = argc > 2 ? argv[2] : "";
    const uint32 account = 7010;
    PlayerDumpNamePolicy policy{0, 0, false};
    DumpReturn result = DUMP_SUCCESS;

    if (scenario == "blocked") {
        BlockCharacterRecovery(CharacterRecoveryRecord{
            CharacterRecoveryKind::WholeSave, {}, {account}, 0, "blocked"});
        result = PlayerDumpReader().LoadDump(file, account, "", 0, policy);
        check(result == DUMP_DATABASE_ERROR && g_events.empty() && g_countCalls == 0,
              "blocked no count/file/txn");
        check(!admissible(account), "blocked still blocked");
    } else if (scenario == "count_failure") {
        g_countOk = false;
        result = PlayerDumpReader().LoadDump(file, account, "", 0, policy);
        check(result == DUMP_DATABASE_ERROR && g_events.empty() && g_countCalls == 1,
              "count failure no begin");
    } else if (scenario == "busy") {
        auto hold = GetAccountLifecycleRegistry().TryAcquire(
            {account}, AccountLifecycleKind::Mutation);
        result = PlayerDumpReader().LoadDump(file, account, "", 0, policy);
        check(result == DUMP_DATABASE_ERROR && g_countCalls == 0 && g_events.empty(),
              "busy no count/file/txn");
        hold.reset();
        check(admissible(account), "busy lease released");
    } else if (scenario == "ambient") {
        g_inTransaction = true;
        result = PlayerDumpReader().LoadDump(file, account, "", 0, policy);
        check(result == DUMP_DATABASE_ERROR && g_events.empty() && g_inTransaction,
              "ambient untouched");
        g_inTransaction = false;
    } else if (scenario == "missing_account") {
        g_accountExists = false; g_accountValue = "0";
        result = PlayerDumpReader().LoadDump(file, account, "", 0, policy);
        check(result == DUMP_IDENTITY_ERROR &&
              g_events == std::vector<std::string>{"begin", "rollback"},
              "missing account rejects/cleans");
        checkOwnedRejection(result, DUMP_IDENTITY_ERROR);
        check(admissible(account), "missing account releases admission");
    } else if (scenario == "explicit_guid") {
        g_guidExists = true;
        result = PlayerDumpReader().LoadDump(file, account, "", 55, policy);
        check(result == DUMP_IDENTITY_ERROR &&
              g_events == std::vector<std::string>{"begin", "rollback"},
              "existing explicit guid rejects");
        checkOwnedRejection(result, DUMP_IDENTITY_ERROR);
    } else if (scenario == "malformed") {
        result = PlayerDumpReader().LoadDump(file, account, "", 0, policy);
        check(result == DUMP_FILE_BROKEN && g_claimCalls == 0 &&
              std::find(g_events.begin(), g_events.end(), "rollback") != g_events.end(),
              "malformed rollback no claim");
        checkOwnedRejection(result, DUMP_FILE_BROKEN);
    } else if (scenario == "commit_false") {
        g_commitOk = false;
        result = PlayerDumpReader().LoadDump(file, account, "", 0, policy);
        check(result == DUMP_DATABASE_ERROR, "commit false error result=" + std::to_string(result) + " events=" + std::to_string(g_events.size()) + " claims=" + std::to_string(g_claimCalls));
        check(g_claimCalls == 1, "commit false claim once");
        check(g_textCalls == 0 && g_highCalls == 0, "commit false no publication");
        check(g_commitObserved, "commit false observed exclusion");
        check(IsAccountRecoveryBlocked(account), "commit false recovery record");
    } else if (scenario == "claim_failure") {
        g_claimOk = false;
        result = PlayerDumpReader().LoadDump(file, account, "", 0, policy);
        check(result == DUMP_IDENTITY_ERROR &&
              std::find(g_events.begin(), g_events.end(), "rollback") != g_events.end() &&
              g_textCalls == 0, "claim failure rollback/no publication");
        checkOwnedRejection(result, DUMP_IDENTITY_ERROR, 1, 1);
    } else if (scenario == "cache_failure") {
        g_cacheOk = false;
        result = PlayerDumpReader().LoadDump(file, account, "", 0, policy);
        check(result == DUMP_DATABASE_ERROR && g_cacheCalls == 1 &&
              IsAccountRecoveryBlocked(account), "cache failure recovery");
    } else if (scenario == "count_publication_failure") {
        g_countPubOk = false;
        result = PlayerDumpReader().LoadDump(file, account, "", 0, policy);
        bool found = false;
        for (auto const& record : GetCharacterRecoveryRecords())
            if (record.kind == CharacterRecoveryKind::WholeSave &&
                record.guids.size() == 1 && record.guids[0] == 4242 &&
                record.accountIds.size() == 1 && record.accountIds[0] == account &&
                record.expectedGeneration == 1)
                found = true;
        check(result == DUMP_DATABASE_ERROR && g_countPubCalls == 1 && found,
              "count publication exact recovery");
        check(std::count(g_events.begin(), g_events.end(), "commit") == 1 &&
              std::find(g_events.begin(), g_events.end(), "rollback") == g_events.end(),
              "count publication no retry/rollback after commit");
    } else if (scenario == "count_shape") {
        uint64 output = 77;
        check(!ReadSingleDumpCount(nullptr, output) && output == 77, "null count");
        check(!ReadSingleDumpCount(CharacterDatabase.Make({}).get(), output) && output == 77, "no rows count");
        check(!ReadSingleDumpCount(CharacterDatabase.Make({{Field{"1", false}, Field{"2", false}}}).get(), output) && output == 77, "two fields count");
        check(!ReadSingleDumpCount(CharacterDatabase.Make({{Field{"1", true}}}).get(), output) && output == 77, "null value count");
        check(!ReadSingleDumpCount(CharacterDatabase.Make({{Field{"", false}}}).get(), output) && output == 77, "empty value count");
        check(!ReadSingleDumpCount(CharacterDatabase.Make({{Field{"-1", false}}}).get(), output) && output == 77, "signed count");
        check(!ReadSingleDumpCount(CharacterDatabase.Make({{Field{"abc", false}}}).get(), output) && output == 77, "nondecimal count");
        check(!ReadSingleDumpCount(CharacterDatabase.Make({{Field{"18446744073709551616", false}}}).get(), output) && output == 77, "overflow count");
        check(!ReadSingleDumpCount(CharacterDatabase.Make({{Field{"1", false}}, {Field{"2", false}}}).get(), output) && output == 77, "multiple rows count");
        check(ReadSingleDumpCount(CharacterDatabase.Make({{Field{"0", false}}}).get(), output) && output == 0, "valid zero count");
        check(ReadSingleDumpCount(CharacterDatabase.Make({{Field{"18446744073709551615", false}}}).get(), output) && output == UINT64_MAX, "valid max count");
        result = DUMP_SUCCESS;
    } else if (scenario == "high_guid") {
        result = PlayerDumpReader().LoadDump(file, account, "", 2147483648u, policy);
        check(result == DUMP_SUCCESS, "high guid success");
        check(g_lastCharacterWrite.find("2147483648") != std::string::npos &&
              g_lastCharacterWrite.find("|claim:Alice Smith:2147483648") != std::string::npos,
              "high guid written and claimed");
        check(g_cacheGuid == 2147483648u, "high guid cache exact");
    } else if (scenario == "collision_failure") {
        g_collisionQueryOk = false;
        result = PlayerDumpReader().LoadDump(file, account, "", 0, policy);
        check(result == DUMP_IDENTITY_ERROR &&
              std::find(g_events.begin(), g_events.end(), "rollback") != g_events.end() &&
              g_claimCalls == 0, "collision failure rollback no claim");
        checkOwnedRejection(result, DUMP_IDENTITY_ERROR);
    } else if (scenario == "explicit_below") {
        sObjectMgr.m_CharGuids.next = 1000;
        result = PlayerDumpReader().LoadDump(file, account, "", 999, policy);
        check(result == DUMP_SUCCESS && g_charSetCalls == 0 &&
              sObjectMgr.m_CharGuids.GetNextAfterMaxUsed() == 1000,
              "explicit below no setter/no lowering");
    } else if (scenario == "explicit_at") {
        sObjectMgr.m_CharGuids.next = 1000;
        result = PlayerDumpReader().LoadDump(file, account, "", 1000, policy);
        check(result == DUMP_SUCCESS && g_charSetCalls == 1 &&
              sObjectMgr.m_CharGuids.GetNextAfterMaxUsed() == 1001 &&
              eventBefore("commit", "character_highwater"),
              "explicit at next sets guid+1 after commit");
    } else if (scenario == "explicit_above") {
        sObjectMgr.m_CharGuids.next = 1000;
        result = PlayerDumpReader().LoadDump(file, account, "", 1001, policy);
        check(result == DUMP_SUCCESS && g_charSetCalls == 1 &&
              sObjectMgr.m_CharGuids.GetNextAfterMaxUsed() == 1002 &&
              eventBefore("commit", "character_highwater"),
              "explicit above sets guid+1 after commit");
    } else if (scenario == "explicit_upper") {
        result = PlayerDumpReader().LoadDump(file, account, "", 0xFFFFFFFEu, policy);
        check(result == DUMP_IDENTITY_ERROR && g_events.empty() &&
              g_claimCalls == 0 && g_charSetCalls == 0,
              "upper boundary before effects");
    } else if (scenario == "high_cache_failure") {
        sObjectMgr.m_CharGuids.next = 1000;
        g_cacheOk = false;
        result = PlayerDumpReader().LoadDump(file, account, "", 4294967292u, policy);
        check(result == DUMP_DATABASE_ERROR, "high cache failure error");
        bool found = false;
        for (auto const& record : GetCharacterRecoveryRecords())
            if (record.kind == CharacterRecoveryKind::WholeSave &&
                record.guids.size() == 1 && record.guids[0] == 4294967292u &&
                record.accountIds.size() == 1 && record.accountIds[0] == account &&
                record.expectedGeneration == 1)
                found = true;
        check(found, "high cache failure exact recovery");
    } else if (scenario == "override_different") {
        result = PlayerDumpReader().LoadDump(file, account, "Alice Smith", 0, policy);
        check(result == DUMP_SUCCESS &&
              g_lastCharacterWrite.find("Alice Smith") != std::string::npos &&
              g_lastCharacterWrite.find("Bob Jones") == std::string::npos &&
              g_lastCharacterWrite.find("|claim:Alice Smith:") != std::string::npos,
              "different override reaches write and claim");
    } else if (scenario == "account_two" || scenario == "account_malformed" ||
               scenario == "account_null") {
        if (scenario == "account_two") g_accountValue = "2";
        else if (scenario == "account_malformed") g_accountValue = "abc";
        else g_accountQueryOk = false;
        result = PlayerDumpReader().LoadDump(file, account, "", 0, policy);
        if (scenario == "account_null")
            check(std::count(g_queryLog.begin(), g_queryLog.end(), "account") == 1 &&
                  std::find(g_queryLog.begin(), g_queryLog.end(), "schema") == g_queryLog.end(),
                  "account null reached only account query");
        check(result == DUMP_IDENTITY_ERROR && g_claimCalls == 0 &&
              g_textCalls == 0 && g_highCalls == 0 && !g_inTransaction &&
              admissible(account), "account rejection clean/released");
        checkOwnedRejection(result, DUMP_IDENTITY_ERROR);
        check(g_openCalls == 1 && g_closeCalls == 1 && g_doubleClose == 0,
              "account rejection closes file once");
    } else if (scenario == "guid_query_failure") {
        g_guidQueryOk = false;
        result = PlayerDumpReader().LoadDump(file, account, "", 55, policy);
        check(result == DUMP_IDENTITY_ERROR && g_claimCalls == 0 &&
              std::find(g_events.begin(), g_events.end(), "rollback") != g_events.end(),
              "guid query failure rollback/no write");
        checkOwnedRejection(result, DUMP_IDENTITY_ERROR);
    } else if (scenario == "override_ownership_failure") {
        g_firstOwnershipQueryOk = false;
        result = PlayerDumpReader().LoadDump(file, account, "Alice Smith", 0, policy);
        check(result == DUMP_IDENTITY_ERROR && g_ownershipQueries == 1 &&
              g_claimCalls == 0, "override ownership failure before row");
        checkOwnedRejection(result, DUMP_IDENTITY_ERROR);
    } else if (scenario == "row_ownership_failure") {
        g_ownershipQueryOk = false;
        result = PlayerDumpReader().LoadDump(file, account, "Alice Smith", 0, policy);
        check(result == DUMP_IDENTITY_ERROR && g_ownershipQueries == 2 &&
              g_claimCalls == 0, "row ownership failure after precheck");
        checkOwnedRejection(result, DUMP_IDENTITY_ERROR);
    } else if (scenario == "name_query_failure") {
        g_nameQueryOk = false;
        result = PlayerDumpReader().LoadDump(file, account, "Alice Smith", 0, policy);
        check(result == DUMP_IDENTITY_ERROR && g_ownershipQueries == 2 &&
              std::find(g_queryLog.begin(), g_queryLog.end(), "name") != g_queryLog.end() &&
              g_claimCalls == 0, "name query reached then failed");
        checkOwnedRejection(result, DUMP_IDENTITY_ERROR);
    } else if (scenario == "schema_empty" || scenario == "schema_null" ||
               scenario == "schema_duplicate" || scenario == "schema_missing") {
        if (scenario == "schema_empty") g_schema.clear();
        else if (scenario == "schema_null") g_schemaNull = true;
        else if (scenario == "schema_duplicate") g_schema.push_back("guid");
        else g_schema.pop_back();
        result = PlayerDumpReader().LoadDump(file, account, "", 0, policy);
        check(result == DUMP_IDENTITY_ERROR && g_claimCalls == 0 &&
              g_textCalls == 0 && g_highCalls == 0 && !g_inTransaction,
              "schema rejection before writes");
        checkOwnedRejection(result, DUMP_IDENTITY_ERROR);
    } else if (scenario == "column_list" || scenario == "bad_prefix" ||
               scenario == "trailing_sql" || scenario == "dependent_first" ||
               scenario == "duplicate_character" || scenario == "empty_file" ||
               scenario == "tombstone") {
        result = PlayerDumpReader().LoadDump(file, account, "", 0, policy);
        check(result == DUMP_IDENTITY_ERROR && !g_inTransaction &&
              std::find(g_events.begin(), g_events.end(), "rollback") != g_events.end() &&
              g_textCalls == 0 && g_highCalls == 0 && g_cacheCalls == 0 &&
              g_countPubCalls == 0, "malformed character rejected/clean");
        if (scenario == "duplicate_character") {
            check(g_claimCalls == 1, "duplicate first row queued then rolled back");
            checkOwnedRejection(result, DUMP_IDENTITY_ERROR, 1, 1);
        } else {
            check(g_claimCalls == 0, "no claim before rejection");
            checkOwnedRejection(result, DUMP_IDENTITY_ERROR);
        }
        check(g_openCalls == 1 && g_closeCalls == 1 && g_doubleClose == 0,
              "rejected file closed once");
    } else if (scenario == "ownership_input") {
        result = PlayerDumpReader().LoadDump(file, account, "", 0, policy);
        check(result == DUMP_FILE_BROKEN && g_claimCalls == 0 &&
              std::find(g_events.begin(), g_events.end(), "rollback") != g_events.end(),
              "ownership table input prohibited");
        checkOwnedRejection(result, DUMP_FILE_BROKEN);
    } else if (scenario == "max_minus_two" || scenario == "max_guid") {
        uint32 value = scenario == "max_minus_two" ? 0xFFFFFFFDu : 0xFFFFFFFFu;
        result = PlayerDumpReader().LoadDump(file, account, "", value, policy);
        check(result == DUMP_IDENTITY_ERROR && g_openCalls == 0 &&
              g_countCalls == 0 && g_claimCalls == 0 && g_charSetCalls == 0 &&
              g_events.empty(), "boundary before all effects");
    } else if (scenario == "explicit_commit_failure") {
        sObjectMgr.m_CharGuids.next = 1000;
        g_commitOk = false;
        result = PlayerDumpReader().LoadDump(file, account, "", 1000, policy);
        check(result == DUMP_DATABASE_ERROR && g_charSetCalls == 0 &&
              g_textCalls == 0 && g_highCalls == 0 && g_cacheCalls == 0,
              "explicit commit failure no setter/publication");
    } else if (scenario == "canonicalize" || scenario == "reject_name") {
        if (scenario == "canonicalize") g_canonicalizeAlice = true;
        else g_rejectFixtureName = true;
        result = PlayerDumpReader().LoadDump(file, account, "", 0, policy);
        if (scenario == "canonicalize") {
            check(result == DUMP_SUCCESS &&
                  g_rawCharacterSql.find("'Alice Smith'") != std::string::npos &&
                  g_lastCharacterWrite.find("|claim:Alice Smith:") != std::string::npos,
                  "canonicalized name reaches write/claim");
        } else {
            check(result == DUMP_IDENTITY_ERROR && g_rawCharacterSql.empty() &&
                  g_claimCalls == 0, "validator rejection before writes");
        checkOwnedRejection(result, DUMP_IDENTITY_ERROR);
        }
    } else if (scenario == "exact_sql") {
        g_schema.push_back("keep");
        result = PlayerDumpReader().LoadDump(file, account, "", 0, policy);
        std::string expected = "INSERT INTO `characters` VALUES (4242, 7010, 'Alice Smith', 0, NULL, NULL, NULL, 1, 'keep');";
        check(result == DUMP_SUCCESS && g_rawCharacterSql == expected,
              "exact rewritten identity SQL");
        check(g_lastCharacterWrite.find("|claim:Alice Smith:4242") != std::string::npos,
              "exact bound claim identity");
    } else if (scenario == "success") {
        result = PlayerDumpReader().LoadDump(file, account, "", 0, policy);
        check(result == DUMP_SUCCESS, "import success");
        check(std::count(g_events.begin(), g_events.end(), "begin") == 1,
              "exactly one begin");
        check(g_guidCalls == 1 && g_claimCalls == 1, "one automatic guid/claim");
        check(g_lastCharacterWrite.find("Alice Smith") != std::string::npos &&
              g_lastCharacterWrite.find("4242") != std::string::npos &&
              g_lastCharacterWrite.find("|claim:Alice Smith:4242") != std::string::npos,
              "canonical name/guid bound claim");
        check(g_lastCharacterWrite.find(", 1)") != std::string::npos ||
              g_lastCharacterWrite.find(",1)") != std::string::npos,
              "generation one");
        check(eventBefore("write", "claim") && eventBefore("claim", "commit") &&
              eventBefore("commit", "text") && eventBefore("commit", "highwater"),
              "atomic write/claim before commit/publication");
        check(g_commitObserved && g_textObserved && g_highObserved,
              "excluded at commit/text/high-water");
        check(g_cacheCalls == 1 && g_countPubCalls == 1, "cache/count publication");
        check(g_writeInTxn && g_claimInTxn, "write/claim share owned transaction");
        check(g_charSetCalls == 0, "automatic guid no character setter");
        check(g_openCalls == 1 && g_closeCalls == 1 && g_openHandle == nullptr &&
              g_doubleClose == 0, "success closes file once");
        check(admissible(account), "success releases admission");
    } else if (scenario == "override") {
        result = PlayerDumpReader().LoadDump(file, account, "Alice Smith", 0, policy);
        check(result == DUMP_SUCCESS &&
              g_lastCharacterWrite.find("Alice Smith") != std::string::npos,
              "explicit override survives");
    } else { std::cerr << "unknown scenario\n"; return 2; }
    if (failures) { std::cerr << "failures=" << failures << "\n"; return 1; }
    std::cout << "import " << scenario << ": PASS\n";
    return 0;
}
'''


@unittest.skipUnless(shutil.which("cmake"), "cmake not available")
class PlayerDumpAdmissionExecutionTests(unittest.TestCase):
    def test_actual_load_dump(self):
        cpp = CPP.read_text(encoding="utf-8")
        header = HEADER.read_text(encoding="utf-8")
        enum = extract(header, "enum DumpTableType") + ";"
        dump_return = extract(header, "enum DumpReturn") + ";"
        tables = cpp[cpp.index("struct DumpTable"):cpp.index("// Low level functions")]
        helpers = "\n".join(
            extract(cpp, sig) for sig in [
                "static bool findtoknth", "static std::string gettoknth",
                "static bool findnth", "static std::string gettablename",
                "static bool changenth", "static std::string getnth",
                "static bool changetoknth", "static uint32 registerNewGuid",
                "static bool changeGuid", "static bool changetokGuid",
                "static bool ParseCharacterRowValues",
                "static int FindColumn", "static std::string JoinSqlValues",
                "static bool ReadSingleDumpCount"])
        body = extract(cpp, "DumpReturn PlayerDumpReader::LoadDump")
        preamble = (PREAMBLE.replace("// DUMP_ENUM", enum)
                    .replace("// DUMP_TABLES", tables)
                    .replace("class PlayerDumpReader", dump_return + "\nclass PlayerDumpReader"))
        program = preamble + "\n" + helpers + "\n" + body + "\n" + MAIN
        with tempfile.TemporaryDirectory(prefix="fn05h-v44-import-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(import_exec CXX)\n"
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
            valid = temp / "valid.sql"
            valid.write_text(
                "INSERT INTO `characters` VALUES ('1', '1', 'Alice Smith', '0', NULL, NULL, NULL, '0');\n"
                "INSERT INTO `item_text` VALUES ('7', 'hello');\n", encoding="utf-8")
            malformed = temp / "bad.sql"
            malformed.write_text("garbage\n", encoding="utf-8")
            column_list = temp / "column_list.sql"
            column_list.write_text(
                "INSERT INTO `characters` (`guid`) VALUES ('1');\n", encoding="utf-8")
            bad_prefix = temp / "bad_prefix.sql"
            bad_prefix.write_text(
                "UPDATE INTO `characters` VALUES ('1', '1', 'Alice Smith', '0', NULL, NULL, NULL, '0');\n",
                encoding="utf-8")
            trailing_sql = temp / "trailing_sql.sql"
            trailing_sql.write_text(
                "INSERT INTO `characters` VALUES ('1', '1', 'Alice Smith', '0', NULL, NULL, NULL, '0'); SELECT 1;\n",
                encoding="utf-8")
            dependent_first = temp / "dependent_first.sql"
            dependent_first.write_text(
                "INSERT INTO `item_text` VALUES ('7', 'hello');\n"
                "INSERT INTO `characters` VALUES ('1', '1', 'Alice Smith', '0', NULL, NULL, NULL, '0');\n",
                encoding="utf-8")
            duplicate_character = temp / "duplicate_character.sql"
            duplicate_character.write_text(
                "INSERT INTO `characters` VALUES ('1', '1', 'Alice Smith', '0', NULL, NULL, NULL, '0');\n"
                "INSERT INTO `characters` VALUES ('2', '1', 'Bob Jones', '0', NULL, NULL, NULL, '0');\n",
                encoding="utf-8")
            empty_file = temp / "empty.sql"; empty_file.write_text("", encoding="utf-8")
            tombstone = temp / "tombstone.sql"
            tombstone.write_text(
                "INSERT INTO `characters` VALUES ('1', '1', 'Alice Smith', '0', NULL, NULL, '2020-01-01', '0');\n",
                encoding="utf-8")
            ownership_input = temp / "ownership.sql"
            ownership_input.write_text(
                "INSERT INTO `character_name_ownership` VALUES ('Alice Smith', '1');\n",
                encoding="utf-8")
            canonical_source = temp / "canonical.sql"
            canonical_source.write_text(
                "INSERT INTO `characters` VALUES ('1', '1', 'aLICE sMITH', '0', NULL, NULL, NULL, '0');\n",
                encoding="utf-8")
            reject_source = temp / "reject.sql"
            reject_source.write_text(
                "INSERT INTO `characters` VALUES ('1', '1', 'Fixture Invalid', '0', NULL, NULL, NULL, '0');\n",
                encoding="utf-8")
            valid_keep = temp / "valid_keep.sql"
            valid_keep.write_text(
                "INSERT INTO `characters` VALUES ('1', '1', 'Alice Smith', '0', NULL, NULL, NULL, '0', 'keep');\n",
                encoding="utf-8")
            cases = [
                ("blocked", "missing.sql"), ("count_failure", "missing.sql"),
                ("busy", "missing.sql"), ("ambient", "missing.sql"),
                ("missing_account", str(valid)), ("explicit_guid", str(valid)),
                ("malformed", str(malformed)), ("commit_false", str(valid)),
                ("claim_failure", str(valid)), ("cache_failure", str(valid)),
                ("count_publication_failure", str(valid)),
                ("count_shape", "missing.sql"), ("account_two", str(valid)), ("account_malformed", str(valid)), ("account_null", str(valid)), ("guid_query_failure", str(valid)), ("override_ownership_failure", str(valid)), ("row_ownership_failure", str(valid)), ("name_query_failure", str(valid)), ("schema_empty", str(valid)), ("schema_null", str(valid)), ("schema_duplicate", str(valid)), ("schema_missing", str(valid)), ("column_list", str(column_list)), ("bad_prefix", str(bad_prefix)), ("trailing_sql", str(trailing_sql)), ("dependent_first", str(dependent_first)), ("duplicate_character", str(duplicate_character)), ("empty_file", str(empty_file)), ("tombstone", str(tombstone)), ("ownership_input", str(ownership_input)), ("max_minus_two", "missing.sql"), ("max_guid", "missing.sql"), ("explicit_commit_failure", str(valid)), ("canonicalize", str(canonical_source)), ("reject_name", str(reject_source)), ("exact_sql", str(valid_keep)), ("collision_failure", str(valid)), ("explicit_below", str(valid)), ("explicit_at", str(valid)), ("explicit_above", str(valid)), ("explicit_upper", "missing.sql"), ("high_cache_failure", str(valid)), ("override_different", str(valid)), ("high_guid", str(valid)), ("success", str(valid)), ("override", str(valid)),
            ]
            failures = []
            for scenario, path in cases:
                result = subprocess.run([str(executable), scenario, path],
                                        text=True, capture_output=True, timeout=30)
                if result.returncode != 0 or (
                        "import " + scenario + ": PASS") not in result.stdout:
                    failures.append(scenario + "\n" + result.stdout + result.stderr)
            self.assertFalse(failures, "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
