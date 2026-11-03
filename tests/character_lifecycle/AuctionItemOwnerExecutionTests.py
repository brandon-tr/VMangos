"""Execution evidence that auction Items load with their persisted owner."""

import pathlib
import subprocess
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "src/game/AuctionHouse/AuctionHouseMgr.cpp").read_text(encoding="utf-8")


def extract():
    start = SOURCE.index("void AuctionHouseMgr::LoadAuctionItems()")
    end = SOURCE.index("void AuctionHouseMgr::LoadAuctions()")
    return SOURCE[start:end]


PREAMBLE = r'''
#include <cstdint>
#include <iostream>
#include <map>
#include <memory>
#include <string>
#include <vector>

using uint8 = unsigned char;
using uint32 = unsigned int;
using uint64 = unsigned long long;

enum { HIGHGUID_PLAYER = 0 };
enum { LOG_BASIC = 0, LOG_LVL_MINIMAL = 1, LOG_LVL_ERROR = 2 };

struct ObjectGuid {
    uint32 value = 0;
    ObjectGuid() = default;
    ObjectGuid(int, uint32 v) : value(v) {}
    uint32 GetCounter() const { return value; }
};

struct Field {
    uint32 value = 0;
    uint32 GetUInt32() const { return value; }
};

struct QueryResult {
    std::vector<std::vector<Field>> rows;
    std::size_t index = 0;
    std::size_t GetRowCount() const { return rows.size(); }
    Field* Fetch() {
        if (index >= rows.size() || rows[index].empty()) return nullptr;
        return rows[index].data();
    }
    bool NextRow() {
        if (index + 1 >= rows.size()) return false;
        ++index;
        return true;
    }
};

uint32 g_loadedOwner = 0;
uint32 g_loadedGuid = 0;
int g_deleted = 0;
int g_added = 0;

struct ItemPrototype { uint32 id = 100; };
struct Item {
    uint32 guid = 0;
    uint32 entry = 0;
    uint32 GetGUIDLow() const { return guid; }
    uint32 GetEntry() const { return entry; }
    bool LoadFromDB(uint32 itemGuid, ObjectGuid owner, Field*, uint32 itemId) {
        guid = itemGuid;
        entry = itemId;
        g_loadedOwner = owner.GetCounter();
        g_loadedGuid = itemGuid;
        return true;
    }
    ~Item() { ++g_deleted; }
};
Item* NewItemOrBag(ItemPrototype const*) { return new Item(); }

struct ObjectMgrStub {
    ItemPrototype proto;
    ItemPrototype const* GetItemPrototype(uint32) { return &proto; }
} sObjectMgr;

struct BarGoLink {
    explicit BarGoLink(std::size_t) {}
    void step() {}
};
struct LogStub { template<typename... A> void Out(A&&...) {} } sLog;

struct Database {
    std::unique_ptr<QueryResult> result;
    std::unique_ptr<QueryResult> Query(char const*) {
        return std::move(result);
    }
} CharacterDatabase;

struct AuctionHouseMgr {
    void AddAItem(Item*) { ++g_added; }
    void LoadAuctionItems();
};
AuctionHouseMgr sAuctionMgr;
'''

MAIN = r'''
int main() {
    auto result = std::make_unique<QueryResult>();
    std::vector<Field> row(13);
    for (uint32 i = 0; i < row.size(); ++i) row[i].value = i + 1;
    row[10].value = 77;       // item guid
    row[11].value = 100;      // item id
    row[12].value = 600;      // persisted owner
    result->rows.push_back(row);
    CharacterDatabase.result = std::move(result);
    sAuctionMgr.LoadAuctionItems();
    if (g_loadedGuid != 77 || g_loadedOwner != 600 || g_added != 1) {
        std::cerr << "loaded guid=" << g_loadedGuid << " owner=" << g_loadedOwner
                  << " added=" << g_added << "\n";
        return 1;
    }
    std::cout << "auction item owner: PASS\n";
    return 0;
}
'''


class AuctionItemOwnerExecutionTests(unittest.TestCase):
    def test_loaded_item_has_persisted_owner(self):
        with tempfile.TemporaryDirectory(prefix="fn05h-auction-owner-") as td:
            work = pathlib.Path(td)
            (work / "probe.cpp").write_text(
                PREAMBLE + "\n" + extract() + "\n" + MAIN, encoding="utf-8")
            (work / "CMakeLists.txt").write_text(textwrap.dedent("""
                cmake_minimum_required(VERSION 3.20)
                project(auction_owner_probe CXX)
                add_executable(probe probe.cpp)
                set_property(TARGET probe PROPERTY CXX_STANDARD 14)
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
            run = subprocess.run([str(exe)], stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
            self.assertEqual(run.returncode, 0, run.stdout)


if __name__ == "__main__":
    unittest.main()
