"""Execution evidence for composed auction operations.

The real Append/Process bodies are extracted from AuctionHouseMgr.cpp and run
against a fake context that records staged calls and commit publication.  This
proves that mails, Item transfer, gold and the auction delete are one unit and
that caller-visible state changes only on commit.
"""

import pathlib
import subprocess
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "src/game/AuctionHouse/AuctionHouseMgr.cpp").read_text(encoding="utf-8")


def extract():
    start = SOURCE.index("namespace\n{\nvoid AddAuctionAccount")
    end = SOURCE.index("AuctionHouseObject* AuctionHouseMgr::MakeNewAuctionHouseObject()")
    return SOURCE[start:end]


PREAMBLE = r'''
#include <cstdint>
#include <functional>
#include <initializer_list>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

using uint8 = unsigned char;
using uint16 = unsigned short;
using uint32 = unsigned int;
using uint64 = unsigned long long;
using int32 = int;
using int16 = short;
using time_t = long long;

enum { HIGHGUID_PLAYER = 0 };
enum { LOG_BASIC = 0, LOG_LVL_ERROR = 1, LOG_LVL_DEBUG = 2 };
enum MailCheckMask { MAIL_CHECK_MASK_COPIED = 1 };
enum { AUCTION_WON = 1, AUCTION_SUCCESSFUL = 2, AUCTION_EXPIRED = 3,
       AUCTION_OUTBIDDED = 4, AUCTION_CANCELLED_TO_BIDDER = 5,
       AUCTION_CANCELED = 6 };

struct ObjectGuid {
    uint32 value = 0;
    ObjectGuid() = default;
    ObjectGuid(int, uint32 v) : value(v) {}
    uint32 GetCounter() const { return value; }
};

struct ItemProto { char const* Name1 = "Item"; };
struct Item {
    uint32 guid = 0;
    uint32 entry = 0;
    uint32 count = 1;
    ItemProto proto;
    uint32 GetGUIDLow() const { return guid; }
    uint32 GetEntry() const { return entry; }
    uint32 GetCount() const { return count; }
    ItemProto const* GetProto() const { return &proto; }
};

struct CharacterIdentitySnapshot {
    uint32 guid = 0;
    uint32 accountId = 0;
    std::string canonicalName;
    uint64 generation = 0;
};

struct Database {};
Database CharacterDatabase;

enum class MailPersistenceStatus { Committed, Rejected, OutcomeUnknown };
enum class CharacterRecoveryKind { WholeSave };
struct CharacterRecoveryRecord {
    CharacterRecoveryKind kind = CharacterRecoveryKind::WholeSave;
    std::vector<uint32> guids;
    std::vector<uint32> accountIds;
    uint64 expectedGeneration = 0;
    std::string detail;
};

MailPersistenceStatus g_status = MailPersistenceStatus::Committed;
bool g_sendOk = true;
bool g_deleteOk = true;
bool g_admit = true;
uint32 g_goldProjected = 0;
int g_sends = 0;
int g_deletes = 0;
bool g_itemRemoved = false;
std::vector<CharacterRecoveryRecord> g_recovery;
bool g_admissionHeld = false;
bool g_blockHeld = false;
Item g_item{77, 100, 3};

bool BlockCharacterRecovery(CharacterRecoveryRecord const& record) {
    g_recovery.push_back(record);
    g_blockHeld = g_admissionHeld;
    return true;
}

struct CharacterPersistenceContext;
struct SqlBatchResult {};

struct AuctionEntry {
    uint32 Id = 0;
    uint32 owner = 0;
    uint32 bidder = 0;
    uint32 bid = 0;
    uint32 buyout = 0;
    uint32 deposit = 0;
    uint32 itemTemplate = 0;
    uint32 itemGuidLow = 0;
    bool recoveryBlocked = false;
    uint32 GetAuctionCut() const { return 10; }
    bool AppendDeleteFromDB(CharacterPersistenceContext& context);
};

class Player;

struct MailReceiver {
    Player* player = nullptr;
    ObjectGuid guid;
    MailReceiver(Player* p, ObjectGuid g) : player(p), guid(g) {}
    explicit MailReceiver(ObjectGuid g) : guid(g) {}
};
struct MailSender {
    AuctionEntry* auction = nullptr;
    MailSender(AuctionEntry* a) : auction(a) {}
};

struct WorldSession {
    uint32 GetAccountId() const { return 42; }
    uint32 GetSecurity() const { return 0; }
    void SendAuctionBidderNotification(AuctionEntry*, bool) {}
    void SendAuctionOwnerNotification(AuctionEntry*, bool) {}
    void SendAuctionRemovedNotification(AuctionEntry*) {}
};

class Player {
public:
    Player() : guid(500), session(new WorldSession()) {}
    ~Player() { delete session; }
    uint32 GetGUIDLow() const { return guid; }
    ObjectGuid GetObjectGuid() const { return ObjectGuid(HIGHGUID_PLAYER, guid); }
    WorldSession* GetSession() const { return session; }
    char const* GetName() const { return "Auctioner"; }
    uint64 GetIdentityGeneration() const { return 7; }
    uint32 GetMoney() const { return money; }
    void ModifyMoney(int32 delta) { money = uint32(int32(money) + delta); }
    void LogModifyMoney(int32 delta, char const*, ObjectGuid, uint32) {
        money = uint32(int32(money) + delta);
        ++logs;
    }
    bool AppendGoldProjectedToDB(CharacterPersistenceContext&, uint32 projected);
    void AppendIdentityGenerationPublication(CharacterPersistenceContext&) {}
    uint32 guid;
    WorldSession* session;
    uint32 money = 10000;
    int logs = 0;
};

struct CharacterPersistenceContext {
    CharacterPersistenceContext(Database&, uint32) {}
    bool AppendIdentityGuards(std::initializer_list<CharacterIdentitySnapshot>) { return true; }
    void OnCommit(std::function<void()> action) { actions.push_back(std::move(action)); }
    SqlBatchResult Execute();
    std::vector<std::function<void()>> actions;
    static int executions;
};
int CharacterPersistenceContext::executions = 0;
SqlBatchResult CharacterPersistenceContext::Execute() {
    ++executions;
    if (g_status != MailPersistenceStatus::Committed)
        return {};
    for (auto& action : actions)
        action();
    return {};
}

bool AuctionEntry::AppendDeleteFromDB(CharacterPersistenceContext&) {
    ++g_deletes;
    return g_deleteOk;
}
bool Player::AppendGoldProjectedToDB(CharacterPersistenceContext&, uint32 projected) {
    g_goldProjected = projected;
    return true;
}

struct MailDraft {
    std::string subject;
    std::string body;
    Item* item = nullptr;
    uint32 money = 0;
    MailDraft(std::string s, std::string b = "") : subject(std::move(s)), body(std::move(b)) {}
    MailDraft& AddItem(Item* value) { item = value; return *this; }
    MailDraft& SetMoney(uint32 value) { money = value; return *this; }
    bool AppendSendTo(CharacterPersistenceContext&, MailReceiver const&, MailSender const&,
                      MailCheckMask, uint32, uint32, uint32& mailId,
                      std::function<void()> = nullptr);
};
MailPersistenceStatus ClassifyMailBatch(SqlBatchResult const&) { return g_status; }
bool MailDraft::AppendSendTo(CharacterPersistenceContext&, MailReceiver const&, MailSender const&,
                             MailCheckMask, uint32, uint32, uint32& mailId,
                             std::function<void()>) {
    if (!g_sendOk) return false;
    ++g_sends;
    mailId = 100 + g_sends;
    return true;
}

struct AuctionHouseMgr {
    Item* GetAItem(uint32 id) { return id == g_item.guid ? &g_item : nullptr; }
    bool RemoveAItem(uint32 id) { if (id == g_item.guid) g_itemRemoved = true; return true; }
    bool AppendAuctionWonMail(CharacterPersistenceContext&, AuctionEntry*, uint32, uint32);
    bool AppendAuctionSuccessfulMail(CharacterPersistenceContext&, AuctionEntry*, uint32, uint32);
    bool AppendAuctionExpiredMail(CharacterPersistenceContext&, AuctionEntry*);
    bool AppendAuctionOutbiddedMail(CharacterPersistenceContext&, AuctionEntry*, uint32, uint32);
    bool AppendAuctionCancelledToBidderMail(CharacterPersistenceContext&, AuctionEntry*, uint32, uint32);
    bool AppendAuctionCancelledOwnerMail(CharacterPersistenceContext&, AuctionEntry*);
    MailPersistenceStatus ProcessExpiredAuction(AuctionEntry*);
    MailPersistenceStatus ProcessBuyoutAuction(AuctionEntry*, Player*, int32, uint32, uint32);
    MailPersistenceStatus ProcessCancelAuction(AuctionEntry*, Player*, uint32);
    MailPersistenceStatus ProcessOrphanedAuction(AuctionEntry*);
};
AuctionHouseMgr sAuctionMgr;

struct ObjectMgrStub {
    Player* player = nullptr;
    Player* GetPlayer(ObjectGuid) { return player; }
    uint32 GetPlayerAccountIdByGUID(ObjectGuid) { return 42; }
} sObjectMgr;

struct PlayerTransactionData { std::string type; };
struct WorldStub { void LogTransaction(PlayerTransactionData const&) {} } sWorld;
struct LogStub { template<typename... A> void Out(A&&...) {} } sLog;

enum class AccountLifecycleKind { Mutation, Deletion };
class AccountLifecycleLease;
struct CharacterPersistenceAdmission {
    bool Admit(std::vector<uint32> const&, AccountLifecycleKind,
               AccountLifecycleLease const* = nullptr) {
        if (!g_admit)
            return false;
        g_admissionHeld = true;
        return true;
    }
    ~CharacterPersistenceAdmission() { g_admissionHeld = false; }
};

'''

