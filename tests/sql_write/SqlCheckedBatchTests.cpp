#include <cstdint>
#include <iostream>
#include <limits>
#include <memory>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include "Database/SqlCheckedBatch.h"
#include "Database/SqlCheckedBatchExecution.h"

#include <mysql.h>

struct SqlCheckedBatchTestAccess
{
    static SqlCheckedBatch Make(Database* database, uint32 serialId)
    {
        return SqlCheckedBatch(database, serialId);
    }

    static bool Append(SqlCheckedBatch& batch, std::string sql,
                       std::vector<SqlStmtFieldData> parameters,
                       SqlAffectedRowsPolicy policy)
    {
        return batch.Append(std::move(sql), std::move(parameters), policy);
    }
};

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

SqlWriteResult result(SqlWriteOutcome outcome, uint64 affected = 0,
                      uint32 error = 0)
{
    SqlWriteResult value = {outcome, affected, error};
    return value;
}

SqlBoundWrite write(std::string sql,
                    SqlAffectedRowsPolicy policy = SqlAffectedRowsPolicy::Any)
{
    SqlBoundWrite value;
    value.sql = std::move(sql);
    value.affectedRowsPolicy = policy;
    return value;
}

struct FakeConnection
{
    SqlWriteResult begin;
    SqlWriteResult commit;
    SqlWriteResult rollback;
    std::vector<SqlWriteResult> statements;
    std::size_t statementCursor;
    int discardCalls;
    std::vector<std::string> events;

    FakeConnection()
        : begin(result(SqlWriteOutcome::Succeeded, 1)),
          commit(result(SqlWriteOutcome::Succeeded, 0)),
          rollback(result(SqlWriteOutcome::Succeeded, 0)),
          statementCursor(0),
          discardCalls(0)
    {
    }

    SqlWriteResult ExecuteChecked(std::string const& sql)
    {
        events.push_back(sql);
        if (sql == "START TRANSACTION")
            return begin;
        if (sql == "COMMIT")
            return commit;
        if (sql == "ROLLBACK")
            return rollback;
        return result(SqlWriteOutcome::Failed, 0, 1);
    }

    SqlWriteResult ExecutePreparedChecked(
        std::string const& sql,
        std::vector<SqlStmtFieldData> const&)
    {
        events.push_back("stmt:" + sql);
        if (statementCursor >= statements.size())
            return result(SqlWriteOutcome::Failed, 0, 2);
        return statements[statementCursor++];
    }

    void DiscardConnection()
    {
        ++discardCalls;
        events.push_back("discard");
    }
};

void emptyBatchExecutesNothing()
{
    FakeConnection connection;
    std::vector<SqlBoundWrite> writes;
    SqlBatchResult const batch = ExecuteSqlCheckedBatch(connection, writes);

    check(batch.outcome == SqlWriteOutcome::NotExecuted &&
              batch.stage == SqlBatchStage::NotStarted,
          "empty batch result");
    check(connection.events.empty(), "empty batch has no native calls");
    check(connection.discardCalls == 0, "empty batch does not discard");
}

void beginFailureExecutesNoStatements()
{
    FakeConnection connection;
    connection.begin = result(SqlWriteOutcome::Failed, 0, 9);
    std::vector<SqlBoundWrite> writes = {write("one")};
    SqlBatchResult const batch = ExecuteSqlCheckedBatch(connection, writes);

    check(batch.stage == SqlBatchStage::Begin &&
              batch.outcome == SqlWriteOutcome::Failed,
          "begin failure result");
    check(connection.events == std::vector<std::string>{"START TRANSACTION"},
          "begin failure executes nothing else");
    check(connection.discardCalls == 0, "begin failure does not discard");
}

void unknownBeginAttemptsCleanupWithoutRetry()
{
    FakeConnection connection;
    connection.begin = result(SqlWriteOutcome::OutcomeUnknown, 0, 2006);
    std::vector<SqlBoundWrite> writes = {write("one")};
    SqlBatchResult const batch = ExecuteSqlCheckedBatch(connection, writes);

    check(batch.stage == SqlBatchStage::Begin &&
              batch.outcome == SqlWriteOutcome::OutcomeUnknown &&
              batch.rollbackAttempted &&
              batch.rollbackResult.outcome == SqlWriteOutcome::Succeeded,
          "unknown begin cleanup result");
    check(connection.events ==
              std::vector<std::string>{"START TRANSACTION", "ROLLBACK"},
          "unknown begin rolls back once without retry");
}

