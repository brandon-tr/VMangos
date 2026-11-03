#!/usr/bin/env python3
"""Executable regression for the expired-mail returner defects.

The real SingleMailReturner / OldMailsReturner callback bodies are extracted from
ObjectMgr.cpp and compiled against recording stubs.  This exercises the actual
control flow: every mail_items row must be handled, item_text_id must survive the
expiry query, COD/returned mail must not be returned, and no legacy SQL may be
submitted from the callback.  Database execution itself is stubbed and disclosed
as such.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OBJECT_MGR = ROOT / "src/game/ObjectMgr.cpp"

PREAMBLE = r'''
#include <iostream>
#include <memory>
#include <string>
#include <vector>

using uint32 = unsigned int;
using uint64 = unsigned long long;
using int16 = signed short;
using uint8 = unsigned char;

enum { HIGHGUID_PLAYER = 0 };
enum { MAIL_NORMAL = 0, MAIL_CREATURE = 3 };
enum { MAIL_CHECK_MASK_RETURNED = 0x02, MAIL_CHECK_MASK_COD_PAYMENT = 0x08 };
enum { LOG_BASIC = 0, LOG_LVL_ERROR = 0, LOG_LVL_MINIMAL = 0 };

struct ObjectGuid {
    uint32 value = 0;
    ObjectGuid() {}
    ObjectGuid(int, uint32 v) : value(v) {}
    uint32 GetCounter() const { return value; }
};

struct Field {
    uint32 u = 0;
    uint32 GetUInt32() const { return u; }
    uint8 GetUInt8() const { return uint8(u); }
    int16 GetInt16() const { return int16(u); }
    bool GetBool() const { return u != 0; }
    uint64 GetUInt64() const { return u; }
};

struct QueryResult {
    std::vector<std::vector<Field>> rows;
    std::size_t index = 0;
    Field* Fetch() { return rows.empty() ? nullptr : rows[index].data(); }
    bool NextRow() { if (index + 1 >= rows.size()) return false; ++index; return true; }
    std::size_t GetRowCount() const { return rows.size(); }
};

struct MailItemInfo {
    uint32 itemGuid = 0;
    uint32 itemId = 0;
};

struct Mail {
    uint32 messageID = 0;
    uint8 messageType = 0;
    uint32 sender = 0;
    ObjectGuid receiverGuid;
    uint32 itemTextId = 0;
    bool has_items = false;
    time_t expire_time = 0;
    time_t deliver_time = 0;
    uint32 money = 0;
    uint32 COD = 0;
    uint32 checked = 0;
    int16 mailTemplateId = 0;
};

enum class MailPersistenceStatus { Committed, Rejected, OutcomeUnknown };

int g_blockedExpiry = 0;
void BlockExpiredMailFailure(uint32, uint32, ObjectGuid, char const*) {
    ++g_blockedExpiry;
}

struct MailCall {
    std::vector<uint32> attachments;
    bool returnToSender = false;
    uint32 itemTextId = 0;
};
std::vector<MailCall> g_calls;
int g_dbWrites = 0;
int g_asyncQueries = 0;
bool g_invokeReturner = false;
bool g_returnerNull = false;
std::vector<std::vector<Field>> g_returnerRows;

MailPersistenceStatus g_processStatus = MailPersistenceStatus::Committed;
MailPersistenceStatus ProcessExpiredMail(Mail const& mail,
                                        std::vector<MailItemInfo> const& attachments,
                                        bool returnToSender, time_t) {
    MailCall call;
    for (auto const& attachment : attachments)
        call.attachments.push_back(attachment.itemGuid);
    call.returnToSender = returnToSender;
    call.itemTextId = mail.itemTextId;
    g_calls.push_back(call);
    if (g_processStatus == MailPersistenceStatus::OutcomeUnknown)
        ++g_blockedExpiry;   // real ProcessExpiredMail blocks under admission
    return g_processStatus;
}

struct ObjectAccessor {
    bool online = false;
    bool FindPlayerNotInWorld(ObjectGuid) const { return online; }
} sObjectAccessor;

struct Log {
    template<typename... A> void Out(A&&...) {}
} sLog;

struct ObjectMgrStub {
    void ResetOldMailCounter() {}
    void IncrementOldMailCounter(uint32) {}
} sObjectMgr;

struct Database {
    template<class C, class... A>
    bool AsyncPQueryUnsafe(C* object,
                           void (C::*method)(std::unique_ptr<QueryResult>),
                           char const*, A...) {
        ++g_asyncQueries;
        if (g_returnerNull)
            (object->*method)(nullptr);
        else if (g_invokeReturner) {
            auto result = std::make_unique<QueryResult>();
            result->rows = g_returnerRows;
            (object->*method)(std::move(result));
        }
        return true;
    }
    template<typename... A> bool PExecute(A...) { ++g_dbWrites; return true; }
} CharacterDatabase;
'''

MAIN = r'''
static int failures = 0;
static void check(bool value, std::string const& label) {
    if (!value) { std::cerr << "FAIL " << label << "\n"; ++failures; }
}

static std::unique_ptr<QueryResult> rows(std::vector<std::vector<uint32>> const& values) {
    auto result = std::make_unique<QueryResult>();
    for (auto const& row : values) {
        std::vector<Field> fields;
        for (uint32 value : row) { Field field; field.u = value; fields.push_back(field); }
        result->rows.push_back(fields);
    }
    return result;
}

static std::unique_ptr<QueryResult> oldMailRow(uint32 id, uint32 messageType,
                                               uint32 sender, uint32 receiver,
                                               uint32 itemTextId, uint32 hasItems,
                                               uint32 cod, uint32 checked) {
    auto result = rows({{id, messageType, sender, receiver, itemTextId,
                         hasItems, 1, cod, checked, 0}});
    return result;
}

int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";

    if (scenario == "single_all_attachments") {
        SingleMailReturner* returner = new SingleMailReturner();
        returner->returnToLowGuid = 500;
        returner->messageID = 9;
        returner->receiverGuid = ObjectGuid(HIGHGUID_PLAYER, 600);
        returner->sender = 500;
        returner->itemTextId = 77;
        returner->basetime = 1000;
        returner->Callback(rows({{11}, {22}, {33}}));
        check(g_calls.size() == 1, "one processed mail");
        check(g_calls[0].attachments == std::vector<uint32>({11, 22, 33}),
              "all attachments handled");
        check(g_calls[0].returnToSender && g_calls[0].itemTextId == 77,
              "return flag and body id");
        check(g_dbWrites == 0, "no legacy SQL");
    }
    else if (scenario == "single_dispose_all") {
        SingleMailReturner* returner = new SingleMailReturner();
        returner->returnToLowGuid = 0;
        returner->messageID = 10;
        returner->receiverGuid = ObjectGuid(HIGHGUID_PLAYER, 601);
        returner->sender = 0;
        returner->itemTextId = 78;
        returner->basetime = 1000;
        returner->Callback(rows({{41}, {42}}));
        check(g_calls.size() == 1 && !g_calls[0].returnToSender,
              "disposal path");
        check(g_calls[0].attachments == std::vector<uint32>({41, 42}),
              "all disposed attachments");
        check(g_calls[0].itemTextId == 78, "disposal body id retained");
        check(g_dbWrites == 0, "no legacy SQL");
    }
    else if (scenario == "single_online_skip") {
        sObjectAccessor.online = true;
        SingleMailReturner* returner = new SingleMailReturner();
        returner->returnToLowGuid = 500;
        returner->receiverGuid = ObjectGuid(HIGHGUID_PLAYER, 602);
        returner->Callback(rows({{51}}));
        check(g_calls.empty(), "online receiver skipped");
        check(g_dbWrites == 0, "no legacy SQL");
    }
    else if (scenario == "single_no_rows") {
        SingleMailReturner* returner = new SingleMailReturner();
        returner->returnToLowGuid = 500;
        returner->messageID = 11;
        returner->receiverGuid = ObjectGuid(HIGHGUID_PLAYER, 603);
        returner->sender = 500;
        returner->itemTextId = 79;
        returner->basetime = 1000;
        returner->Callback(rows({}));
        check(g_calls.empty() && g_blockedExpiry == 1,
              "empty/malformed result set is blocked, not treated as zero rows");
    }
    else if (scenario == "single_zero_attachments") {
        SingleMailReturner* returner = new SingleMailReturner();
        returner->returnToLowGuid = 500;
        returner->messageID = 12;
        returner->receiverGuid = ObjectGuid(HIGHGUID_PLAYER, 604);
        returner->sender = 500;
        returner->itemTextId = 79;
        returner->basetime = 1000;
        returner->hasItems = true;
        returner->Callback(rows({{0}}));
        check(g_calls.size() == 1 && g_calls[0].attachments.empty(),
              "authoritative left-join NULL row is zero attachments");
        check(g_calls[0].itemTextId == 79 && g_blockedExpiry == 0,
              "zero-attachment evidence processed without blocking");
    }
    else if (scenario == "old_attachment_query_failure_blocks") {
        g_returnerNull = true;
        OldMailsReturner* returner = new OldMailsReturner();
        returner->serverUp = false;
        returner->basetime = 2000;
        returner->Callback(oldMailRow(24, MAIL_NORMAL, 703, 803, 84, 1, 0, 0));
        check(g_calls.empty() && g_blockedExpiry == 1,
              "attachment enumeration failure blocks destructive expiry work");
        check(g_dbWrites == 0, "no legacy SQL");
    }
    else if (scenario == "old_unknown_blocks") {
        g_invokeReturner = true;
        g_returnerRows = {{{91}}};
        g_processStatus = MailPersistenceStatus::OutcomeUnknown;
        OldMailsReturner* returner = new OldMailsReturner();
        returner->serverUp = false;
        returner->basetime = 2000;
        returner->Callback(oldMailRow(25, MAIL_NORMAL, 704, 804, 85, 1, 0, 0));
        check(g_calls.size() == 1 && g_blockedExpiry == 1,
              "unknown expiry outcome blocks affected participants");
    }
    else if (scenario == "old_return_all_items") {
        g_invokeReturner = true;
        g_returnerRows = {{{61}}, {{62}}, {{63}}};
        OldMailsReturner* returner = new OldMailsReturner();
        returner->serverUp = false;
        returner->basetime = 2000;
        returner->Callback(oldMailRow(21, MAIL_NORMAL, 700, 800, 81, 1, 0, 0));
        check(g_calls.size() == 1 && g_calls[0].attachments == std::vector<uint32>({61, 62, 63}),
              "expiry return handles every row");
        check(g_calls[0].returnToSender && g_calls[0].itemTextId == 81,
              "expiry return flag/body");
        check(g_asyncQueries == 1, "one attachment query");
        check(g_dbWrites == 0, "no legacy SQL");
    }
    else if (scenario == "old_cod_disposes_all") {
        g_invokeReturner = true;
        g_returnerRows = {{{71}}, {{72}}};
        OldMailsReturner* returner = new OldMailsReturner();
        returner->serverUp = false;
        returner->basetime = 2000;
        returner->Callback(oldMailRow(22, MAIL_NORMAL, 701, 801, 82, 1, 0,
                                      MAIL_CHECK_MASK_COD_PAYMENT));
        check(g_calls.size() == 1 && g_calls[0].attachments == std::vector<uint32>({71, 72}),
              "COD mail disposes all attachments");
        check(!g_calls[0].returnToSender, "COD payment mail not returned");
        check(g_dbWrites == 0, "no legacy SQL");
    }
    else if (scenario == "old_empty_mail_body") {
        OldMailsReturner* returner = new OldMailsReturner();
        returner->serverUp = false;
        returner->basetime = 2000;
        returner->Callback(oldMailRow(23, MAIL_NORMAL, 702, 802, 83, 0, 0, 0));
        check(g_calls.size() == 1 && g_calls[0].attachments.empty() &&
              !g_calls[0].returnToSender, "empty mail disposed");
        check(g_calls[0].itemTextId == 83, "selected body id retained");
        check(g_asyncQueries == 0, "no attachment query for empty mail");
        check(g_dbWrites == 0, "no legacy SQL");
    }
    else { std::cerr << "unknown\n"; return 2; }

    if (failures) { std::cerr << "failures=" << failures << "\n"; return 1; }
    std::cout << "expired-mail " << scenario << ": PASS\n";
    return 0;
}
'''


def extract(source):
    start = source.index("class SingleMailReturner")
    end = source.index("void ObjectMgr::ReturnOrDeleteOldMails")
    return source[start:end]


@unittest.skipUnless(shutil.which("cmake"), "cmake not available")
class ExpiredMailReturnExecutionTests(unittest.TestCase):
    def test_expired_mail_callbacks(self):
        body = extract(OBJECT_MGR.read_text(encoding="utf-8"))
        program = PREAMBLE + "\n" + body + "\n" + MAIN
        with tempfile.TemporaryDirectory(prefix="fn05h-mail-expiry-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(mail_expiry_exec CXX)\n"
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
            for scenario in ["single_all_attachments", "single_dispose_all",
                             "single_online_skip", "single_no_rows", "single_zero_attachments",
                             "old_attachment_query_failure_blocks", "old_unknown_blocks",
                             "old_return_all_items", "old_cod_disposes_all",
                             "old_empty_mail_body"]:
                r = subprocess.run([str(exe), scenario], text=True,
                                   capture_output=True, timeout=30)
                if r.returncode != 0 or ("expired-mail " + scenario + ": PASS") not in r.stdout:
                    failures.append(scenario + "\n" + r.stdout + r.stderr)
            self.assertFalse(failures, "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
