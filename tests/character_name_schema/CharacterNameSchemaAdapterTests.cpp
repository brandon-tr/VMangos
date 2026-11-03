#include "Database/CharacterNameSchema.h"
#include "Database/Database.h"
#include "Database/Field.h"
#include "Database/QueryResult.h"

#include <cstdint>
#include <iostream>
#include <memory>
#include <string>
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
    int nextRowCalls = 0;
};

class FakeResult : public QueryResult
{
    public:
        FakeResult(std::uint64_t rows, std::uint32_t fields, bool validFetch,
                   std::string value, std::shared_ptr<CursorState> state)
            : QueryResult(rows, fields),
              m_value(std::move(value)),
              m_field(),
              m_state(std::move(state))
        {
            if (validFetch && fields > 0)
            {
                m_field.SetValue(m_value.c_str());
                mCurrentRow = &m_field;
            }
        }

        bool NextRow() override
        {
            ++m_state->nextRowCalls;
            return false;
        }

    private:
        std::string m_value;
        Field m_field;
        std::shared_ptr<CursorState> m_state;
};

class FakeDatabase : public Database
{
    public:
        explicit FakeDatabase(std::shared_ptr<CursorState> state)
            : m_state(std::move(state))
        {
        }

        void Add(std::uint64_t value)
        {
            m_results.push_back(std::unique_ptr<QueryResult>(
                new FakeResult(1, 1, true, std::to_string(value), m_state)));
        }

        void AddRaw(std::unique_ptr<QueryResult> result)
        {
            m_results.push_back(std::move(result));
        }

        void Replace(std::size_t index, std::unique_ptr<QueryResult> result)
        {
            m_results[index] = std::move(result);
        }

        std::unique_ptr<QueryResult> Query(std::string const&) override
        {
            int const index = m_queries++;
            if (index == m_failAt || index >= static_cast<int>(m_results.size()))
                return nullptr;
            return std::move(m_results[static_cast<std::size_t>(index)]);
        }

        int m_queries = 0;
        int m_failAt = -1;

    private:
        std::shared_ptr<CursorState> m_state;
        std::vector<std::unique_ptr<QueryResult>> m_results;
};

FakeDatabase NineValid(std::shared_ptr<CursorState> state)
{
    FakeDatabase database(std::move(state));
    for (std::uint64_t value : {56, 56, 1, 3, 2, 1, 1, 1, 1})
        database.Add(value);
    return database;
}

void validAdapterReadsPositionedRows()
{
    auto state = std::make_shared<CursorState>();
    FakeDatabase database = NineValid(state);
    std::string reason = "stale";

    check(CheckCharacterNameSchema(database, reason),
          "valid adapter accepts positioned results");
    check(database.m_queries == 9, "valid adapter performs nine queries");
    check(state->nextRowCalls == 0, "valid adapter does not advance cursor");
    check(reason.empty(), "valid adapter clears reason");
}

void malformedResultsAreRejected()
{
    struct Case
    {
        char const* label;
        std::uint64_t rows;
        std::uint32_t fields;
        bool validFetch;
    };

    Case const cases[] = {
        {"null query result", 0, 0, false},
        {"zero rows", 0, 1, false},
        {"multiple rows", 2, 1, true},
        {"zero fields", 1, 0, false},
        {"multiple fields", 1, 2, true},
        {"null Fetch pointer", 1, 1, false},
    };

    for (Case const& item : cases)
    {
        auto state = std::make_shared<CursorState>();
        FakeDatabase database(state);
        database.AddRaw(std::unique_ptr<QueryResult>(
            new FakeResult(item.rows, item.fields, item.validFetch, "56",
                           state)));
        std::string reason;
        check(!CheckCharacterNameSchema(database, reason), item.label);
        check(database.m_queries == 1,
              std::string(item.label) + " stops at first query");
        check(state->nextRowCalls == 0,
              std::string(item.label) + " does not advance cursor");
        check(!reason.empty(), std::string(item.label) + " gives reason");
    }
}

void nullQueryResultIsRejected()
{
    auto state = std::make_shared<CursorState>();
    FakeDatabase database(state);
    database.AddRaw(nullptr);
    std::string reason;
    check(!CheckCharacterNameSchema(database, reason), "null result rejected");
    check(database.m_queries == 1, "null result stops at first query");
    check(state->nextRowCalls == 0, "null result does not advance cursor");
}

void wrongCountsAreRejected()
{
    for (std::uint64_t wrong : {0, 1, 55, 57})
    {
        auto state = std::make_shared<CursorState>();
        FakeDatabase database = NineValid(state);
        database.Replace(0, std::unique_ptr<QueryResult>(
            new FakeResult(1, 1, true, std::to_string(wrong), state)));
        std::string reason;
        check(!CheckCharacterNameSchema(database, reason),
              "wrong count rejected: " + std::to_string(wrong));
        check(database.m_queries == 1, "wrong count stops at first check");
        check(state->nextRowCalls == 0, "wrong count does not advance cursor");
    }
}

void laterQueryFailureStopsImmediately()
{
    auto state = std::make_shared<CursorState>();
    FakeDatabase database = NineValid(state);
    database.m_failAt = 5;
    std::string reason;
    check(!CheckCharacterNameSchema(database, reason),
          "later query failure rejected");
    check(database.m_queries == 6, "later query failure stops later checks");
    check(state->nextRowCalls == 0,
          "later query failure does not advance cursor");
}

void ownershipAdapterReadsCounts()
{
    auto state = std::make_shared<CursorState>();
    FakeDatabase database(state);
    for (int i = 0; i < 4; ++i)
        database.Add(0);
    std::string reason = "stale";
    check(CheckCharacterNameOwnership(database, reason),
          "ownership adapter accepts consistent counts");
    check(database.m_queries == 4, "ownership adapter four queries");
    check(state->nextRowCalls == 0, "ownership adapter does not advance count");
    check(reason.empty(), "ownership adapter clears reason");
}

void runTests()
{
    validAdapterReadsPositionedRows();
    malformedResultsAreRejected();
    nullQueryResultIsRejected();
    wrongCountsAreRejected();
    laterQueryFailureStopsImmediately();
    ownershipAdapterReadsCounts();
}
}

int main()
{
    runTests();
    std::cout << "Executed " << checks << " checks; " << failures
              << " failure(s)\n";
    return failures == 0 ? 0 : 1;
}