void successfulBatchCommitsOnceInOrder()
{
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 2),
    };
    std::vector<SqlBoundWrite> writes = {write("one"), write("two")};
    SqlBatchResult const batch = ExecuteSqlCheckedBatch(connection, writes);

    check(batch.outcome == SqlWriteOutcome::Succeeded &&
              batch.stage == SqlBatchStage::Complete,
          "successful batch result");
    check(batch.statementIndex == static_cast<std::size_t>(-1),
          "successful commit has no statement index");
    check(connection.events == std::vector<std::string>{
              "START TRANSACTION", "stmt:one", "stmt:two", "COMMIT"},
          "successful batch order and one commit");
    check(batch.statementResults.size() == 2, "two statement results");
}

void zeroRowsAllowedUnderAnyPolicy()
{
    FakeConnection connection;
    connection.statements = {result(SqlWriteOutcome::Succeeded, 0)};
    std::vector<SqlBoundWrite> writes = {write("one")};
    SqlBatchResult const batch = ExecuteSqlCheckedBatch(connection, writes);

    check(batch.outcome == SqlWriteOutcome::Succeeded,
          "Any policy accepts zero rows");
}

void exactOnePolicyRejectsZeroAndMultipleRows()
{
    uint64 const badCounts[] = {0, 2};
    for (uint64 count : badCounts)
    {
        FakeConnection connection;
        connection.statements = {result(SqlWriteOutcome::Succeeded, count)};
        std::vector<SqlBoundWrite> writes = {
            write("one", SqlAffectedRowsPolicy::ExactlyOne)};
        SqlBatchResult const batch = ExecuteSqlCheckedBatch(connection, writes);

        check(batch.outcome == SqlWriteOutcome::Failed &&
                  batch.stage == SqlBatchStage::RowCount &&
                  batch.statementIndex == 0 &&
                  batch.cause.outcome == SqlWriteOutcome::Succeeded &&
                  batch.cause.affectedRows == count,
              "ExactlyOne rejects row count");
        check(connection.events ==
                  std::vector<std::string>{
                      "START TRANSACTION", "stmt:one", "ROLLBACK"},
              "row-count failure rolls back once");
    }
}

void statementFailureStopsAndRollsBackOnce()
{
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Failed, 0, 1146),
        result(SqlWriteOutcome::Succeeded, 1),
    };
    std::vector<SqlBoundWrite> writes = {write("one"), write("two"), write("three")};
    SqlBatchResult const batch = ExecuteSqlCheckedBatch(connection, writes);

    check(batch.outcome == SqlWriteOutcome::Failed &&
              batch.stage == SqlBatchStage::Statement &&
              batch.statementIndex == 1,
          "statement failure stage/index");
    check(connection.events == std::vector<std::string>{
              "START TRANSACTION", "stmt:one", "stmt:two", "ROLLBACK"},
          "statement failure stops and rolls back once");
    check(batch.statementResults.size() == 2, "only attempted results recorded");
    check(batch.cause.nativeError == 1146, "original cause preserved");
}

void duplicatePreservesOriginalCause()
{
    FakeConnection connection;
    connection.statements = {result(SqlWriteOutcome::DuplicateKey, 0, 1062)};
    std::vector<SqlBoundWrite> writes = {write("one")};
    SqlBatchResult const batch = ExecuteSqlCheckedBatch(connection, writes);

    check(batch.outcome == SqlWriteOutcome::DuplicateKey &&
              batch.cause.outcome == SqlWriteOutcome::DuplicateKey &&
              batch.cause.nativeError == 1062 &&
              batch.rollbackResult.outcome == SqlWriteOutcome::Succeeded,
          "duplicate cause/rollback result");
}

void unknownStatementStaysUnknownAfterSuccessfulRollback()
{
    FakeConnection connection;
    connection.statements = {result(SqlWriteOutcome::OutcomeUnknown, 0, 2013)};
    std::vector<SqlBoundWrite> writes = {write("one")};
    SqlBatchResult const batch = ExecuteSqlCheckedBatch(connection, writes);

    check(batch.outcome == SqlWriteOutcome::OutcomeUnknown &&
              batch.cause.outcome == SqlWriteOutcome::OutcomeUnknown &&
              batch.rollbackResult.outcome == SqlWriteOutcome::Succeeded,
          "unknown statement remains unknown");
    check(connection.discardCalls == 0,
          "successful cleanup does not discard");
}

void failedRollbackDiscardsConnection()
{
    FakeConnection connection;
    connection.statements = {result(SqlWriteOutcome::Failed, 0, 1146)};
    connection.rollback = result(SqlWriteOutcome::Failed, 0, 2006);
    std::vector<SqlBoundWrite> writes = {write("one")};
    SqlBatchResult const batch = ExecuteSqlCheckedBatch(connection, writes);

    check(batch.outcome == SqlWriteOutcome::OutcomeUnknown &&
              batch.rollbackAttempted &&
              batch.rollbackResult.outcome == SqlWriteOutcome::Failed,
          "failed rollback result");
    check(connection.discardCalls == 1, "failed rollback discards once");
}