MAIN = r'''
namespace {
int failures = 0;
void check(bool ok, std::string const& message) {
    if (!ok) { std::cerr << "FAIL " << message << "\n"; ++failures; }
}

void reset() {
    CharacterPersistenceContext::executions = 0;
    g_status = MailPersistenceStatus::Committed;
    g_sendOk = true;
    g_deleteOk = true;
    g_admit = true;
    g_goldProjected = 0;
    g_sends = 0;
    g_deletes = 0;
    g_itemRemoved = false;
    g_recovery.clear();
    g_admissionHeld = false;
    g_blockHeld = false;
}
AuctionEntry makeAuction(uint32 bidder) {
    AuctionEntry a;
    a.Id = 9; a.owner = 600; a.bidder = bidder; a.bid = 900;
    a.buyout = 1000; a.deposit = 50; a.itemTemplate = 100; a.itemGuidLow = 77;
    return a;
}
}

int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";
    if (scenario == "expiry_no_bidder") {
        reset();
        AuctionEntry a = makeAuction(0);
        MailPersistenceStatus s = sAuctionMgr.ProcessExpiredAuction(&a);
        check(s == MailPersistenceStatus::Committed, "committed");
        check(CharacterPersistenceContext::executions == 1, "one execution");
        check(g_sends == 1 && g_deletes == 1, "item mail and auction delete staged");
        check(g_itemRemoved, "item cache removed after commit");
    }
    else if (scenario == "expiry_sale") {
        reset();
        AuctionEntry a = makeAuction(700);
        MailPersistenceStatus s = sAuctionMgr.ProcessExpiredAuction(&a);
        check(s == MailPersistenceStatus::Committed, "committed");
        check(CharacterPersistenceContext::executions == 1, "one execution");
        check(g_sends == 2 && g_deletes == 1, "both mails and delete in one unit");
        check(g_itemRemoved, "item cache removed after commit");
    }
    else if (scenario == "expiry_failure") {
        reset();
        g_sendOk = false;
        AuctionEntry a = makeAuction(700);
        MailPersistenceStatus s = sAuctionMgr.ProcessExpiredAuction(&a);
        check(s == MailPersistenceStatus::Rejected, "rejected");
        check(CharacterPersistenceContext::executions == 0, "no execution");
        check(g_sends == 0 && g_deletes == 0, "nothing published");
        check(!g_itemRemoved, "item cache retained");
    }
    else if (scenario == "expiry_unknown") {
        reset();
        g_status = MailPersistenceStatus::OutcomeUnknown;
        AuctionEntry a = makeAuction(700);
        MailPersistenceStatus s = sAuctionMgr.ProcessExpiredAuction(&a);
        check(s == MailPersistenceStatus::OutcomeUnknown, "unknown");
        check(CharacterPersistenceContext::executions == 1, "one execution");
        check(g_recovery.size() == 1 && g_recovery[0].guids.size() == 2,
              "all participants blocked");
        check(g_blockHeld, "expiry recovery registered before admission release");
        check(a.recoveryBlocked, "uncertain expiry cannot be retried");
    }
    else if (scenario == "orphan_unknown") {
        reset();
        g_status = MailPersistenceStatus::OutcomeUnknown;
        AuctionEntry a = makeAuction(0);
        MailPersistenceStatus s = sAuctionMgr.ProcessOrphanedAuction(&a);
        check(s == MailPersistenceStatus::OutcomeUnknown, "orphan unknown");
        check(g_recovery.size() == 1 && g_recovery[0].guids.size() == 1,
              "orphan owner blocked");
        check(g_blockHeld, "orphan recovery registered before admission release");
        check(a.recoveryBlocked, "uncertain orphan cannot be retried");
    }
    else if (scenario == "buyout") {
        reset();
        Player buyer;
        AuctionEntry a = makeAuction(700);
        MailPersistenceStatus s = sAuctionMgr.ProcessBuyoutAuction(&a, &buyer, 1000, 700, 900);
        check(s == MailPersistenceStatus::Committed, "committed");
        check(CharacterPersistenceContext::executions == 1, "one execution");
        check(g_sends == 3 && g_deletes == 1, "refund + two mails + delete");
        check(g_goldProjected == 9000, "buyer balance staged");
        check(buyer.logs == 1 && buyer.money == 9000, "money published on commit");
        check(a.bidder == 500 && a.bid == 1000, "bid fields published");
        check(g_itemRemoved, "item cache removed");
    }
    else if (scenario == "buyout_failure") {
        reset();
        g_sendOk = false;
        Player buyer;
        AuctionEntry a = makeAuction(700);
        MailPersistenceStatus s = sAuctionMgr.ProcessBuyoutAuction(&a, &buyer, 1000, 700, 900);
        check(s == MailPersistenceStatus::Rejected, "rejected");
        check(CharacterPersistenceContext::executions == 0, "no execution");
        check(buyer.money == 10000 && a.bidder == 700, "no local mutation");
        check(!g_itemRemoved && g_deletes == 0, "listing retained");
    }
    else if (scenario == "cancel") {
        reset();
        Player owner;
        AuctionEntry a = makeAuction(700);
        MailPersistenceStatus s = sAuctionMgr.ProcessCancelAuction(&a, &owner, 10);
        check(s == MailPersistenceStatus::Committed, "committed");
        check(CharacterPersistenceContext::executions == 1, "one execution");
        check(g_sends == 2 && g_deletes == 1, "refund + owner item + delete");
        check(g_goldProjected == 9990, "cut staged");
        check(owner.money == 9990, "cut published on commit");
        check(g_itemRemoved, "item cache removed");
    }
    else if (scenario == "cancel_failure") {
        reset();
        g_deleteOk = false;
        Player owner;
        AuctionEntry a = makeAuction(700);
        MailPersistenceStatus s = sAuctionMgr.ProcessCancelAuction(&a, &owner, 10);
        check(s == MailPersistenceStatus::Rejected, "rejected");
        check(CharacterPersistenceContext::executions == 0, "no execution");
        check(owner.money == 10000, "no cut");
    }
    else { std::cerr << "unknown\n"; return 2; }
    return failures ? 1 : 0;
}
'''


