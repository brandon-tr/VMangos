#include "Database/CharacterNameSchemaChecks.h"

#include <cstdint>
#include <iostream>
#include <string>
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

struct FakeQuery
{
    std::vector<std::uint64_t> counts;
    int failAt = -1;
    int calls = 0;
    std::vector<std::string> sqls;

    bool operator()(std::string const& sql, std::uint64_t& count)
    {
        int const index = calls++;
        sqls.push_back(sql);
        if (index == failAt)
            return false;
        if (index >= static_cast<int>(counts.size()))
            return false;
        count = counts[static_cast<std::size_t>(index)];
        return true;
    }
};

std::vector<std::uint64_t> validCounts()
{
    return {56, 56, 1, 3, 2, 1, 1, 1, 1};
}

bool run(std::vector<std::uint64_t> counts, int failAt, std::string& reason,
         FakeQuery& query)
{
    query.counts = std::move(counts);
    query.failAt = failAt;
    return CheckCharacterNameSchemaCounts(query, reason);
}

void validSchemaPasses()
{
    FakeQuery query;
    std::string reason = "stale";
    check(run(validCounts(), -1, reason, query), "valid schema passes");
    check(reason.empty(), "valid schema clears reason");
    check(query.calls == 9, "valid schema runs nine checks");
    check(query.sqls.size() == 9, "valid schema runs nine queries");
}

void queryFailureAtEveryPosition()
{
    char const* const reasons[] = {
        "required character-name schema tables are missing",
        "required character-name schema tables are not InnoDB",
        "characters.identity_generation has an incompatible definition",
        "character naming columns are missing or not varchar(25)",
        "character_name_ownership columns have incompatible definitions",
        "character_name_ownership primary key is not canonical_name",
        "character_name_ownership guid unique key is invalid",
        "character_name_ownership foreign key target is invalid",
        "character_name_ownership foreign key rules are invalid",
    };

    for (int failAt = 0; failAt < 9; ++failAt)
    {
        FakeQuery query;
        std::string reason;
        check(!run(validCounts(), failAt, reason, query),
              "query failure stops validation at " + std::to_string(failAt));
        check(query.calls == failAt + 1,
              "query failure stops later queries at " + std::to_string(failAt));
        check(reason == reasons[failAt],
              "query failure reason at " + std::to_string(failAt));
    }
}

void countMismatchAtEachPosition()
{
    char const* const reasons[] = {
        "required character-name schema tables are missing",
        "required character-name schema tables are not InnoDB",
        "characters.identity_generation has an incompatible definition",
        "character naming columns are missing or not varchar(25)",
        "character_name_ownership columns have incompatible definitions",
        "character_name_ownership primary key is not canonical_name",
        "character_name_ownership guid unique key is invalid",
        "character_name_ownership foreign key target is invalid",
        "character_name_ownership foreign key rules are invalid",
    };

    for (int position = 0; position < 9; ++position)
    {
        std::vector<std::uint64_t> counts = validCounts();
        counts[static_cast<std::size_t>(position)] -=
            counts[static_cast<std::size_t>(position)] == 0 ? 0 : 1;
        FakeQuery query;
        std::string reason;
        check(!run(std::move(counts), -1, reason, query),
              "count mismatch at " + std::to_string(position));
        check(query.calls == position + 1,
              "count mismatch stops later queries at " + std::to_string(position));
        check(reason == reasons[position],
              "count mismatch reason at " + std::to_string(position));
    }
}

void zeroCountIsAValidMismatch()
{
    std::vector<std::uint64_t> counts = validCounts();
    counts[0] = 0;
    FakeQuery query;
    std::string reason;
    check(!run(std::move(counts), -1, reason, query), "zero count mismatch");
    check(reason == "required character-name schema tables are missing",
          "zero count reason");
}

void shapeSpecificFailures()
{
    struct Case
    {
        int position;
        std::uint64_t value;
        char const* reason;
        char const* label;
    };

    Case const cases[] = {
        {0, 55, "required character-name schema tables are missing",
         "missing required table"},
        {1, 55, "required character-name schema tables are not InnoDB",
         "non-InnoDB required table"},
        {2, 0, "characters.identity_generation has an incompatible definition",
         "wrong generation definition"},
        {3, 2, "character naming columns are missing or not varchar(25)",
         "insufficient naming capacity"},
        {4, 1, "character_name_ownership columns have incompatible definitions",
         "wrong ownership columns"},
        {5, 0, "character_name_ownership primary key is not canonical_name",
         "composite or prefix primary key"},
        {6, 0, "character_name_ownership guid unique key is invalid",
         "nonunique or composite guid key"},
        {7, 0, "character_name_ownership foreign key target is invalid",
         "wrong foreign key target"},
        {8, 0, "character_name_ownership foreign key rules are invalid",
         "cascade rules mismatch"},
    };

    for (Case const& item : cases)
    {
        std::vector<std::uint64_t> counts = validCounts();
        counts[static_cast<std::size_t>(item.position)] = item.value;
        FakeQuery query;
        std::string reason;
        check(!run(std::move(counts), -1, reason, query), item.label);
        check(reason == item.reason,
              std::string(item.label) + " reason");
    }
}

