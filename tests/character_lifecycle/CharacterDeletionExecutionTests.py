#!/usr/bin/env python3
"""Actual char-delete handler and Player::DeleteFromDB with real registry."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAYER = ROOT / "src/game/Objects/Player.cpp"
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
#include "Database/CharacterPersistenceRecovery.h"
#include <iostream>
#include <ctime>
#include <memory>
#include <string>
#include <vector>

using uint32 = unsigned int;
using uint64 = unsigned long long;
using uint8 = unsigned char;

enum { CHAR_DELETE_SUCCESS=0, CHAR_DELETE_FAILED=1 };
enum { CONFIG_UINT32_CHARDELETE_METHOD=1, CONFIG_UINT32_CHARDELETE_MIN_LEVEL=2 };
enum { LOG_BASIC=0, LOG_LVL_ERROR=0, LOG_CHAR=1, LOG_LVL_BASIC=0 };
std::vector<std::string> g_events;
std::vector<uint32> g_petIds;
int g_identityQueries=0, g_corpseCalls=0, g_groupRemovals=0;
int g_guildMembers=0, g_guildDisbands=0, g_guildDeletes=0, g_cacheDeletes=0;
bool g_builderProtected=false, g_execProtected=false;
bool g_publishProtected=false, g_realmLeaseValid=false;
bool g_realmAccountOk=false;
struct Log { template<typename... A> void Out(A&&...) {} template<typename... A> void Player(A&&...) {} } sLog;
struct ObjectGuid { uint32 value=0; ObjectGuid(){} ObjectGuid(int,uint32 v):value(v){} uint32 GetCounter() const { return value; } bool IsPlayer() const { return true; } };
enum { HIGHGUID_PLAYER=0 };

struct CharacterIdentitySnapshot {
    uint32 guid=0, accountId=0; std::string canonicalName; uint64 generation=0;
};
struct CharacterDeletionSnapshot { CharacterIdentitySnapshot identity; uint64 deletedTime=0; };
struct SoftCharacterDeletion { CharacterIdentitySnapshot identity; uint64 deletionTime=0; };

struct Field { uint32 u=0; uint64 u64=0; std::string s; bool null=false;
    uint32 GetUInt32() const { return u; } uint64 GetUInt64() const { return u64; }
    std::string GetCppString() const { return s; } char const* GetString() const { return null ? nullptr : s.c_str(); } };
struct QueryResult {
    std::vector<std::vector<Field>> rows; std::size_t index=0;
    Field* Fetch() { return rows.empty()?nullptr:rows[index].data(); }
    bool NextRow() { if(index+1>=rows.size()) return false; ++index; return true; }
};
struct SqlStatementID {};

struct Database {
    bool g_identityOk=true, g_codOk=true, codNull=false;
    std::vector<std::vector<Field>> identityRows, petRows;
    std::string codValue="0";
    std::unique_ptr<QueryResult> PQuery(char const* fmt, ...) {
        std::string q=fmt; auto r=std::make_unique<QueryResult>();
        if (q.find("FROM `characters`")!=std::string::npos) {
            ++g_identityQueries;
            if(!g_identityOk) return nullptr; r->rows=identityRows; return r;
        }
        if (q.find("COUNT(*) FROM `mail`")!=std::string::npos) {
            if(!g_codOk) return nullptr;
            if (!codValue.empty()) r->rows={{{0,0,codValue,codNull}}}; return r;
        }
        if (q.find("FROM `character_pet`")!=std::string::npos) {
            r->rows=petRows; return r;
        }
        return nullptr;
    }
} CharacterDatabase;

struct SqlCheckedBatch {};
enum class SqlAffectedRowsPolicy { Any, ExactlyOne };
struct SqlStatement { bool AppendToCheckedBatch(SqlCheckedBatch&, SqlAffectedRowsPolicy){return true;} };
enum class SqlWriteOutcome { Succeeded, Failed, DuplicateKey, OutcomeUnknown };
enum class SqlBatchStage { NotStarted, Begin, Statement, RowCount, Commit, Complete };
struct SqlWriteResult { int outcome=0; };
struct SqlBatchResult {
    SqlWriteOutcome outcome=SqlWriteOutcome::Succeeded;
    SqlBatchStage stage=SqlBatchStage::Complete;
    std::size_t statementIndex=0; bool rollbackAttempted=false;
};
struct CharacterPersistenceContext {
    CharacterPersistenceContext(Database&, uint32) {}
    SqlCheckedBatch& Batch(){static SqlCheckedBatch b;return b;}
    SqlBatchResult Execute();
    static SqlBatchResult g_batch;
};
SqlBatchResult CharacterPersistenceContext::g_batch;
int g_execCalls=0;
SqlBatchResult CharacterPersistenceContext::Execute(){++g_execCalls; g_events.push_back("execute");
    g_execProtected = !GetAccountLifecycleRegistry().TryAcquire({3010},AccountLifecycleKind::Mutation);
    return g_batch;}


struct SqlStatementID;
bool g_softOk=true, g_permOk=true;
CharacterDeletionSnapshot g_softRequest; CharacterDeletionSnapshot g_permRequest;
bool g_softCalled=false, g_permCalled=false;
bool AppendSoftCharacterDeletion(Database&, CharacterPersistenceContext&, SoftCharacterDeletion const& d) {
    g_softCalled=true; g_softRequest.identity=d.identity; g_softRequest.deletedTime=d.deletionTime;
    g_events.push_back("builder"); g_builderProtected = !GetAccountLifecycleRegistry().TryAcquire({3010},AccountLifecycleKind::Mutation);
    return g_softOk;
}
bool AppendPermanentCharacterDeletion(Database&, CharacterPersistenceContext&, CharacterDeletionSnapshot const& d) {
    g_permCalled=true; g_permRequest=d; g_events.push_back("builder");
    g_builderProtected = !GetAccountLifecycleRegistry().TryAcquire({3010},AccountLifecycleKind::Mutation);
    return g_permOk;
}
struct CharacterDeletionRelationshipPlan {};
bool g_relAppendOk=true, g_relCalled=false, g_relApplied=false;
bool AppendCharacterDeletionRelationships(CharacterPersistenceContext&, ObjectGuid, CharacterDeletionRelationshipPlan&) {
    g_relCalled=true; g_events.push_back("relationships");
    return g_relAppendOk;
}
void ApplyCharacterDeletionRelationships(CharacterDeletionRelationshipPlan&) {
    g_relApplied=true; g_events.push_back("relationships_apply");
}
bool isCanonicalFullPlayerName(std::string const& n){return !n.empty() && n.find(' ')!=std::string::npos;}

struct World {
    uint32 method=1; bool countOk=true; int countCalls=0; bool countLeaseValid=false;
    uint32 getConfig(int id){ if(id==CONFIG_UINT32_CHARDELETE_METHOD) return method;
        if(id==CONFIG_UINT32_CHARDELETE_MIN_LEVEL) return 0; return 0; }
    bool UpdateRealmCharCount(uint32 account, AccountLifecycleLease const* lease, uint32* =nullptr){
        ++countCalls; g_events.push_back("realm_count");
        g_publishProtected = !GetAccountLifecycleRegistry().TryAcquire({3010},AccountLifecycleKind::Mutation);
        g_realmAccountOk = account==3010;
        g_realmLeaseValid = lease && lease->IsActive() && lease->Covers(3010) &&
            GetAccountLifecycleRegistry().Allows(*lease,3010,AccountLifecycleKind::Mutation);
        countLeaseValid = g_realmLeaseValid;
        return countOk;
    }
} sWorld;

struct PlayerCacheSnapshot { bool ok=true; uint32 uiLevel=60; uint32 uiAccount=3010; std::string sName="Old Smith"; explicit operator bool() const {return ok;} PlayerCacheSnapshot* operator->(){return this;} };
struct Guild { bool DelMember(ObjectGuid){++g_guildMembers; g_events.push_back("guild_member"); return true;} void Disband(){++g_guildDisbands; g_events.push_back("guild_disband");} ~Guild(){++g_guildDeletes;} };
struct Group {};
struct ObjectMgr {
    PlayerCacheSnapshot cache; bool hasCache=true; bool group=false;
    PlayerCacheSnapshot GetPlayerDataByGUID(uint32){ return hasCache?cache:PlayerCacheSnapshot{false}; }
    PlayerCacheSnapshot GetPlayerDataByGUID(ObjectGuid g){ return GetPlayerDataByGUID(g.GetCounter()); }
    Group* GetGroupByMember(ObjectGuid){ return group?&groupObject:nullptr; }
    Group groupObject;
    void DeletePlayerFromCache(uint32){++g_cacheDeletes; g_events.push_back("cache_delete");}
} sObjectMgr;
struct WorldSession;
struct Player;
struct ObjectAccessor {
    bool loaded=false;
    static bool FindPlayerNotInWorld(ObjectGuid);
    static Player* FindPlayer(ObjectGuid);
    void ConvertCorpseForPlayer(ObjectGuid, Player const* =nullptr, bool =true){++g_corpseCalls; g_events.push_back("corpse");}
};
ObjectAccessor sObjectAccessor;
bool ObjectAccessor::FindPlayerNotInWorld(ObjectGuid){return sObjectAccessor.loaded;}
Player* ObjectAccessor::FindPlayer(ObjectGuid){return nullptr;}
struct GuildMgr { bool leader=false; Guild* guild=nullptr;
    Guild* GetPlayerGuild(ObjectGuid){return guild;}
    bool GetGuildByLeader(ObjectGuid){return leader;} } sGuildMgr;
struct Cache { int petDeletes=0; void DeleteCharacterPetById(uint32 id){++petDeletes; g_petIds.push_back(id); g_events.push_back("pet");} } sCharacterDatabaseCache;
int g_petitionCalls=0;

struct Player {
    static bool DeleteFromDB(ObjectGuid,uint32,bool updateRealmChars=true,bool deleteFinally=false,
        CharacterDeletionSnapshot const* expected=nullptr,AccountLifecycleLease const* lease=nullptr);
    static void RemovePetitionsAndSigns(ObjectGuid,uint32 exceptPetitionId=0);
    static void RemoveFromGroup(Group*,ObjectGuid);
    WorldSession* GetSession() const;
};
static bool ParseDeletionCount(char const* text, uint64& out);

WorldSession* Player::GetSession() const { return nullptr; }
void Player::RemovePetitionsAndSigns(ObjectGuid,uint32){++g_petitionCalls; g_events.push_back("petitions");}
void Player::RemoveFromGroup(Group*,ObjectGuid){++g_groupRemovals; g_events.push_back("group");}
namespace WorldPackets { namespace Character {
struct CharDelete { ObjectGuid guid; };
struct CharDeleteResponse { uint8 result=0; };
}}
struct WorldSession {
    uint32 account=3010; int sent=0; uint8 last=255;
    uint32 GetAccountId() const {return account;}
    void LogoutPlayer(bool){}
    void HandleCharDeleteOpcode(WorldPackets::Character::CharDelete const& packet);
    template<typename T> void SendPacket(std::unique_ptr<T> p){++sent; last=p->result;}
};
'''

MAIN = r'''
static int failures=0;
static void check(bool v, std::string const& l){if(!v){std::cerr<<"FAIL "<<l<<"\n";++failures;}}
static bool record(uint32 guid,uint32 account,uint64 gen,CharacterRecoveryKind kind){
    for(auto const& r:GetCharacterRecoveryRecords())
        if(r.kind==kind && r.guids.size()==1 && r.guids[0]==guid &&
           r.accountIds.size()==1 && r.accountIds[0]==account && r.expectedGeneration==gen) return true;
    return false;
}
static void activeRows(){
    std::vector<Field> row(6);
    row[0].u=3010; row[1].s="Old Smith"; row[2].u64=9; row[3].u64=0;
    row[4].u=0; row[5].s="";
    CharacterDatabase.identityRows={row};
}
int main(int argc,char** argv){
    std::string sc=argc>1?argv[1]:"";
    ObjectGuid guid(HIGHGUID_PLAYER,77);
    activeRows();
    CharacterDatabase.petRows={{{101,0,"",false}},{{102,0,"",false}}};
    CharacterDatabase.petRows[0][0].u=101; CharacterDatabase.petRows[1][0].u=102;
    bool ok=false;
    if(sc=="soft_success"){
        uint64 before=uint64(std::time(nullptr));
        ok=Player::DeleteFromDB(guid,3010,true,false,nullptr,nullptr);
        uint64 after=uint64(std::time(nullptr));
        check(ok && g_softCalled && g_execCalls==1 && sWorld.countCalls==1,
              "soft success one execute/count");
        check(g_softRequest.identity.guid==77 && g_softRequest.identity.accountId==3010 &&
              g_softRequest.identity.canonicalName=="Old Smith" && g_softRequest.identity.generation==9,
              "soft exact active identity");
        check(g_softRequest.deletedTime>=before && g_softRequest.deletedTime<=after,
              "soft deletion timestamp bounded");
        check(g_relCalled && g_relApplied, "soft relationship append/apply");
        std::vector<std::string> expected={"builder","relationships","execute","corpse","relationships_apply","cache_delete","realm_count"};
        check(g_events==expected, "soft exact event order");
        check(g_builderProtected && g_execProtected && g_publishProtected,
              "admission excluded at builder/execute/realm");
        check(g_realmAccountOk && g_realmLeaseValid, "realm lease account/kind/global");
        check(GetAccountLifecycleRegistry().TryAcquire({3010},AccountLifecycleKind::Mutation)!=nullptr,
              "owned lease released");
    } else if(sc=="permanent_success"){
        sWorld.method=0;
        ok=Player::DeleteFromDB(guid,3010,true,true,nullptr,nullptr);
        check(ok && g_permCalled && sCharacterDatabaseCache.petDeletes==2 && sWorld.countCalls==1,
              "permanent pets/count");
        check(g_petIds==std::vector<uint32>({101,102}), "exact pet ids");
        std::vector<std::string> expected={"builder","relationships","execute","pet","pet","corpse","relationships_apply","cache_delete","realm_count"};
        check(g_events==expected, "permanent exact event order");
        check(g_permRequest.identity.generation==9, "permanent snapshot");
    } else if(sc=="permanent_cod"){
        sWorld.method=0; CharacterDatabase.codValue="5";
        ok=Player::DeleteFromDB(guid,3010,true,true,nullptr,nullptr);
        check(!ok && !g_permCalled && g_execCalls==0, "nonzero COD rejects before builder");
    } else if(sc=="tombstone"){
        std::vector<Field> row(6);
        row[0].u=0; row[1].s=""; row[2].u64=9; row[3].u64=12345;
        row[4].u=3010; row[5].s="Old Smith";
        CharacterDatabase.identityRows={row};
        sWorld.method=0;
        ok=Player::DeleteFromDB(guid,3010,true,true,nullptr,nullptr);
        check(ok && g_permRequest.identity.accountId==3010 &&
              g_permRequest.identity.canonicalName=="Old Smith" &&
              g_permRequest.deletedTime==12345, "tombstone original identity");
    } else if(sc=="supplied_mismatch"){
        CharacterDeletionSnapshot s; s.identity.guid=77; s.identity.accountId=9999;
        ok=Player::DeleteFromDB(guid,3010,true,false,&s,nullptr);
        check(!ok && !g_softCalled && g_execCalls==0, "supplied mismatch reject");
    } else if(sc=="assembly_failure"){
        g_softOk=false;
        ok=Player::DeleteFromDB(guid,3010,true,false,nullptr,nullptr);
        check(!ok && g_execCalls==0 && sWorld.countCalls==0 && !g_relCalled && !g_relApplied &&
              !record(77,3010,9,CharacterRecoveryKind::SoftDeletion),
              "assembly failure stops before relationships/execute/recovery");
    } else if(sc=="relationship_assembly_failure"){
        g_relAppendOk=false;
        ok=Player::DeleteFromDB(guid,3010,true,false,nullptr,nullptr);
        check(!ok && g_execCalls==0 && g_relCalled && !g_relApplied && sWorld.countCalls==0,
              "relationship assembly failure: no execute/publication");
    } else if(sc=="known_failure"){
        CharacterPersistenceContext::g_batch.outcome=SqlWriteOutcome::Failed;
        ok=Player::DeleteFromDB(guid,3010,true,false,nullptr,nullptr);
        check(!ok && !record(77,3010,9,CharacterRecoveryKind::SoftDeletion) &&
              !g_relApplied && g_corpseCalls==0, "known failure no recovery/publication");
    } else if(sc=="unknown"){
        CharacterPersistenceContext::g_batch.outcome=SqlWriteOutcome::OutcomeUnknown;
        ok=Player::DeleteFromDB(guid,3010,true,false,nullptr,nullptr);
        check(!ok && record(77,3010,9,CharacterRecoveryKind::SoftDeletion) &&
              !GetAccountLifecycleRegistry().TryAcquire({3010},AccountLifecycleKind::Mutation) &&
              !g_relApplied && g_corpseCalls==0,
              "unknown recovery blocks all publication");
    } else if(sc=="count_failure"){
        sWorld.countOk=false;
        ok=Player::DeleteFromDB(guid,3010,true,false,nullptr,nullptr);
        check(!ok && sWorld.countCalls==1 && record(77,3010,9,CharacterRecoveryKind::PermanentDeletion) &&
              GetAccountLifecycleRegistry().TryAcquire({3010},AccountLifecycleKind::Mutation)==nullptr,
              "count failure recovery/blocked account");
    } else if(sc=="borrowed"){
        auto lease=GetAccountLifecycleRegistry().TryAcquire({3010},AccountLifecycleKind::Mutation);
        ok=Player::DeleteFromDB(guid,3010,true,false,nullptr,lease.get());
        check(ok && lease->IsActive(), "borrowed retained");
        lease.reset();
    } else if(sc=="deletion_kind_count"){
        auto lease=GetAccountLifecycleRegistry().TryAcquire({3010},AccountLifecycleKind::Deletion);
        ok=Player::DeleteFromDB(guid,3010,true,false,nullptr,lease.get());
        check(!ok && !g_softCalled, "Deletion-kind count rejected");
        lease.reset();
    } else if(sc=="supplied_guid_mismatch"){
        CharacterDeletionSnapshot snap; snap.identity.guid=78; snap.identity.accountId=3010;
        ok=Player::DeleteFromDB(guid,3010,true,false,&snap,nullptr);
        check(!ok && !g_softCalled && g_execCalls==0, "supplied GUID mismatch reject");
    } else if(sc=="identity_query_null"){
        CharacterDatabase.g_identityOk=false;
        ok=Player::DeleteFromDB(guid,3010,true,false,nullptr,nullptr);
        check(!ok && !g_softCalled && g_execCalls==0, "null identity query reject");
    } else if(sc=="generation_zero"){
        CharacterDatabase.identityRows[0][2].u64=0;
        ok=Player::DeleteFromDB(guid,3010,true,false,nullptr,nullptr);
        check(!ok && !g_softCalled && g_execCalls==0, "zero generation reject");
    } else if(sc=="deletion_kind_no_count"){
        auto lease=GetAccountLifecycleRegistry().TryAcquire({3010},AccountLifecycleKind::Deletion);
        ok=Player::DeleteFromDB(guid,3010,false,false,nullptr,lease.get());
        check(ok && g_softCalled && sWorld.countCalls==0, "Deletion-kind no-count success");
        lease.reset();
    } else if(sc=="supplied_valid"){
        CharacterDeletionSnapshot snap; snap.identity.guid=77; snap.identity.accountId=3010;
        snap.identity.canonicalName="Old Smith"; snap.identity.generation=9;
        ok=Player::DeleteFromDB(guid,3010,true,false,&snap,nullptr);
        check(ok && g_softCalled && g_identityQueries==0, "supplied snapshot skips identity query");
    } else if(sc=="identity_missing_row"){
        CharacterDatabase.identityRows.clear();
        ok=Player::DeleteFromDB(guid,3010,true,false,nullptr,nullptr);
        check(!ok && g_identityQueries==1 && !g_softCalled, "missing row reject");
    } else if(sc=="identity_wrong_account"){
        CharacterDatabase.identityRows[0][0].u=9999;
        ok=Player::DeleteFromDB(guid,3010,true,false,nullptr,nullptr);
        check(!ok && !g_softCalled, "identity wrong account reject");
    } else if(sc=="generation_max"){
        CharacterDatabase.identityRows[0][2].u64=std::numeric_limits<uint64>::max();
        ok=Player::DeleteFromDB(guid,3010,true,false,nullptr,nullptr);
        check(!ok && !g_softCalled, "max generation reject");
    } else if(sc=="cod_query_failure"){
        sWorld.method=0; CharacterDatabase.g_codOk=false;
        ok=Player::DeleteFromDB(guid,3010,true,true,nullptr,nullptr);
        check(!ok && !g_permCalled && g_execCalls==0, "COD query failure reject");
    } else if(sc=="cod_missing_row"){
        sWorld.method=0; CharacterDatabase.codValue="";
        ok=Player::DeleteFromDB(guid,3010,true,true,nullptr,nullptr);
        check(!ok && !g_permCalled, "COD missing row reject");
    } else if(sc=="cod_null"){
        sWorld.method=0; CharacterDatabase.codNull=true;
        ok=Player::DeleteFromDB(guid,3010,true,true,nullptr,nullptr);
        check(!ok && !g_permCalled, "COD null reject");
    } else if(sc=="cod_malformed"){
        sWorld.method=0; CharacterDatabase.codValue="-1";
        ok=Player::DeleteFromDB(guid,3010,true,true,nullptr,nullptr);
        check(!ok && !g_permCalled, "COD malformed reject");
    } else if(sc=="busy_account"){
        auto hold=GetAccountLifecycleRegistry().TryAcquire({3010},AccountLifecycleKind::Mutation);
        ok=Player::DeleteFromDB(guid,3010,true,false,nullptr,nullptr);
        check(!ok && !g_softCalled && hold->IsActive(), "busy rejects/no effects");
    } else if(sc=="foreign_lease"){
        AccountLifecycleRegistry foreign;
        auto lease=foreign.TryAcquire({3010},AccountLifecycleKind::Mutation);
        ok=Player::DeleteFromDB(guid,3010,true,false,nullptr,lease.get());
        check(!ok && !g_softCalled && lease->IsActive(), "foreign lease rejects");
    } else if(sc=="soft_unknown" || sc=="permanent_unknown"){
        if(sc=="permanent_unknown") sWorld.method=0;
        CharacterPersistenceContext::g_batch.outcome=SqlWriteOutcome::OutcomeUnknown;
        ok=Player::DeleteFromDB(guid,3010,true,sc=="permanent_unknown",nullptr,nullptr);
        CharacterRecoveryKind kind=sc=="permanent_unknown"?CharacterRecoveryKind::PermanentDeletion:CharacterRecoveryKind::SoftDeletion;
        check(!ok && record(77,3010,9,kind) && g_petIds.empty() && !g_relApplied &&
              g_corpseCalls==0 &&
              GetAccountLifecycleRegistry().TryAcquire({3010},AccountLifecycleKind::Mutation)==nullptr,
              "exact unknown recovery/no publication/blocked");
    } else if(sc=="permanent_assembly_failure"){
        sWorld.method=0; g_permOk=false;
        ok=Player::DeleteFromDB(guid,3010,true,true,nullptr,nullptr);
        check(!ok && g_execCalls==0 && g_petIds.empty() && !g_relCalled && !g_relApplied,
              "permanent assembly stops before relationships");
    } else if(sc=="permanent_known_failure"){
        sWorld.method=0;
        CharacterPersistenceContext::g_batch.outcome=SqlWriteOutcome::Failed;
        ok=Player::DeleteFromDB(guid,3010,true,true,nullptr,nullptr);
        check(!ok && !record(77,3010,9,CharacterRecoveryKind::PermanentDeletion) &&
              g_petIds.empty() && !g_relApplied, "permanent known no recovery/publication");
    } else if(sc=="incomplete_stage"){
        CharacterPersistenceContext::g_batch.stage=SqlBatchStage::Commit;
        ok=Player::DeleteFromDB(guid,3010,true,false,nullptr,nullptr);
        check(!ok && g_petIds.empty() && g_corpseCalls==0 && !g_relApplied,
              "incomplete stage no publication");
    } else if(sc=="handler_execute_failure"){
        CharacterPersistenceContext::g_batch.outcome=SqlWriteOutcome::Failed;
        WorldSession session; WorldPackets::Character::CharDelete packet; packet.guid=guid;
        session.HandleCharDeleteOpcode(packet);
        check(session.sent==1 && session.last==CHAR_DELETE_FAILED &&
              g_corpseCalls==0 && g_cacheDeletes==0 && sWorld.countCalls==0,
              "handler execute failure exact response/no publication");
    } else if(sc=="handler_count_failure"){
        sWorld.countOk=false;
        WorldSession session; WorldPackets::Character::CharDelete packet; packet.guid=guid;
        session.HandleCharDeleteOpcode(packet);
        check(session.sent==1 && session.last==CHAR_DELETE_FAILED &&
              g_corpseCalls==1 && g_cacheDeletes==1 && sWorld.countCalls==1 &&
              record(77,3010,9,CharacterRecoveryKind::PermanentDeletion),
              "handler count failure effects/recovery/response");    } else if(sc=="parse_counts"){
        uint64 out=777;
        check(ParseDeletionCount("0",out) && out==0, "parse zero");
        check(ParseDeletionCount("18446744073709551615",out) && out==UINT64_MAX, "parse max");
        uint64 keep=555;
        check(!ParseDeletionCount(nullptr,keep) && keep==555, "parse null");
        check(!ParseDeletionCount("",keep) && keep==555, "parse empty");
        check(!ParseDeletionCount("-1",keep) && keep==555, "parse sign");
        check(!ParseDeletionCount("+1",keep) && keep==555, "parse plus");
        check(!ParseDeletionCount("1x",keep) && keep==555, "parse nondigit");
        check(!ParseDeletionCount("18446744073709551616",keep) && keep==555, "parse overflow");
        ok=true;
    } else if(sc=="zero_guid"){
        ObjectGuid zero(HIGHGUID_PLAYER,0);
        ok=Player::DeleteFromDB(zero,3010,true,false,nullptr,nullptr);
        check(!ok && !g_softCalled && g_execCalls==0, "zero GUID reject");
    } else if(sc=="zero_account"){
        ok=Player::DeleteFromDB(guid,0,true,false,nullptr,nullptr);
        check(!ok && !g_softCalled && g_execCalls==0, "zero account reject");
    } else if(sc=="wrong_account_borrowed"){
        auto lease=GetAccountLifecycleRegistry().TryAcquire({9999},AccountLifecycleKind::Mutation);
        ok=Player::DeleteFromDB(guid,3010,true,false,nullptr,lease.get());
        check(!ok && !g_softCalled && lease->IsActive(), "wrong-account lease rejects/retained");
        check(GetAccountLifecycleRegistry().TryAcquire({3010},AccountLifecycleKind::Mutation)!=nullptr,
              "unrelated character account remains free");
        lease.reset();
    } else if(sc=="guild_group_success"){
        sGuildMgr.guild=new Guild(); sObjectMgr.group=true;
        ok=Player::DeleteFromDB(guid,3010,true,false,nullptr,nullptr);
        std::vector<std::string> expected={"builder","relationships","execute","corpse",
            "relationships_apply","cache_delete","realm_count"};
        // Guild/group/petition row SQL and local effects now live behind the
        // relationship append/apply contract; the legacy per-deletion calls
        // must not run inside Player::DeleteFromDB any more.
        check(ok && g_events==expected && g_guildMembers==0 && g_guildDisbands==0 &&
              g_groupRemovals==0 && g_petitionCalls==0 && g_relCalled && g_relApplied,
              "guild/group relationship boundary exact order");
    } else if(sc=="handler_loaded" || sc=="handler_missing" || sc=="handler_wrong" || sc=="handler_leader" || sc=="handler_success"){
        WorldSession session; WorldPackets::Character::CharDelete packet; packet.guid=guid;
        if(sc=="handler_loaded") sObjectAccessor.loaded=true;
        if(sc=="handler_missing") sObjectMgr.hasCache=false;
        if(sc=="handler_leader") sGuildMgr.leader=true;
        if(sc=="handler_wrong") sObjectMgr.cache.uiAccount=9999;
        session.HandleCharDeleteOpcode(packet);
        if(sc=="handler_success")
        {
            std::vector<std::string> expected={"builder","relationships","execute","corpse",
                "relationships_apply","cache_delete","realm_count"};
            check(session.sent==1 && session.last==CHAR_DELETE_SUCCESS && g_execCalls==1 &&
                  g_events==expected,
                  "handler success exact completion order and response");
        }
        else if(sc=="handler_leader")
            check(session.sent==1 && session.last==CHAR_DELETE_FAILED &&
                  !g_softCalled && !g_permCalled && g_execCalls==0 && sWorld.countCalls==0,
                  "guild leader exact failure/no deletion");
        else
            check(session.sent==0 && !g_softCalled && !g_permCalled &&
                  g_execCalls==0 && sWorld.countCalls==0, "handler silent rejection/no effects");
    } else { std::cerr<<"unknown\n"; return 2; }
    if(failures){std::cerr<<"failures="<<failures<<"\n";return 1;}
    std::cout<<"deletion "<<sc<<": PASS\n"; return 0;
}
'''


@unittest.skipUnless(shutil.which("cmake"), "cmake not available")
class CharacterDeletionExecutionTests(unittest.TestCase):
    def test_actual_deletion_caller(self):
        player_src = PLAYER.read_text(encoding="utf-8")
        handler_src = HANDLER.read_text(encoding="utf-8")
        delete_body = extract(player_src, "bool Player::DeleteFromDB")
        parse_body = extract(player_src, "static bool ParseDeletionCount")
        handler = extract(handler_src, "void WorldSession::HandleCharDeleteOpcode")
        program = PREAMBLE + "\n" + delete_body + "\n" + parse_body + "\n" + handler + "\n" + MAIN
        with tempfile.TemporaryDirectory(prefix="fn05h-v62-delete-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(delete_exec CXX)\n"
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
            exe = build / "Release" / "probe.exe"
            if not exe.exists(): exe = build / "probe"
            failures=[]
            for scenario in ["soft_success","permanent_success","permanent_cod","tombstone",
                             "supplied_mismatch","assembly_failure","relationship_assembly_failure",
                             "known_failure","unknown",
                             "count_failure","borrowed","deletion_kind_count","parse_counts","zero_guid","zero_account","wrong_account_borrowed","guild_group_success","supplied_guid_mismatch","supplied_valid","identity_query_null","identity_missing_row","identity_wrong_account","generation_zero","generation_max","cod_query_failure","cod_missing_row","cod_null","cod_malformed","busy_account","foreign_lease","soft_unknown","permanent_unknown","permanent_assembly_failure","permanent_known_failure","incomplete_stage","deletion_kind_no_count","handler_execute_failure","handler_count_failure","handler_loaded",
                             "handler_missing","handler_wrong","handler_leader","handler_success"]:
                r=subprocess.run([str(exe),scenario],text=True,capture_output=True,timeout=30)
                if r.returncode!=0 or ("deletion "+scenario+": PASS") not in r.stdout:
                    failures.append(scenario+"\n"+r.stdout+r.stderr)
            self.assertFalse(failures,"\n".join(failures))


if __name__ == "__main__":
    unittest.main()