void failedCommitIsUnknownEvenAfterSuccessfulCleanup()
{
    FakeConnection connection;
    connection.statements = {result(SqlWriteOutcome::Succeeded, 1)};
    connection.commit = result(SqlWriteOutcome::Failed, 0, 1205);
    std::vector<SqlBoundWrite> writes = {write("one")};
    SqlBatchResult const batch = ExecuteSqlCheckedBatch(connection, writes);

    check(batch.outcome == SqlWriteOutcome::OutcomeUnknown &&
              batch.stage == SqlBatchStage::Commit &&
              batch.statementIndex == static_cast<std::size_t>(-1) &&
              batch.cause.outcome == SqlWriteOutcome::Failed &&
              batch.rollbackResult.outcome == SqlWriteOutcome::Succeeded,
          "failed commit remains unknown");
    check(connection.discardCalls == 0,
          "successful commit cleanup does not discard");
}

void unknownCommitIsNotReplayed()
{
    FakeConnection connection;
    connection.statements = {result(SqlWriteOutcome::Succeeded, 1)};
    connection.commit = result(SqlWriteOutcome::OutcomeUnknown, 0, 2013);
    std::vector<SqlBoundWrite> writes = {write("one")};
    SqlBatchResult const batch = ExecuteSqlCheckedBatch(connection, writes);

    check(batch.outcome == SqlWriteOutcome::OutcomeUnknown &&
              batch.stage == SqlBatchStage::Commit &&
              batch.statementIndex == static_cast<std::size_t>(-1),
          "unknown commit result");
    int commits = 0;
    for (std::string const& event : connection.events)
        if (event == "COMMIT")
            ++commits;
    check(commits == 1, "unknown commit is not replayed");
}

void successfulCleanupDoesNotDiscardConnection()
{
    FakeConnection connection;
    connection.statements = {result(SqlWriteOutcome::Failed, 0, 1146)};
    connection.rollback = result(SqlWriteOutcome::Succeeded, 0);
    std::vector<SqlBoundWrite> writes = {write("one")};
    (void)ExecuteSqlCheckedBatch(connection, writes);
    check(connection.discardCalls == 0,
          "successful cleanup keeps connection");
}

void statementResultsPreserveOrderAndLargeCounts()
{
    FakeConnection connection;
    uint64 const large = static_cast<uint64>(std::numeric_limits<uint32>::max()) + 7;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 0),
        result(SqlWriteOutcome::Succeeded, large),
    };
    std::vector<SqlBoundWrite> writes = {write("one"), write("two")};
    SqlBatchResult const batch = ExecuteSqlCheckedBatch(connection, writes);

    check(batch.outcome == SqlWriteOutcome::Succeeded, "large counts success");
    check(batch.statementResults.size() == 2 &&
              batch.statementResults[0].affectedRows == 0 &&
              batch.statementResults[1].affectedRows == large,
          "statement results preserve order/counts");
}

void movedFromBatchCannotBeReused()
{
    Database* const first = reinterpret_cast<Database*>(0x1);
    Database* const second = reinterpret_cast<Database*>(0x2);

    SqlCheckedBatch original = SqlCheckedBatchTestAccess::Make(first, 42);
    std::vector<SqlStmtFieldData> payload;
    payload.emplace_back(static_cast<uint32>(77));
    payload.emplace_back("payload");
    check(SqlCheckedBatchTestAccess::Append(
              original, "one", std::move(payload),
              SqlAffectedRowsPolicy::ExactlyOne),
          "original batch append");
    SqlCheckedBatch moved(std::move(original));
    check(moved.GetDatabase() == first && moved.GetSerialId() == 42,
          "move transfers identity/serial");
    check(moved.Writes().size() == 1 &&
              moved.Writes()[0].sql == "one" &&
              moved.Writes()[0].affectedRowsPolicy ==
                  SqlAffectedRowsPolicy::ExactlyOne &&
              moved.Writes()[0].parameters.size() == 2 &&
              moved.Writes()[0].parameters[0].type() == FIELD_UI32 &&
              *static_cast<uint32 const*>(
                  moved.Writes()[0].parameters[0].buff()) == 77 &&
              moved.Writes()[0].parameters[1].type() == FIELD_STRING &&
              std::string(static_cast<char const*>(
                  moved.Writes()[0].parameters[1].buff())) == "payload",
          "move transfers SQL/policy/typed payload");
    check(original.GetDatabase() == nullptr,
          "move nulls source database");
    check(original.GetSerialId() == 0, "move zeros source serial");
    check(original.Empty(), "move empties source writes");
    check(!SqlCheckedBatchTestAccess::Append(
              original, "two", {}, SqlAffectedRowsPolicy::Any),
          "moved-from append rejected");
    check(original.Empty() && original.GetSerialId() == 0,
          "moved-from append leaves source unchanged");
    check(!original.IsValid(), "moved-from batch invalid");

    SqlCheckedBatch assigned = SqlCheckedBatchTestAccess::Make(second, 7);
    check(SqlCheckedBatchTestAccess::Append(
              assigned, "old", {}, SqlAffectedRowsPolicy::Any),
          "destination initial payload");
    assigned = std::move(moved);
    check(assigned.GetDatabase() == first && assigned.GetSerialId() == 42 &&
              assigned.Writes().size() == 1 &&
              assigned.Writes()[0].sql == "one",
          "move assignment replaces destination payload");
    check(moved.GetDatabase() == nullptr && moved.GetSerialId() == 0 &&
              moved.Empty(),
          "move assignment invalidates source storage");
    check(!SqlCheckedBatchTestAccess::Append(
              moved, "three", {}, SqlAffectedRowsPolicy::Any),
          "move-assigned source append rejected");
    check(moved.Empty() && !moved.IsValid(),
          "move-assigned source remains invalid");

    SqlCheckedBatch& self = assigned;
    assigned = std::move(self);
    check(assigned.GetDatabase() == first && assigned.GetSerialId() == 42 &&
              assigned.Writes().size() == 1 &&
              assigned.Writes()[0].sql == "one",
          "self-move preserves batch");
}