class AuctionCompositionExecutionTests(unittest.TestCase):
    def test_composed_auction_operations(self):
        with tempfile.TemporaryDirectory(prefix="fn05h-auction-") as td:
            work = pathlib.Path(td)
            body = extract()
            program = PREAMBLE + "\n" + body + "\n" + MAIN
            # PlayerTransactionData is referenced by sWorld before its definition;
            # make the stub opaque instead.
            program = program.replace(
                "struct WorldStub { void LogTransaction(PlayerTransactionData const&) {} } sWorld;",
                "struct WorldStub { template<typename T> void LogTransaction(T const&) {} } sWorld;")
            (work / "probe.cpp").write_text(program, encoding="utf-8")
            (work / "CMakeLists.txt").write_text(textwrap.dedent("""
                cmake_minimum_required(VERSION 3.20)
                project(auction_composition_probe CXX)
                add_executable(probe probe.cpp)
                set_property(TARGET probe PROPERTY CXX_STANDARD 14)
                target_compile_definitions(probe PRIVATE _CRT_SECURE_NO_WARNINGS)
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
            failures = []
            for scenario in ["expiry_no_bidder", "expiry_sale", "expiry_failure",
                             "expiry_unknown", "orphan_unknown",
                             "buyout", "buyout_failure",
                             "cancel", "cancel_failure"]:
                run = subprocess.run([str(exe), scenario], stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True)
                if run.returncode != 0:
                    failures.append(scenario + "\n" + run.stdout)
            self.assertFalse(failures, "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