void queriesAreReadOnlyAndScoped()
{
    FakeQuery query;
    std::string reason;
    check(run(validCounts(), -1, reason, query), "scope fixture passes");

    for (std::string const& sql : query.sqls)
    {
        check(sql.find("DATABASE()") != std::string::npos,
              "query scoped to DATABASE()");
        check(sql.find("SELECT COUNT(*)") == 0,
              "query is a count aggregate");
        check(sql.find("INSERT") == std::string::npos &&
                  sql.find(" UPDATE ") == std::string::npos &&
                  sql.find(" DELETE ") == std::string::npos &&
                  sql.find("ALTER") == std::string::npos &&
                  sql.find("CREATE") == std::string::npos &&
                  sql.find("DROP") == std::string::npos &&
                  sql.find("TRUNCATE") == std::string::npos,
              "query has no mutation");
        check(sql.find("mangos") == std::string::npos &&
                  sql.find("characters_test") == std::string::npos,
              "query does not hard-code a database name");
    }
}

void shapePredicatesAreExplicit()
{
    FakeQuery query;
    std::string reason;
    check(run(validCounts(), -1, reason, query), "predicate fixture passes");
    if (query.sqls.size() != 9)
        return;

    auto has = [](std::string const& sql, std::string const& text) {
        return sql.find(text) != std::string::npos;
    };

    check(has(query.sqls[2], "DATA_TYPE='bigint'") &&
              has(query.sqls[2], "COLUMN_TYPE LIKE '%unsigned%'") &&
              has(query.sqls[2], "IS_NULLABLE='NO'") &&
              has(query.sqls[2], "COLUMN_DEFAULT='1'"),
          "generation checks width/sign/null/default");
    check(has(query.sqls[3], "DATA_TYPE='varchar'") &&
              has(query.sqls[3], "CHARACTER_MAXIMUM_LENGTH=25"),
          "naming checks varchar(25)");
    check(has(query.sqls[4], "DATA_TYPE='varbinary'") &&
              has(query.sqls[4], "CHARACTER_MAXIMUM_LENGTH=75") &&
              has(query.sqls[4], ")=2"),
          "ownership checks binary width and exact column count");

    for (std::size_t index = 5; index <= 6; ++index)
    {
        check(has(query.sqls[index], "SEQ_IN_INDEX=1") &&
                  has(query.sqls[index], "NON_UNIQUE=0") &&
                  has(query.sqls[index], "SUB_PART IS NULL"),
              "index check rejects composite/prefix/nonunique");
    }
    check(has(query.sqls[5], "INDEX_NAME='PRIMARY'") &&
              has(query.sqls[5], "COLUMN_NAME='canonical_name'"),
          "primary key checks exact column");
    check(has(query.sqls[6], "INDEX_NAME='uq_character_name_guid'") &&
              has(query.sqls[6], "COLUMN_NAME='guid'"),
          "guid key checks exact column");
    check(has(query.sqls[7], "REFERENCED_TABLE_SCHEMA=DATABASE()") &&
              has(query.sqls[7], "REFERENCED_TABLE_NAME='characters'") &&
              has(query.sqls[7], "REFERENCED_COLUMN_NAME='guid'"),
          "foreign key checks exact target");
    check(has(query.sqls[8], "UPDATE_RULE='RESTRICT'") &&
              has(query.sqls[8], "DELETE_RULE='RESTRICT'"),
          "foreign key checks both RESTRICT rules");
}

void ownershipAudit()
{
    for (int failAt = 0; failAt < 4; ++failAt)
    {
        FakeQuery query;
        query.counts = {0, 0, 0, 0};
        query.failAt = failAt;
        std::string reason;
        check(!CheckCharacterNameOwnershipCounts(query, reason),
              "ownership query failure rejected");
        check(query.calls == failAt + 1,
              "ownership query failure stops later checks");
        check(!reason.empty(), "ownership query failure reason");
    }

    for (int position = 0; position < 4; ++position)
    {
        FakeQuery query;
        query.counts = {0, 0, 0, 0};
        query.counts[static_cast<std::size_t>(position)] = 1;
        std::string reason;
        check(!CheckCharacterNameOwnershipCounts(query, reason),
              "ownership inconsistency rejected");
        check(query.calls == position + 1,
              "ownership inconsistency stops later checks");
        check(!reason.empty(), "ownership inconsistency reason");
    }

    FakeQuery query;
    query.counts = {0, 0, 0, 0};
    std::string reason = "stale";
    check(CheckCharacterNameOwnershipCounts(query, reason),
          "consistent ownership passes");
    check(reason.empty(), "consistent ownership clears reason");
    check(query.calls == 4, "ownership audit four checks");
}

void runTests()
{
    validSchemaPasses();
    queryFailureAtEveryPosition();
    countMismatchAtEachPosition();
    zeroCountIsAValidMismatch();
    shapeSpecificFailures();
    shapePredicatesAreExplicit();
    queriesAreReadOnlyAndScoped();
    ownershipAudit();
}
}

int main()
{
    runTests();
    std::cout << "Executed " << checks << " checks; " << failures
              << " failure(s)\n";
    return failures == 0 ? 0 : 1;
}