void batchCallbackPreservesResultAfterCancellation()
{
    SqlBatchResult expected = EmptySqlBatchResult();
    expected.outcome = SqlWriteOutcome::Failed;
    expected.stage = SqlBatchStage::Statement;
    expected.statementIndex = 2;
    expected.cause = result(SqlWriteOutcome::DuplicateKey, 0, 1062);
    expected.statementResults = {result(SqlWriteOutcome::Succeeded, 1),
                                 result(SqlWriteOutcome::DuplicateKey, 0, 1062)};
    expected.rollbackAttempted = true;
    expected.rollbackResult = result(SqlWriteOutcome::Succeeded, 0);

    int calls = 0;
    std::thread::id const caller = std::this_thread::get_id();
    std::thread::id observed;
    std::unique_ptr<MaNGOS::IQueryCallback> callback =
        MakeSqlBatchCallback(expected, [&](SqlBatchResult actual) {
            ++calls;
            observed = std::this_thread::get_id();
            check(actual.outcome == expected.outcome &&
                      actual.stage == expected.stage &&
                      actual.statementIndex == expected.statementIndex &&
                      actual.cause.outcome == expected.cause.outcome &&
                      actual.cause.nativeError == expected.cause.nativeError &&
                      actual.statementResults.size() == 2 &&
                      actual.rollbackAttempted &&
                      actual.rollbackResult.outcome ==
                          SqlWriteOutcome::Succeeded,
                  "batch callback full result");
        });

    check(!callback->IsThreadSafe(), "batch callback unsafe routing");
    callback->SetResult(nullptr);
    callback->Execute();
    callback->Execute();
    check(calls == 1, "batch callback delivers once");
    check(observed == caller, "batch callback runs on caller thread");
}

void runTests()
{
    emptyBatchExecutesNothing();
    beginFailureExecutesNoStatements();
    unknownBeginAttemptsCleanupWithoutRetry();
    successfulBatchCommitsOnceInOrder();
    zeroRowsAllowedUnderAnyPolicy();
    exactOnePolicyRejectsZeroAndMultipleRows();
    statementFailureStopsAndRollsBackOnce();
    duplicatePreservesOriginalCause();
    unknownStatementStaysUnknownAfterSuccessfulRollback();
    failedRollbackDiscardsConnection();
    failedCommitIsUnknownEvenAfterSuccessfulCleanup();
    unknownCommitIsNotReplayed();
    successfulCleanupDoesNotDiscardConnection();
    statementResultsPreserveOrderAndLargeCounts();
    movedFromBatchCannotBeReused();
    batchCallbackPreservesResultAfterCancellation();
}
}

int main()
{
    runTests();
    std::cout << "Executed " << checks << " checks; " << failures
              << " failure(s)\n";
    return failures == 0 ? 0 : 1;
}

// Unused raw-write substitutes keep the shared SqlWriteCallback object linkable;
// these batch tests never call the raw write executor.
extern "C"
{
int STDCALL mysql_options(MYSQL*, enum mysql_option, void const*) { return 0; }
int STDCALL mysql_real_query(MYSQL*, char const*, unsigned long) { return 0; }
unsigned int STDCALL mysql_errno(MYSQL*) { return 0; }
unsigned int STDCALL mysql_field_count(MYSQL*) { return 0; }
MYSQL_RES* STDCALL mysql_store_result(MYSQL*) { return nullptr; }
void STDCALL mysql_free_result(MYSQL_RES*) {}
my_ulonglong STDCALL mysql_affected_rows(MYSQL*) { return 0; }
}
