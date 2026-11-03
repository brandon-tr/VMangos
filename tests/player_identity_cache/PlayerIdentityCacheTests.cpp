#include "PlayerIdentityCache.h"
#include "Database/Database.h"
#include "Database/Field.h"
#include "Database/QueryResult.h"

#include <atomic>
#include <cstdint>
#include <iostream>
#include <limits>
#include <memory>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace
{
int checks = 0;
int failures = 0;

void check(bool condition, std::string const& label)
{
    ++checks;
    if (!condition)
    {
        ++failures;
        std::cerr << "FAIL " << label << '\n';
    }
}

struct CursorState
{
    std::atomic<int> nextRowCalls{0};
};

class FakeResult : public QueryResult
{
    public:
        FakeResult(std::uint64_t reportedRows,
                   std::vector<std::vector<std::string>> rows,
                   bool current = true,
                   std::shared_ptr<CursorState> state = {})
            : QueryResult(reportedRows,
                          rows.empty() ? 0u
                                       : static_cast<std::uint32_t>(rows[0].size())),
              m_rows(std::move(rows)),
              m_state(state ? std::move(state) : std::make_shared<CursorState>())
        {
            for (auto const& row : m_rows)
            {
                std::unique_ptr<Field[]> fields(new Field[row.size()]);
                for (std::size_t i = 0; i < row.size(); ++i)
                    fields[i].SetValue(row[i].c_str());
                m_fields.push_back(std::move(fields));
            }
            if (current && !m_fields.empty())
                mCurrentRow = m_fields[0].get();
        }

        explicit FakeResult(std::uint64_t reportedRows,
                            std::uint32_t fields,
                            bool current,
                            std::shared_ptr<CursorState> state = {})
            : QueryResult(reportedRows, fields),
              m_state(state ? std::move(state) : std::make_shared<CursorState>())
        {
            if (current)
                m_dummy.reset(new Field[fields]);
            if (current)
                mCurrentRow = m_dummy.get();
        }

        bool NextRow() override
        {
            ++m_state->nextRowCalls;
            if (m_rows.empty() || m_row + 1 >= m_rows.size())
            {
                mCurrentRow = nullptr;
                return false;
            }
            ++m_row;
            mCurrentRow = m_fields[m_row].get();
            return true;
        }

        std::shared_ptr<CursorState> State() const { return m_state; }

    private:
        std::vector<std::vector<std::string>> m_rows;
        std::vector<std::unique_ptr<Field[]>> m_fields;
        std::unique_ptr<Field[]> m_dummy;
        std::size_t m_row = 0;
        std::shared_ptr<CursorState> m_state;
};

class NullCountResult : public QueryResult
{
    public:
        explicit NullCountResult(std::shared_ptr<CursorState> state)
            : QueryResult(1, 1), m_state(std::move(state))
        {
            mCurrentRow = &m_field;
        }

        bool NextRow() override
        {
            ++m_state->nextRowCalls;
            mCurrentRow = nullptr;
            return false;
        }

    private:
        Field m_field;
        std::shared_ptr<CursorState> m_state;
};

class FakeDatabase : public Database
{
    public:
        void AddCount(std::uint64_t count)
        {
            AddCountText(std::to_string(count));
        }

        void AddCountText(std::string text)
        {
            auto state = std::make_shared<CursorState>();
            m_responses.push_back(std::unique_ptr<QueryResult>(
                new FakeResult(1, {{std::move(text)}}, true, state)));
            m_states.push_back(std::move(state));
        }

        void AddNullCount()
        {
            auto state = std::make_shared<CursorState>();
            m_responses.push_back(std::unique_ptr<QueryResult>(
                new NullCountResult(state)));
            m_states.push_back(std::move(state));
        }

        void AddData(std::vector<std::vector<std::string>> rows,
                     std::uint64_t reportedRows)
        {
            auto state = std::make_shared<CursorState>();
            m_responses.push_back(std::unique_ptr<QueryResult>(
                new FakeResult(reportedRows, std::move(rows), true, state)));
            m_states.push_back(std::move(state));
        }

        void AddRaw(std::unique_ptr<QueryResult> result,
                    std::shared_ptr<CursorState> state = {})
        {
            m_responses.push_back(std::move(result));
            m_states.push_back(state ? std::move(state)
                                     : std::make_shared<CursorState>());
        }

        std::unique_ptr<QueryResult> Query(std::string const& sql) override
        {
            sqls.push_back(sql);
            int const index = queries++;
            if (index == failAt || index >= static_cast<int>(m_responses.size()))
                return nullptr;
            return std::move(m_responses[static_cast<std::size_t>(index)]);
        }

        int queries = 0;
        int failAt = -1;
        std::vector<std::string> sqls;
        std::vector<std::shared_ptr<CursorState>> m_states;

    private:
        std::vector<std::unique_ptr<QueryResult>> m_responses;
};

bool reload(PlayerIdentityCache& cache, Database& database, uint32 guid = 0)
{
    std::string reason;
    return cache.Reload(database, guid, reason);
}

PlayerCacheData value(std::uint32_t guid, std::string name,
                      std::uint32_t account = 1)
{
    PlayerCacheData data;
    data.uiGuid = guid;
    data.sName = std::move(name);
    data.uiAccount = account;
    data.uiRace = 1;
    data.uiClass = 1;
    data.uiGender = 0;
    data.uiLevel = 10;
    data.uiZoneId = 12;
    data.uiMapId = 0;
    data.fPosX = 1.0f;
    data.fPosY = 2.0f;
    data.fPosZ = 3.0f;
    data.fOrientation = 4.0f;
    return data;
}

std::vector<std::string> row(std::uint32_t guid, std::string const& name,
                             std::uint32_t account = 1)
{
    return {std::to_string(guid), "1", "1", "0", std::to_string(account), name,
            "10", "12", "0", "1", "2", "3", "4", ""};
}

void snapshotSurvivesMutation()
{
    PlayerIdentityCache cache;
    PlayerCacheSnapshot original = cache.Upsert(value(1, "Alice"));
    check(!!original, "snapshot insert");
    cache.Erase(1);
    check(original->sName == "Alice", "snapshot survives erase");
    check(!cache.Find(1), "erased guid gone");
    std::weak_ptr<PlayerCacheData const> weak = original;
    original.reset();
    check(weak.expired(), "snapshot reclaimed after erase/release");

    PlayerCacheSnapshot old = cache.Upsert(value(2, "Bob"));
    cache.Rename(2, "Bob", "Robert");
    check(old->sName == "Bob", "snapshot survives rename");
    check(cache.FindName("Robert") && !cache.FindName("Bob"),
          "rename swaps indexes");
    cache.UpdatePosition(2, 1, 8, 9, 10, 11, true);
    check(old->uiMapId == 0 && old->sName == "Bob",
          "snapshot survives position update");
    cache.UpdateLevelAndZone(2, 60, 99);
    check(old->uiLevel == 10, "snapshot survives level update");
}

void readersSeeNewSnapshots()
{
    PlayerIdentityCache cache;
    PlayerCacheSnapshot old = cache.Upsert(value(1, "Alice"));
    PlayerCacheSnapshot updated = cache.Upsert(value(1, "Alice", 2));
    check(old->uiAccount == 1 && updated->uiAccount == 2,
          "retained reader sees old, new reader sees new");
    check(cache.Find(1)->uiAccount == 2, "new reader sees updated value");
}

void duplicateAndStaleOperationsRejected()
{
    PlayerIdentityCache cache;
    PlayerCacheSnapshot a = cache.Upsert(value(1, "Alice"));
    PlayerCacheSnapshot b = cache.Upsert(value(2, "Bob"));

    check(!cache.Upsert(value(3, "Alice")), "duplicate name rejected");
    check(cache.Find(1) == a && cache.Find(2) == b,
          "duplicate name leaves both owners unchanged");
    check(!cache.Rename(1, "Wrong", "Carol"), "stale rename rejected");
    check(cache.Find(1) == a, "stale rename leaves owner unchanged");
    check(!cache.Rename(1, "Alice", "Bob"), "duplicate rename destination rejected");
    check(cache.FindName("Bob") == b, "duplicate rename leaves destination unchanged");
    check(cache.Rename(1, "Alice", "Alice"), "same-name rename is idempotent");
}

void identityAndMissingUpdatesRejected()
{
    PlayerIdentityCache cache;
    cache.Upsert(value(1, "Alice"));
    PlayerCacheData mismatch = value(1, "Alice", 2);
    check(!cache.UpdateIfIdentityMatches(mismatch), "account mismatch rejected");
    mismatch.uiAccount = 1;
    mismatch.sName = "Alicia";
    check(!cache.UpdateIfIdentityMatches(mismatch), "name mismatch rejected");
    check(!cache.UpdateIfIdentityMatches(value(9, "Missing")),
          "missing identity update rejected");
    check(!cache.UpdatePosition(9, 1, 0, 0, 0, 0, false),
          "missing position update rejected");
    check(!cache.UpdateLevelAndZone(9, 1, 1),
          "missing level update rejected");
    check(cache.Size() == 1, "ordinary updates do not insert");
    check(cache.Find(1)->sName == "Alice", "identity unchanged");
}

void payloadAndReplacement()
{
    PlayerIdentityCache cache;
    cache.Upsert(value(1, "Alice"));
    cache.Upsert(value(2, "Bob"));
    cache.UpdatePosition(1, 5, 6, 7, 8, 9, true);
    PlayerCacheSnapshot updated = cache.Find(1);
    check(updated->uiMapId == 5 && updated->fPosX == 6 && updated->bInFlight,
          "position payload");
    check(updated->uiAccount == 1 && updated->sName == "Alice",
          "position preserves identity");

    std::vector<PlayerCacheData> replacement = {value(3, "Carol")};
    check(cache.ReplaceAll(std::move(replacement)), "replacement accepted");
    check(!cache.Find(1) && cache.Find(3), "replacement swaps both indexes");

    std::vector<PlayerCacheData> duplicateGuid = {value(4, "D"), value(4, "E")};
    check(!cache.ReplaceAll(std::move(duplicateGuid)), "duplicate guid rejected");
    std::vector<PlayerCacheData> duplicateName = {value(4, "D"), value(5, "D")};
    check(!cache.ReplaceAll(std::move(duplicateName)), "duplicate name rejected");
    std::vector<PlayerCacheData> invalid = {value(0, "Bad")};
    check(!cache.ReplaceAll(std::move(invalid)), "invalid replacement rejected");
    check(!!cache.Find(3), "rejected replacement preserves old cache");

    PlayerCacheSnapshot survivor = cache.Find(3);
    std::weak_ptr<PlayerCacheData const> weak = survivor;
    check(cache.ReplaceAll({}), "empty replacement accepted");
    check(survivor->sName == "Carol", "snapshot survives replacement");
    survivor.reset();
    check(weak.expired(), "snapshot reclaimed after replacement/release");
    check(cache.Size() == 0, "empty replacement clears cache");
}

void concurrentNameClaims()
{
    PlayerIdentityCache cache;
    std::atomic<int> ready{0};
    std::atomic<bool> go{false};
    std::atomic<int> accepted{0};
    std::vector<std::thread> threads;
    for (std::uint32_t i = 1; i <= 8; ++i)
    {
        threads.emplace_back([&, i]() {
            ready.fetch_add(1);
            while (!go.load()) {}
            if (cache.Upsert(value(i, "Contested")))
                accepted.fetch_add(1);
        });
    }
    while (ready.load() != 8) {}
    go.store(true);
    for (std::thread& thread : threads)
        thread.join();
    check(accepted.load() == 1, "exactly one concurrent name claim");
}

void concurrentRename()
{
    PlayerIdentityCache cache;
    cache.Upsert(value(1, "A"));
    cache.Upsert(value(2, "B"));
    std::atomic<int> ready{0};
    std::atomic<bool> go{false};
    std::atomic<int> accepted{0};
    std::vector<std::thread> threads;
    threads.emplace_back([&]() {
        ready.fetch_add(1);
        while (!go.load()) {}
        if (cache.Rename(1, "A", "Target")) accepted.fetch_add(1);
    });
    threads.emplace_back([&]() {
        ready.fetch_add(1);
        while (!go.load()) {}
        if (cache.Rename(2, "B", "Target")) accepted.fetch_add(1);
    });
    while (ready.load() != 2) {}
    go.store(true);
    for (std::thread& thread : threads)
        thread.join();
    check(accepted.load() == 1, "exactly one concurrent rename");
}

void concurrentReaders()
{
    PlayerIdentityCache cache;
    cache.Upsert(value(1, "A"));
    std::atomic<bool> stop{false};
    std::atomic<int> invalid{0};
    std::thread reader([&]() {
        while (!stop.load())
        {
            PlayerCacheSnapshot snapshot = cache.Find(1);
            if (snapshot && snapshot->sName != "A" && snapshot->sName != "B")
                invalid.fetch_add(1);
        }
    });
    for (int i = 0; i < 200; ++i)
    {
        cache.Upsert(value(1, (i % 2) ? "B" : "A"));
        cache.Find(1);
        if ((i % 17) == 0)
            cache.Erase(1);
        cache.Upsert(value(1, "A"));
    }
    stop.store(true);
    reader.join();
    check(invalid.load() == 0, "concurrent readers see immutable rows");
}

void accountSnapshotsSurviveMutation()
{
    PlayerIdentityCache cache;
    cache.Upsert(value(1, "Alice Smith", 5));
    cache.Upsert(value(2, "Bob Jones", 5));
    std::vector<PlayerCacheSnapshot> account = cache.FindAccount(5);
    check(account.size() == 2, "account snapshot count");
    cache.Erase(1);
    bool retained = false;
    for (PlayerCacheSnapshot const& snapshot : account)
        if (snapshot->uiGuid == 1 && snapshot->sName == "Alice Smith")
            retained = true;
    check(retained, "account snapshot survives removal");
}

void validReload()
{
    PlayerIdentityCache cache;
    cache.Upsert(value(9, "Old"));
    FakeDatabase database;
    database.AddCount(2);
    database.AddData({row(1, "Alice Smith"), row(2, "Bob Jones")}, 2);
    std::string reason;
    check(cache.Reload(database, 0, reason), "valid full reload");
    check(reason.empty(), "valid reload reason empty");
    check(database.queries == 2, "full reload two queries");
    check(cache.Find(1) && cache.Find(2) && !cache.Find(9),
          "full reload replaces cache");
    check(database.m_states[0]->nextRowCalls.load() == 0,
          "count result not advanced");
    check(database.m_states[1]->nextRowCalls.load() == 1,
          "data rows advanced normally");
    for (std::string const& sql : database.sqls)
    {
        check(sql.find("deleted_time` IS NULL AND `account` <> 0") != std::string::npos,
              "reload active-row predicate");
        check(sql.rfind("SELECT", 0) == 0, "reload performs no writes");
    }
}

void zeroCountReloads()
{
    PlayerIdentityCache cache;
    cache.Upsert(value(1, "Alice"));
    FakeDatabase full;
    full.AddCount(0);
    std::string reason;
    check(cache.Reload(full, 0, reason), "zero full reload");
    check(cache.Size() == 0, "zero full reload clears");

    cache.Upsert(value(1, "Alice"));
    FakeDatabase single;
    single.AddCount(0);
    check(cache.Reload(single, 1, reason), "zero single reload");
    check(!cache.Find(1), "zero single reload erases");
    check(single.sqls[0].find("`guid`=1") != std::string::npos,
          "single reload guid predicate");
}

void failedReloadPreserves()
{
    PlayerIdentityCache cache;
    cache.Upsert(value(7, "Keep"));

    FakeDatabase nullCount;
    check(!reload(cache, nullCount), "null count fails");
    check(!!cache.Find(7), "null count preserves");

    FakeDatabase malformed;
    malformed.AddRaw(std::unique_ptr<QueryResult>(
        new FakeResult(2, 1, true)));
    check(!reload(cache, malformed), "malformed count fails");
    check(!!cache.Find(7), "malformed count preserves");

    FakeDatabase missingData;
    missingData.AddCount(1);
    check(!reload(cache, missingData), "missing data fails");
    check(!!cache.Find(7), "missing data preserves");

    FakeDatabase mismatch;
    mismatch.AddCount(2);
    mismatch.AddData({row(1, "Alice Smith")}, 2);
    check(!reload(cache, mismatch), "row count mismatch fails");
    check(!!cache.Find(7), "row count mismatch preserves");

    FakeDatabase malformedData;
    malformedData.AddCount(1);
    malformedData.AddData({{"1", "1", "1", "0", "1", "Alice Smith",
                            "10", "12", "0", "1", "2", "3", "4"}}, 1);
    check(!reload(cache, malformedData), "malformed data shape fails");
    check(!!cache.Find(7), "malformed data preserves");
}

void invalidRowsRejectAtomically()
{
    PlayerIdentityCache cache;
    cache.Upsert(value(7, "Keep"));

    FakeDatabase badName;
    badName.AddCount(1);
    badName.AddData({row(1, "lowercase")}, 1);
    check(!reload(cache, badName), "invalid active name fails");
    check(!!cache.Find(7), "invalid active name preserves");

    FakeDatabase duplicate;
    duplicate.AddCount(2);
    duplicate.AddData({row(1, "Alice Smith"), row(1, "Bob Jones")}, 2);
    check(!reload(cache, duplicate), "duplicate guid fails");
    check(!!cache.Find(7), "duplicate guid preserves");

    FakeDatabase duplicateName;
    duplicateName.AddCount(2);
    duplicateName.AddData({row(1, "Alice Smith"), row(2, "Alice Smith")}, 2);
    check(!reload(cache, duplicateName), "duplicate name fails");
    check(!!cache.Find(7), "duplicate name preserves");
}

void singleReloadAlias()
{
    PlayerIdentityCache cache;
    cache.Upsert(value(1, "Old"));
    cache.Upsert(value(2, "Other"));
    FakeDatabase database;
    database.AddCount(1);
    database.AddData({row(1, "New Name")}, 1);
    std::string reason;
    check(cache.Reload(database, 1, reason), "single reload succeeds");
    check(cache.Find(1)->sName == "New Name" && cache.FindName("New Name") &&
              !cache.FindName("Old"),
          "single reload removes old alias");
    check(cache.Find(2) && cache.Find(2)->sName == "Other",
          "single reload preserves unrelated");
}

void singleCollisionPreserves()
{
    PlayerIdentityCache cache;
    cache.Upsert(value(1, "Old"));
    cache.Upsert(value(2, "Taken Name"));
    FakeDatabase database;
    database.AddCount(1);
    database.AddData({row(1, "Taken Name")}, 1);
    check(!reload(cache, database, 1), "single collision fails");
    check(cache.Find(1)->sName == "Old" && cache.Find(2)->sName == "Taken Name",
          "single collision preserves cache");
}

void malformedCountPreservesCache()
{
    std::vector<std::string> const malformed = {
        "", "not-a-count", "-1", "+1", " 1", "1 ", "1x",
        "18446744073709551616"
    };

    for (std::string const& text : malformed)
    {
        for (uint32 guid : {0u, 1u})
        {
            PlayerIdentityCache cache;
            PlayerCacheSnapshot one = cache.Upsert(value(1, "Alice Smith"));
            PlayerCacheSnapshot two = cache.Upsert(value(2, "Bob Jones"));
            FakeDatabase database;
            database.AddCountText(text);
            std::string reason;
            check(!cache.Reload(database, guid, reason),
                  "malformed count rejected: " + text);
            check(!reason.empty(), "malformed count reason: " + text);
            check(database.queries == 1,
                  "malformed count stops before data query: " + text);
            check(cache.Find(1) == one && cache.Find(2) == two,
                  "malformed count preserves snapshots: " + text);
            check(cache.FindName("Alice Smith") == one &&
                      cache.FindName("Bob Jones") == two,
                  "malformed count preserves names: " + text);
        }
    }

    for (uint32 guid : {0u, 1u})
    {
        PlayerIdentityCache cache;
        PlayerCacheSnapshot one = cache.Upsert(value(1, "Alice Smith"));
        PlayerCacheSnapshot two = cache.Upsert(value(2, "Bob Jones"));
        FakeDatabase database;
        database.AddNullCount();
        std::string reason;
        check(!cache.Reload(database, guid, reason), "NULL count rejected");
        check(!reason.empty(), "NULL count reason");
        check(database.queries == 1, "NULL count stops before data query");
        check(cache.Find(1) == one && cache.Find(2) == two,
              "NULL count preserves snapshots");
    }
}

void countBoundaries()
{
    PlayerIdentityCache full;
    full.Upsert(value(1, "Alice Smith"));
    FakeDatabase zeroFull;
    zeroFull.AddCountText("0");
    std::string reason;
    check(full.Reload(zeroFull, 0, reason), "zero full count succeeds");
    check(full.Size() == 0, "zero full count clears");

    PlayerIdentityCache single;
    single.Upsert(value(1, "Alice Smith"));
    FakeDatabase zeroSingle;
    zeroSingle.AddCountText("0");
    check(single.Reload(zeroSingle, 1, reason), "zero single count succeeds");
    check(!single.Find(1), "zero single count erases");

    PlayerIdentityCache positive;
    FakeDatabase rows;
    rows.AddCountText("2");
    rows.AddData({row(1, "Alice Smith"), row(2, "Bob Jones")}, 2);
    check(positive.Reload(rows, 0, reason), "positive count loads");
    check(positive.Size() == 2, "positive count preserves rows");

    PlayerIdentityCache maximum;
    PlayerCacheSnapshot keep = maximum.Upsert(value(7, "Keep Me"));
    FakeDatabase maxCount;
    maxCount.AddCountText("18446744073709551615");
    check(!maximum.Reload(maxCount, 0, reason),
          "uint64 maximum reaches data query");
    check(maxCount.queries == 2, "uint64 maximum attempts data query");
    check(maximum.Find(7) == keep, "max count failure preserves cache");

    FakeDatabase overflow;
    overflow.AddCountText("18446744073709551616");
    check(!maximum.Reload(overflow, 0, reason),
          "uint64 maximum plus one rejected");
    check(overflow.queries == 1, "overflow stops before data query");
    check(maximum.Find(7) == keep, "overflow preserves cache");
}

void runTests()
{
    snapshotSurvivesMutation();
    readersSeeNewSnapshots();
    duplicateAndStaleOperationsRejected();
    identityAndMissingUpdatesRejected();
    payloadAndReplacement();
    concurrentNameClaims();
    concurrentRename();
    concurrentReaders();
    accountSnapshotsSurviveMutation();
    validReload();
    zeroCountReloads();
    failedReloadPreserves();
    invalidRowsRejectAtomically();
    singleReloadAlias();
    singleCollisionPreserves();
    malformedCountPreservesCache();
    countBoundaries();
}
}

int main()
{
    runTests();
    std::cout << "Executed " << checks << " checks; " << failures
              << " failure(s)\n";
    return failures == 0 ? 0 : 1;
}
