#include <cstring>
#include "Progression.h"
static_assert(SUPPORTED_CLIENT_BUILD == CLIENT_BUILD_1_12_1,
              "FN-05H tests require the 1.12.1 client build");
#include "Database/CharacterPersistenceContext.h"
#include "Database/CharacterIdentityPersistence.h"
#include "Database/CharacterPersistenceRecovery.h"
#include "Database/Database.h"
#include "Database/SqlCheckedBatchExecution.h"
#include "PlayerName.h"
#include "Objects/Player.h"
#include "ReputationMgr.h"
#include "Chat/MasterPlayer.h"

#include <mysql.h>

#include <cstdint>
#include <chrono>
#include <functional>
#include <atomic>
#include <thread>
#include <mutex>
#include <iostream>
#include <limits>
#include <memory>
#include <string>
#include <utility>
#include <vector>

SqlStmtParameters::SqlStmtParameters(int nParams)
{
    m_params.reserve(nParams);
}

bool Player::SaveAura(SpellAuraHolder const* holder, AuraSaveStruct& out)
{
    if (!holder || !holder->admitted)
        return false;
    out = holder->saved;
    return true;
}

bool SqlStatement::AppendToCheckedBatch(SqlCheckedBatch& batch,
                                        SqlAffectedRowsPolicy policy)
{
    if (!m_pDB || batch.GetDatabase() != m_pDB)
        return false;

    int const bound = m_pParams ? m_pParams->boundParams() : 0;
    if (bound != arguments())
        return false;

    std::string const sql = m_pDB->GetStmtString(ID());
    if (sql.empty() || sql.find('\0') != std::string::npos)
        return false;

    std::vector<SqlStmtFieldData> parameters;
    if (m_pParams)
        parameters = m_pParams->params();
    return batch.Append(std::move(sql), std::move(parameters), policy);
}

struct SqlCheckedBatchTestAccess
{
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

struct ObservedStatement
{
    std::string sql;
    std::vector<SqlStmtFieldData> parameters;
};

struct FakeConnection
{
    SqlWriteResult begin = result(SqlWriteOutcome::Succeeded, 1);
    SqlWriteResult commit = result(SqlWriteOutcome::Succeeded, 1);
    SqlWriteResult rollback = result(SqlWriteOutcome::Succeeded, 1);
    std::vector<SqlWriteResult> statements;
    std::vector<std::string> events;
    std::vector<ObservedStatement> observed;
    std::size_t cursor = 0;
    int beginCalls = 0;
    int commitCalls = 0;
    int rollbackCalls = 0;
    int discardCalls = 0;

    SqlWriteResult ExecuteChecked(std::string const& sql)
    {
        events.push_back("raw:" + sql);
        if (sql == "START TRANSACTION")
        {
            ++beginCalls;
            return begin;
        }
        if (sql == "COMMIT")
        {
            ++commitCalls;
            return commit;
        }
        if (sql == "ROLLBACK")
        {
            ++rollbackCalls;
            return rollback;
        }
        return result(SqlWriteOutcome::Failed, 0, 1);
    }

    SqlWriteResult ExecutePreparedChecked(
        std::string const& sql,
        std::vector<SqlStmtFieldData> const& parameters)
    {
        events.push_back("stmt:" + sql);
        observed.push_back(ObservedStatement{sql, parameters});
        if (cursor >= statements.size())
            return result(SqlWriteOutcome::Failed, 0, 2);
        return statements[cursor++];
    }

    void DiscardConnection()
    {
        ++discardCalls;
        events.push_back("discard");
    }
};

struct DatabaseState
{
    int submissions = 0;
    bool executing = false;
};

class ExecutorDatabase : public Database
{
    public:
        ExecutorDatabase(FakeConnection& connection, DatabaseState& state)
            : m_connection(connection), m_state(state)
        {
        }

        SqlBatchResult DirectExecuteCheckedBatch(SqlCheckedBatch batch) override
        {
            ++m_state.submissions;
            m_state.executing = true;
            SqlBatchResult const result =
                ExecuteSqlCheckedBatch(m_connection, batch.Writes());
            m_state.executing = false;
            return result;
        }

    private:
        FakeConnection& m_connection;
        DatabaseState& m_state;
};

class PresetDatabase : public Database
{
    public:
        explicit PresetDatabase(DatabaseState& state) : m_state(state) {}

        SqlBatchResult DirectExecuteCheckedBatch(SqlCheckedBatch) override
        {
            ++m_state.submissions;
            return preset;
        }

        SqlBatchResult preset = EmptySqlBatchResult();

    private:
        DatabaseState& m_state;
};

void append(CharacterPersistenceContext& context, std::string sql)
{
    check(SqlCheckedBatchTestAccess::Append(
              context.Batch(), std::move(sql), {},
              SqlAffectedRowsPolicy::ExactlyOne),
          "append " + sql);
}

void emptyOrInvalidBatchDoesNotSubmit()
{
    DatabaseState state;
    FakeConnection connection;
    ExecutorDatabase database(connection, state);
    CharacterPersistenceContext context(database, 4);
    int acknowledgments = 0;
    context.OnCommit([&]() { ++acknowledgments; });

    SqlBatchResult const result = context.Execute();
    check(result.outcome == SqlWriteOutcome::NotExecuted &&
              result.stage == SqlBatchStage::NotStarted,
          "empty context not submitted");
    check(state.submissions == 0, "empty context no submission");
    check(acknowledgments == 0, "empty context no acknowledgment");
    check(context.WasSubmitted(), "empty context consumed");
}

void invalidationDiscardsAcknowledgments()
{
    DatabaseState state;
    FakeConnection connection;
    ExecutorDatabase database(connection, state);
    CharacterPersistenceContext context(database, 5);
    append(context, "guard");
    int acknowledgments = 0;
    context.OnCommit([&]() { ++acknowledgments; });
    context.Invalidate();

    SqlBatchResult const result = context.Execute();
    check(result.outcome == SqlWriteOutcome::NotExecuted,
          "invalidated context not submitted");
    check(state.submissions == 0, "invalidated context no submission");
    check(acknowledgments == 0, "invalidated context no acknowledgment");
}

void destructionRunsNoAcknowledgments()
{
    DatabaseState state;
    FakeConnection connection;
    ExecutorDatabase database(connection, state);
    int acknowledgments = 0;
    {
        CharacterPersistenceContext context(database, 6);
        append(context, "guard");
        context.OnCommit([&]() { ++acknowledgments; });
    }
    check(acknowledgments == 0, "destruction no acknowledgment");
}

void guardZeroRowsStopsRelatedWrite()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {result(SqlWriteOutcome::Succeeded, 0)};
    ExecutorDatabase database(connection, state);

    CharacterPersistenceContext context(database, 7);
    append(context, "guard");
    append(context, "related");
    int acknowledgments = 0;
    context.OnCommit([&]() { ++acknowledgments; });

    SqlBatchResult const batch = context.Execute();
    check(batch.outcome == SqlWriteOutcome::Failed &&
              batch.stage == SqlBatchStage::RowCount &&
              batch.statementIndex == 0,
          "zero-row guard result");
    check(connection.events == std::vector<std::string>{
              "raw:START TRANSACTION", "stmt:guard", "raw:ROLLBACK"},
          "zero-row guard event order");
    check(connection.observed.size() == 1 &&
              connection.observed[0].sql == "guard",
          "related statement absent after guard");
    check(connection.rollbackCalls == 1 && connection.commitCalls == 0,
          "actual rollback called, commit absent");
    check(batch.rollbackAttempted &&
              batch.rollbackResult.outcome == SqlWriteOutcome::Succeeded,
          "reported rollback is the actual rollback");
    check(acknowledgments == 0, "guard failure no acknowledgment");
}

void relatedFailureRollsBackWithoutAcknowledgment()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Failed, 0, 1146)};
    ExecutorDatabase database(connection, state);

    CharacterPersistenceContext context(database, 8);
    append(context, "guard");
    append(context, "related");
    int acknowledgments = 0;
    context.OnCommit([&]() { ++acknowledgments; });

    SqlBatchResult const batch = context.Execute();
    check(batch.outcome == SqlWriteOutcome::Failed &&
              batch.stage == SqlBatchStage::Statement &&
              batch.statementIndex == 1 &&
              batch.cause.nativeError == 1146,
          "related failure result");
    check(connection.events == std::vector<std::string>{
              "raw:START TRANSACTION", "stmt:guard",
              "stmt:related", "raw:ROLLBACK"},
          "related failure event order");
    check(connection.rollbackCalls == 1 && connection.commitCalls == 0,
          "related failure actual rollback, no commit");
    check(acknowledgments == 0, "related failure no acknowledgment");
}

void uncertainCommitDoesNotAcknowledge()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1)};
    connection.commit = result(SqlWriteOutcome::OutcomeUnknown, 0, 2013);
    ExecutorDatabase database(connection, state);

    CharacterPersistenceContext context(database, 9);
    append(context, "guard");
    append(context, "related");
    int acknowledgments = 0;
    context.OnCommit([&]() { ++acknowledgments; });

    SqlBatchResult const batch = context.Execute();
    check(batch.outcome == SqlWriteOutcome::OutcomeUnknown &&
              batch.stage == SqlBatchStage::Commit,
          "uncertain commit result");
    check(connection.events == std::vector<std::string>{
              "raw:START TRANSACTION", "stmt:guard", "stmt:related",
              "raw:COMMIT", "raw:ROLLBACK"},
          "uncertain commit event order");
    check(connection.commitCalls == 1 && connection.rollbackCalls == 1,
          "one commit attempt and one cleanup rollback");
    check(connection.cursor == 2, "no statement replay");
    check(acknowledgments == 0, "uncertain commit no acknowledgment");
}

void successfulCommitAcknowledgesOnce()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);

    CharacterPersistenceContext context(database, 10);
    append(context, "guard");
    append(context, "related");
    int acknowledgments = 0;
    bool acknowledgmentDuringExecution = false;
    context.OnCommit([&]() {
        ++acknowledgments;
        if (state.executing)
            acknowledgmentDuringExecution = true;
    });

    SqlBatchResult const batch = context.Execute();
    check(batch.outcome == SqlWriteOutcome::Succeeded &&
              batch.stage == SqlBatchStage::Complete,
          "successful context result");
    check(connection.events == std::vector<std::string>{
              "raw:START TRANSACTION", "stmt:guard", "stmt:related",
              "raw:COMMIT"},
          "success event order");
    check(connection.rollbackCalls == 0, "success no rollback");
    check(acknowledgments == 1 && !acknowledgmentDuringExecution,
          "one acknowledgment after database execution");

    check(context.Execute().outcome == SqlWriteOutcome::NotExecuted,
          "second Execute rejected");
    check(state.submissions == 1 && acknowledgments == 1,
          "second Execute neither submits nor acknowledges");
}

void failedRollbackDiscardsConnection()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Failed, 0, 1146)};
    connection.rollback = result(SqlWriteOutcome::Failed, 0, 2013);
    ExecutorDatabase database(connection, state);

    CharacterPersistenceContext context(database, 11);
    append(context, "guard");
    append(context, "related");
    int acknowledgments = 0;
    context.OnCommit([&]() { ++acknowledgments; });

    SqlBatchResult const batch = context.Execute();
    check(batch.outcome == SqlWriteOutcome::OutcomeUnknown,
          "failed rollback yields uncertain outcome");
    check(connection.rollbackCalls == 1 && connection.discardCalls == 1,
          "failed rollback discards exactly once");
    check(connection.events.back() == "discard",
          "discard follows failed rollback");
    check(acknowledgments == 0, "failed rollback no acknowledgment");
}

void presetNonCompleteStageDoesNotAcknowledge()
{
    DatabaseState state;
    PresetDatabase database(state);
    database.preset = EmptySqlBatchResult();
    database.preset.outcome = SqlWriteOutcome::Succeeded;
    database.preset.stage = SqlBatchStage::Commit;
    CharacterPersistenceContext context(database, 12);
    append(context, "guard");
    int acknowledgments = 0;
    context.OnCommit([&]() { ++acknowledgments; });

    SqlBatchResult const batch = context.Execute();
    check(batch.outcome == SqlWriteOutcome::Succeeded &&
              batch.stage == SqlBatchStage::Commit,
          "artificial non-Complete success preserved");
    check(acknowledgments == 0, "non-Complete stage no acknowledgment");
}

void parametersAreOwnedByBatch()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);

    std::string const expectedSql =
        "INSERT INTO `character_name_ownership` (`canonical_name`, `guid`) "
        "VALUES (?, ?)";
    std::string const expectedName = "Alice Smith";
    uint64 const expectedGeneration = 18446744073709551615ULL;

    CharacterPersistenceContext context(database, 13);
    {
        std::string sql = expectedSql;
        std::string name = expectedName;
        uint64 generation = expectedGeneration;
        std::vector<SqlStmtFieldData> parameters;
        parameters.emplace_back(name.c_str());
        parameters.emplace_back(generation);
        check(SqlCheckedBatchTestAccess::Append(
                  context.Batch(), std::move(sql), std::move(parameters),
                  SqlAffectedRowsPolicy::ExactlyOne),
              "owned parameter append");
        name.assign("mutated");
        generation = 0;
    }

    SqlBatchResult const batch = context.Execute();
    check(batch.outcome == SqlWriteOutcome::Succeeded,
          "owned parameter execution success");
    check(connection.observed.size() == 1,
          "one observed prepared statement");
    if (connection.observed.size() == 1)
    {
        ObservedStatement const& observed = connection.observed[0];
        check(observed.sql == expectedSql, "owned SQL preserved");
        check(observed.parameters.size() == 2, "two observed parameters");
        check(observed.parameters[0].type() == FIELD_STRING &&
                  std::string(static_cast<char const*>(
                      observed.parameters[0].buff())) == expectedName,
              "owned name bytes preserved");
        check(observed.parameters[1].type() == FIELD_UI64 &&
                  *static_cast<uint64 const*>(
                      observed.parameters[1].buff()) == expectedGeneration,
              "owned generation preserved");
    }
}

CharacterIdentitySnapshot identity(uint32 guid, std::string name,
                                   uint64 generation, uint32 account = 7)
{
    CharacterIdentitySnapshot value;
    value.guid = guid;
    value.accountId = account;
    value.canonicalName = std::move(name);
    value.generation = generation;
    return value;
}

std::string parameterString(SqlStmtFieldData const& value)
{
    return value.type() == FIELD_STRING
        ? std::string(static_cast<char const*>(value.buff())) : std::string();
}

uint64 parameterUInt64(SqlStmtFieldData const& value)
{
    return value.type() == FIELD_UI64
        ? *static_cast<uint64 const*>(value.buff()) : 0;
}

bool AppendIdentityGuards(CharacterPersistenceContext& context,
                          CharacterIdentitySnapshot const& identityValue)
{
    return context.AppendIdentityGuards({identityValue});
}

void invalidIdentitySetPreventsSubmission()
{
    std::vector<std::vector<CharacterIdentitySnapshot>> cases;
    cases.push_back({});
    cases.push_back({identity(0, "Alice Smith", 1)});
    cases.push_back({identity(1, "Alice Smith", 1, 0)});
    cases.push_back({identity(1, "", 1)});
    cases.push_back({identity(1, "Alice", 1)});
    cases.push_back({identity(1, "alice smith", 1)});
    cases.push_back({identity(1, std::string("Alice\0Smith", 11), 1)});
    cases.push_back({identity(1, "Alice Smith", 0)});
    cases.push_back({identity(1, "Alice Smith",
                              std::numeric_limits<uint64>::max())});
    cases.push_back({identity(1, "Alice Smith", 1),
                     identity(1, "Bob Jones", 1)});
    cases.push_back({identity(1, "Alice Smith", 1),
                     identity(1, "Alice Smith", 1)});
    cases.push_back({identity(1, "Alice Smith", 1),
                     identity(2, "Bob Jones", 0)});

    for (std::vector<CharacterIdentitySnapshot> const& identities : cases)
    {
        DatabaseState state;
        FakeConnection connection;
        ExecutorDatabase database(connection, state);
        CharacterPersistenceContext context(database, 20);
        check(!context.AppendIdentityGuards(identities),
              "invalid identity set rejected");
        check(context.IsInvalid(), "invalid identity invalidates context");
        check(context.Batch().Empty(), "invalid identity appends nothing");
        check(context.Execute().outcome == SqlWriteOutcome::NotExecuted,
              "invalid identity cannot submit");
        check(state.submissions == 0 && connection.observed.empty(),
              "invalid identity no executor call");
    }
}

void guardsPrecedePayloadInGuidOrder()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);

    std::vector<CharacterIdentitySnapshot> identities = {
        identity(2, "Bob Jones", 22),
        identity(1, "Alice Smith", 11)};
    CharacterPersistenceContext context(database, 21);
    check(context.AppendIdentityGuards(identities), "guards appended");
    check(identities[0].guid == 2 && identities[1].guid == 1,
          "caller snapshot vector unchanged");
    append(context, "related");

    SqlCheckedBatch& batch = context.Batch();
    check(batch.Writes().size() == 3, "two guards plus payload");
    std::string const guard1Sql = batch.Writes()[0].sql;
    std::string const guard2Sql = batch.Writes()[1].sql;
    for (std::size_t i = 0; i < 2; ++i)
    {
        SqlBoundWrite const& guard = batch.Writes()[i];
        check(guard.sql.find("SET `identity_generation`=`identity_generation`+1") !=
                  std::string::npos,
              "guard SQL shape");
        check(guard.affectedRowsPolicy == SqlAffectedRowsPolicy::ExactlyOne,
              "guard ExactlyOne");
        check(guard.parameters.size() == 6, "guard six parameters");
        check(guard.parameters[0].type() == FIELD_UI32 &&
                  *static_cast<uint32 const*>(guard.parameters[0].buff()) ==
                      (i == 0 ? 1u : 2u),
              "guard guid order/type");
        check(guard.parameters[1].type() == FIELD_UI32 &&
                  *static_cast<uint32 const*>(guard.parameters[1].buff()) == 7,
              "guard account type");
        check(parameterString(guard.parameters[2]) ==
                  (i == 0 ? "Alice Smith" : "Bob Jones"),
              "guard name");
        check(guard.parameters[3].type() == FIELD_UI64 &&
                  parameterUInt64(guard.parameters[3]) ==
                      (i == 0 ? 11u : 22u),
              "guard generation");
        check(guard.parameters[4].type() == FIELD_UI32 &&
                  *static_cast<uint32 const*>(guard.parameters[4].buff()) ==
                      (i == 0 ? 1u : 2u),
              "guard ownership guid");
        check(parameterString(guard.parameters[5]) ==
                  (i == 0 ? "Alice Smith" : "Bob Jones"),
              "guard ownership name");
    }

    check(context.Execute().outcome == SqlWriteOutcome::Succeeded,
          "ordered guards commit");
    check(connection.observed.size() == 3 &&
              connection.observed[0].sql == guard1Sql &&
              connection.observed[1].sql == guard2Sql &&
              connection.observed[2].sql == "related",
          "executor order guard1 guard2 payload");
}

void secondCharacterGuardFailureStopsPayload()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 0)};
    ExecutorDatabase database(connection, state);
    CharacterPersistenceContext context(database, 22);
    check(context.AppendIdentityGuards(
              {identity(1, "Alice Smith", 1), identity(2, "Bob Jones", 1)}),
          "two guards appended");
    append(context, "payload");
    std::string const guard1Sql = context.Batch().Writes()[0].sql;
    std::string const guard2Sql = context.Batch().Writes()[1].sql;
    int acknowledgments = 0;
    context.OnCommit([&]() { ++acknowledgments; });

    SqlBatchResult const result = context.Execute();
    check(result.outcome == SqlWriteOutcome::Failed &&
              result.stage == SqlBatchStage::RowCount &&
              result.statementIndex == 1,
          "second guard failure stops batch");
    check(connection.events == std::vector<std::string>{
              "raw:START TRANSACTION",
              "stmt:" + guard1Sql,
              "stmt:" + guard2Sql,
              "raw:ROLLBACK"},
          "no payload after second guard failure");
    check(connection.commitCalls == 0 && connection.rollbackCalls == 1,
          "second guard failure rolls back once");
    check(acknowledgments == 0, "second guard failure no acknowledgment");
}

void staleGuardStopsPayload()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {result(SqlWriteOutcome::Succeeded, 0)};
    ExecutorDatabase database(connection, state);
    CharacterPersistenceContext context(database, 23);
    check(context.AppendIdentityGuards({identity(1, "Alice Smith", 1)}),
          "stale guard appended");
    append(context, "payload");
    int acknowledgments = 0;
    context.OnCommit([&]() { ++acknowledgments; });
    SqlBatchResult const result = context.Execute();
    check(result.outcome == SqlWriteOutcome::Failed &&
              result.stage == SqlBatchStage::RowCount,
          "stale guard rejected");
    check(connection.observed.size() == 1, "stale payload absent");
    check(acknowledgments == 0, "stale no generation acknowledgment");
}

void successfulGuardsAcknowledgeAfterCommit()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);
    CharacterPersistenceContext context(database, 24);
    check(context.AppendIdentityGuards({identity(1, "Alice Smith", 5)}),
          "success guard appended");
    uint64 expectedGeneration = 5;
    bool advanced = false;
    context.OnCommit([&]() {
        ++expectedGeneration;
        advanced = true;
    });
    check(expectedGeneration == 5 && !advanced,
          "snapshot unchanged before execution");
    check(context.Execute().outcome == SqlWriteOutcome::Succeeded,
          "guard success");
    check(advanced && expectedGeneration == 6,
          "generation advances once after commit");
    check(context.Execute().outcome == SqlWriteOutcome::NotExecuted &&
              expectedGeneration == 6,
          "second Execute no repeat acknowledgment");
}

void uncertainCommitDoesNotAdvanceGenerations()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {result(SqlWriteOutcome::Succeeded, 1)};
    connection.commit = result(SqlWriteOutcome::OutcomeUnknown, 0, 2013);
    ExecutorDatabase database(connection, state);
    CharacterPersistenceContext context(database, 25);
    check(context.AppendIdentityGuards({identity(1, "Alice Smith", 5)}),
          "uncertain guard appended");
    uint64 generation = 5;
    context.OnCommit([&]() { ++generation; });
    SqlBatchResult const result = context.Execute();
    check(result.outcome == SqlWriteOutcome::OutcomeUnknown,
          "uncertain commit result");
    check(generation == 5, "uncertain commit no generation advance");
    check(connection.commitCalls == 1, "uncertain commit not retried");
}

void guardAssemblyMustBeFirstAndSingle()
{
    DatabaseState state;
    FakeConnection connection;
    ExecutorDatabase database(connection, state);

    CharacterPersistenceContext prefilled(database, 26);
    append(prefilled, "payload");
    check(!prefilled.AppendIdentityGuards(
              {identity(1, "Alice Smith", 1)}),
          "guards after payload rejected");
    check(prefilled.IsInvalid(), "prefilled context invalidated");

    CharacterPersistenceContext twice(database, 27);
    check(twice.AppendIdentityGuards({identity(1, "Alice Smith", 1)}),
          "first guard set accepted");
    check(!twice.AppendIdentityGuards({identity(2, "Bob Jones", 1)}),
          "second guard set rejected");
    check(twice.IsInvalid() && twice.Batch().Writes().size() == 1,
          "second guard set leaves no partial append");

    CharacterPersistenceContext submitted(database, 28);
    check(submitted.AppendIdentityGuards({identity(1, "Alice Smith", 1)}),
          "guard before submit");
    connection.statements = {result(SqlWriteOutcome::Succeeded, 1)};
    check(submitted.Execute().outcome == SqlWriteOutcome::Succeeded,
          "submitted guard success");
    check(!submitted.AppendIdentityGuards({identity(2, "Bob Jones", 1)}),
          "submitted context rejects guards");
}

void snapshotValuesAreOwned()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);
    CharacterPersistenceContext context(database, 29);
    {
        std::vector<CharacterIdentitySnapshot> identities = {
            identity(1, "Alice Smith", 18446744073709551614ULL)};
        check(context.AppendIdentityGuards(identities), "snapshot append");
        identities[0].canonicalName = "Mutated Name";
        identities[0].generation = 0;
        identities.clear();
    }
    check(context.Execute().outcome == SqlWriteOutcome::Succeeded,
          "owned snapshot executes");
    check(connection.observed.size() == 1 &&
              connection.observed[0].parameters.size() == 6 &&
              parameterString(connection.observed[0].parameters[2]) ==
                  "Alice Smith" &&
              parameterUInt64(connection.observed[0].parameters[3]) ==
                  18446744073709551614ULL,
          "owned guard values retained");
}

void generationBoundary()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);
    CharacterPersistenceContext accepted(database, 30);
    check(accepted.AppendIdentityGuards(
              {identity(1, "Alice Smith", 18446744073709551614ULL)}),
          "maximum minus one accepted");
    check(accepted.Execute().outcome == SqlWriteOutcome::Succeeded,
          "maximum minus one executes");

    DatabaseState rejectedState;
    FakeConnection rejectedConnection;
    ExecutorDatabase rejectedDatabase(rejectedConnection, rejectedState);
    CharacterPersistenceContext rejected(rejectedDatabase, 31);
    check(!rejected.AppendIdentityGuards(
              {identity(1, "Alice Smith", 18446744073709551615ULL)}),
          "maximum generation rejected locally");
    check(rejected.Execute().outcome == SqlWriteOutcome::NotExecuted &&
              rejectedState.submissions == 0,
          "maximum generation cannot submit");
}

MailMoneyWithdrawal withdrawal(uint32 mailId, uint32 amount,
                               uint32 balance = 100,
                               uint32 cap = 1000)
{
    MailMoneyWithdrawal request;
    request.identity = identity(1, "Alice Smith", 7);
    request.mailId = mailId;
    request.amount = amount;
    request.currentBalance = balance;
    request.maximumBalance = cap;
    return request;
}

void validWithdrawalAssembly()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);
    CharacterPersistenceContext context(database, 40);
    uint32 projected = 0;
    MailMoneyWithdrawal request = withdrawal(42, 25);
    check(AppendMailMoneyWithdrawal(database, context, request, projected),
          "valid withdrawal assembles");
    check(projected == 125, "projected balance proposal");
    check(state.submissions == 0, "assembly does not submit");

    SqlCheckedBatch& batch = context.Batch();
    check(batch.Writes().size() == 3, "guard, mail, balance");
    check(batch.Writes()[0].affectedRowsPolicy ==
              SqlAffectedRowsPolicy::ExactlyOne &&
              batch.Writes()[1].affectedRowsPolicy ==
              SqlAffectedRowsPolicy::ExactlyOne &&
              batch.Writes()[2].affectedRowsPolicy ==
              SqlAffectedRowsPolicy::Any,
          "withdrawal policies");
    check(batch.Writes()[1].sql.find("UPDATE `mail` SET `money`=0") !=
              std::string::npos &&
              batch.Writes()[1].sql.find("`receiver_guid`=?") !=
                  std::string::npos &&
              batch.Writes()[1].sql.find("`money`=?") != std::string::npos,
          "mail claim SQL");
    check(batch.Writes()[1].parameters.size() == 3 &&
              *static_cast<uint32 const*>(
                  batch.Writes()[1].parameters[0].buff()) == 42 &&
              *static_cast<uint32 const*>(
                  batch.Writes()[1].parameters[1].buff()) == 1 &&
              *static_cast<uint32 const*>(
                  batch.Writes()[1].parameters[2].buff()) == 25,
          "mail claim parameters");
    check(batch.Writes()[2].parameters.size() == 5 &&
              *static_cast<uint32 const*>(
                  batch.Writes()[2].parameters[0].buff()) == 125 &&
              parameterString(batch.Writes()[2].parameters[3]) ==
                  "Alice Smith" &&
              parameterUInt64(batch.Writes()[2].parameters[4]) == 8,
          "balance parameters and revision");
    std::string const guardSql = batch.Writes()[0].sql;
    std::string const mailSql = batch.Writes()[1].sql;
    std::string const balanceSql = batch.Writes()[2].sql;

    check(context.Execute().outcome == SqlWriteOutcome::Succeeded,
          "withdrawal executor success");
    check(connection.observed.size() == 3 &&
              connection.observed[0].sql == guardSql &&
              connection.observed[1].sql == mailSql &&
              connection.observed[2].sql == balanceSql,
          "withdrawal ordered execution");
}

void invalidWithdrawalDoesNotSubmit()
{
    struct Case
    {
        char const* label;
        MailMoneyWithdrawal request;
    };
    std::vector<Case> cases;
    cases.push_back({"zero mail", withdrawal(0, 25)});
    cases.push_back({"zero amount", withdrawal(42, 0)});
    cases.push_back({"balance above cap", withdrawal(42, 25, 1001, 1000)});
    cases.push_back({"sum above cap", withdrawal(42, 200, 900, 1000)});
    cases.push_back({"uint32 overflow", withdrawal(
        42, 1, std::numeric_limits<uint32>::max(),
        std::numeric_limits<uint32>::max())});
    MailMoneyWithdrawal badIdentity = withdrawal(42, 25);
    badIdentity.identity.canonicalName = "alice smith";
    cases.push_back({"invalid identity", badIdentity});

    for (Case const& item : cases)
    {
        DatabaseState state;
        FakeConnection connection;
        ExecutorDatabase database(connection, state);
        CharacterPersistenceContext context(database, 41);
        uint32 projected = 77;
        check(!AppendMailMoneyWithdrawal(
                  database, context, item.request, projected),
              item.label);
        check(projected == 77, std::string(item.label) + " projected unchanged");
        check(context.IsInvalid(), std::string(item.label) + " invalidated");
        check(context.Execute().outcome == SqlWriteOutcome::NotExecuted &&
                  state.submissions == 0,
              std::string(item.label) + " no submission");
    }

    DatabaseState state;
    FakeConnection firstConnection;
    FakeConnection secondConnection;
    ExecutorDatabase first(firstConnection, state);
    ExecutorDatabase second(secondConnection, state);
    CharacterPersistenceContext wrongDatabase(first, 42);
    uint32 projected = 77;
    MailMoneyWithdrawal request = withdrawal(42, 25);
    check(!AppendMailMoneyWithdrawal(second, wrongDatabase, request, projected),
          "wrong database rejected");
    check(wrongDatabase.IsInvalid(), "wrong database invalidated");

    CharacterPersistenceContext prefilled(first, 43);
    append(prefilled, "payload");
    check(!AppendMailMoneyWithdrawal(first, prefilled, request, projected),
          "nonempty context rejected");

    CharacterPersistenceContext submitted(first, 44);
    check(AppendIdentityGuards(submitted, request.identity),
          "submitted context guard");
    firstConnection.statements = {result(SqlWriteOutcome::Succeeded, 1)};
    check(submitted.Execute().outcome == SqlWriteOutcome::Succeeded,
          "submitted context success");
    check(!AppendMailMoneyWithdrawal(first, submitted, request, projected),
          "submitted context rejects withdrawal");
}

void mailClaimFailureStopsBalanceWrite()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 0)};
    ExecutorDatabase database(connection, state);
    CharacterPersistenceContext context(database, 45);
    uint32 projected = 0;
    check(AppendMailMoneyWithdrawal(
              database, context, withdrawal(42, 25), projected),
          "claim fixture assembles");
    int acknowledgments = 0;
    context.OnCommit([&]() { ++acknowledgments; });
    SqlBatchResult const batch = context.Execute();
    check(batch.outcome == SqlWriteOutcome::Failed &&
              batch.statementIndex == 1,
          "mail claim failure result");
    check(connection.observed.size() == 2, "balance write absent");
    check(connection.rollbackCalls == 1 && connection.commitCalls == 0,
          "mail claim failure rollback");
    check(acknowledgments == 0, "mail claim failure no ack");
}

void balanceWriteFailureRollsBackClaim()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Failed, 0, 1146)};
    ExecutorDatabase database(connection, state);
    CharacterPersistenceContext context(database, 46);
    uint32 projected = 0;
    check(AppendMailMoneyWithdrawal(
              database, context, withdrawal(42, 25), projected),
          "balance failure fixture assembles");
    int acknowledgments = 0;
    context.OnCommit([&]() { ++acknowledgments; });
    SqlBatchResult const batch = context.Execute();
    check(batch.outcome == SqlWriteOutcome::Failed &&
              batch.statementIndex == 2,
          "balance failure result");
    check(connection.rollbackCalls == 1 && acknowledgments == 0,
          "balance failure rollback/no ack");
}

void unchangedStoredBalanceCanCommit()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 0)};
    ExecutorDatabase database(connection, state);
    CharacterPersistenceContext context(database, 47);
    uint32 projected = 0;
    check(AppendMailMoneyWithdrawal(
              database, context, withdrawal(42, 25), projected),
          "unchanged balance fixture assembles");
    SqlBatchResult const batch = context.Execute();
    check(batch.outcome == SqlWriteOutcome::Succeeded &&
              batch.stage == SqlBatchStage::Complete,
          "unchanged final balance commits under Any");
}

void successfulWithdrawalPublishesOnlyAfterCommit()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);
    CharacterPersistenceContext context(database, 48);
    uint32 projected = 0;
    MailMoneyWithdrawal request = withdrawal(42, 25);
    check(AppendMailMoneyWithdrawal(database, context, request, projected),
          "publication fixture assembles");
    uint32 balance = request.currentBalance;
    uint32 mailMoney = request.amount;
    uint64 generation = request.identity.generation;
    context.OnCommit([&]() {
        balance = projected;
        mailMoney = 0;
        ++generation;
    });
    check(balance == 100 && mailMoney == 25 && generation == 7,
          "pre-commit values unchanged");
    check(context.Execute().outcome == SqlWriteOutcome::Succeeded,
          "publication execution success");
    check(balance == 125 && mailMoney == 0 && generation == 8,
          "post-commit values updated once");
    check(context.Execute().outcome == SqlWriteOutcome::NotExecuted &&
              balance == 125 && generation == 8,
          "no replay after success");
}

void uncertainWithdrawalDoesNotPublish()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1)};
    connection.commit = result(SqlWriteOutcome::OutcomeUnknown, 0, 2013);
    ExecutorDatabase database(connection, state);
    CharacterPersistenceContext context(database, 49);
    uint32 projected = 0;
    check(AppendMailMoneyWithdrawal(
              database, context, withdrawal(42, 25), projected),
          "uncertain withdrawal assembles");
    uint32 balance = 100;
    uint32 mailMoney = 25;
    uint64 generation = 7;
    context.OnCommit([&]() {
        balance = projected;
        mailMoney = 0;
        ++generation;
    });
    check(context.Execute().outcome == SqlWriteOutcome::OutcomeUnknown,
          "uncertain withdrawal result");
    check(balance == 100 && mailMoney == 25 && generation == 7,
          "uncertain withdrawal no publication");
    check(connection.commitCalls == 1, "uncertain withdrawal no retry");
}

void ownedWithdrawalValues()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);
    CharacterPersistenceContext context(database, 50);
    uint32 projected = 0;
    {
        MailMoneyWithdrawal request = withdrawal(42, 25);
        check(AppendMailMoneyWithdrawal(database, context, request, projected),
              "owned request assembles");
        request.mailId = 99;
        request.amount = 1;
        request.currentBalance = 0;
        request.identity.canonicalName = "Mutated Name";
    }
    check(projected == 125, "owned request projected value");
    check(context.Execute().outcome == SqlWriteOutcome::Succeeded,
          "owned request executes");
    check(connection.observed.size() == 3 &&
              *static_cast<uint32 const*>(
                  connection.observed[1].parameters[0].buff()) == 42 &&
              *static_cast<uint32 const*>(
                  connection.observed[1].parameters[2].buff()) == 25 &&
              parameterString(connection.observed[2].parameters[3]) ==
                  "Alice Smith",
          "owned request values retained");
}

void setupOwner(Player& player, WorldSession& session)
{
    player.guid = 1;
    player.session = &session;
    session.accountId = 7;
    player.name = "Alice Smith";
    player.generation = 3;
}

CharacterIdentitySnapshot ownerIdentity(Player& player)
{
    CharacterIdentitySnapshot identity;
    identity.guid = player.GetGUIDLow();
    identity.accountId = player.GetSession()->GetAccountId();
    identity.canonicalName = player.GetName();
    identity.generation = player.GetIdentityGeneration();
    return identity;
}

CharacterIdentitySnapshot ownerIdentity(MasterPlayer& player)
{
    CharacterIdentitySnapshot identity;
    identity.guid = player.GetGUIDLow();
    identity.accountId = player.GetSession()->GetAccountId();
    identity.canonicalName = player.name;
    identity.generation = player.identityGeneration;
    return identity;
}

void ownerContext(ExecutorDatabase& database, CharacterPersistenceContext& context,
                  Player& player)
{
    check(context.AppendIdentityGuards({ownerIdentity(player)}),
          "owner guard append");
}

void bgDataAssemblyAndAck()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 0),
        result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);
    WorldSession session;
    Player player;
    setupOwner(player, session);
    player.m_bgData.m_needSave = true;
    player.m_bgData.bgInstanceID = 7;
    player.m_bgData.bgTeam = TEAM_HORDE;
    player.m_bgData.joinPos = {1.0f, 2.0f, 3.0f, 4.0f, 530};
    CharacterPersistenceContext context(database, 60);
    ownerContext(database, context, player);
    check(player.AppendBGData(context), "BG data assembly");
    check(player.m_bgData.m_needSave, "assembly preserves BG dirty");
    check(context.Execute().outcome == SqlWriteOutcome::Succeeded,
          "BG commit");
    check(!player.m_bgData.m_needSave, "commit clears captured BG dirty");

    DatabaseState deleteState;
    FakeConnection deleteConnection;
    deleteConnection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 0)};
    ExecutorDatabase deleteDatabase(deleteConnection, deleteState);
    Player deletePlayer;
    setupOwner(deletePlayer, session);
    deletePlayer.m_bgData.m_needSave = true;
    CharacterPersistenceContext deleteContext(deleteDatabase, 61);
    ownerContext(deleteDatabase, deleteContext, deletePlayer);
    check(deletePlayer.AppendBGData(deleteContext), "delete-only assembly");
    deleteContext.Execute();
    check(deleteConnection.observed.size() == 2 &&
              deleteConnection.observed[1].sql.find("DELETE FROM `character_battleground_data`") !=
                  std::string::npos,
          "BG delete-only payload");
}

void bgDataFailureAndNewerState()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 0),
        result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);
    WorldSession session;
    Player player;
    setupOwner(player, session);
    player.m_bgData.m_needSave = true;
    player.m_bgData.bgInstanceID = 7;
    CharacterPersistenceContext context(database, 62);
    ownerContext(database, context, player);
    check(player.AppendBGData(context), "BG failure assembly");
    player.m_bgData.joinPos.x = 99.0f;
    check(context.Execute().outcome == SqlWriteOutcome::Succeeded,
          "BG changed fixture commit");
    check(player.m_bgData.m_needSave,
          "newer BG value prevents stale dirty clear");

    DatabaseState rollbackState;
    FakeConnection rollbackConnection;
    rollbackConnection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 0),
        result(SqlWriteOutcome::Succeeded, 1)};
    rollbackConnection.commit = result(SqlWriteOutcome::OutcomeUnknown, 0, 2013);
    ExecutorDatabase rollbackDatabase(rollbackConnection, rollbackState);
    Player rollbackPlayer;
    setupOwner(rollbackPlayer, session);
    rollbackPlayer.m_bgData.m_needSave = true;
    rollbackPlayer.m_bgData.bgInstanceID = 7;
    CharacterPersistenceContext rollbackContext(rollbackDatabase, 63);
    ownerContext(rollbackDatabase, rollbackContext, rollbackPlayer);
    rollbackPlayer.AppendBGData(rollbackContext);
    check(rollbackContext.Execute().outcome ==
              SqlWriteOutcome::OutcomeUnknown,
          "BG uncertain outcome");
    check(rollbackPlayer.m_bgData.m_needSave,
          "BG uncertain preserves dirty");
}

void reputationAssemblyAndAck()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 0),
        result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);
    WorldSession session;
    Player player;
    setupOwner(player, session);
    ReputationMgr reputation(&player);
    FactionState dirty;
    dirty.ID = 100;
    dirty.ReputationListID = 10;
    dirty.Standing = -500;
    dirty.Flags = 2;
    dirty.needSend = true;
    dirty.needSave = true;
    reputation.m_factions[10] = dirty;
    FactionState clean;
    clean.ID = 101;
    clean.ReputationListID = 11;
    clean.needSave = false;
    reputation.m_factions[11] = clean;

    CharacterPersistenceContext context(database, 64);
    ownerContext(database, context, player);
    check(reputation.AppendSaveToDB(context), "reputation assembly");
    check(reputation.m_factions[10].needSave,
          "reputation assembly preserves dirty");
    check(connection.observed.empty(), "reputation assembly no execute");
    std::vector<SqlBoundWrite> const writes = context.Batch().Writes();
    check(writes.size() == 3 &&
              writes[1].affectedRowsPolicy == SqlAffectedRowsPolicy::Any &&
              writes[2].affectedRowsPolicy == SqlAffectedRowsPolicy::ExactlyOne,
          "reputation policies");
    check(context.Execute().outcome == SqlWriteOutcome::Succeeded,
          "reputation commit");
    check(!reputation.m_factions[10].needSave,
          "reputation commit clears captured dirty");
    check(reputation.m_factions[10].needSend,
          "reputation acknowledgment preserves needSend");
    check(connection.observed.size() == 3, "dirty faction SQL + cleanup");
    check(connection.observed[2].parameters[2].type() == FIELD_I32 &&
              *static_cast<int32 const*>(
                  connection.observed[2].parameters[2].buff()) == -500,
          "negative reputation binding");
}

void reputationNewerValuePreserved()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 0),
        result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);
    WorldSession session;
    Player player;
    setupOwner(player, session);
    ReputationMgr reputation(&player);
    FactionState dirty;
    dirty.ID = 100;
    dirty.ReputationListID = 10;
    dirty.Standing = 10;
    dirty.Flags = 1;
    dirty.needSave = true;
    reputation.m_factions[10] = dirty;
    CharacterPersistenceContext context(database, 65);
    ownerContext(database, context, player);
    check(reputation.AppendSaveToDB(context), "reputation newer assembly");
    reputation.m_factions[10].Flags = 9;
    reputation.m_factions[10].ReputationListID = 11;
    check(context.Execute().outcome == SqlWriteOutcome::Succeeded,
          "reputation newer commit");
    check(reputation.m_factions[10].needSave,
          "newer reputation values preserve dirty");
}

void actionAssemblyAndAck()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 0),
        result(SqlWriteOutcome::Succeeded, 0)};
    ExecutorDatabase database(connection, state);
    WorldSession session;
    MasterPlayer master(&session);
    session.accountId = 7;
    master.guid = 1;
    master.name = "Alice Smith";
    master.identityGeneration = 3;
    ActionButton fresh;
    fresh.packedData = 100;
    fresh.uState = ACTIONBUTTON_NEW;
    master.m_actionButtons[1] = fresh;
    ActionButton changed;
    changed.packedData = 200;
    changed.uState = ACTIONBUTTON_CHANGED;
    master.m_actionButtons[2] = changed;
    ActionButton deleted;
    deleted.packedData = 300;
    deleted.uState = ACTIONBUTTON_DELETED;
    master.m_actionButtons[3] = deleted;
    ActionButton unchanged;
    unchanged.packedData = 400;
    unchanged.uState = ACTIONBUTTON_UNCHANGED;
    master.m_actionButtons[4] = unchanged;

    CharacterPersistenceContext context(database, 66);
    check(context.AppendIdentityGuards({ownerIdentity(master)}),
          "action owner guard");
    check(master.AppendActionsToDB(context), "action assembly");
    check(master.m_actionButtons[1].uState == ACTIONBUTTON_NEW,
          "action assembly preserves new");
    check(master.m_actionButtons[3].uState == ACTIONBUTTON_DELETED,
          "action assembly preserves deleted");
    std::vector<SqlBoundWrite> const writes = context.Batch().Writes();
    check(writes.size() == 4 &&
              writes[1].affectedRowsPolicy == SqlAffectedRowsPolicy::ExactlyOne &&
              writes[2].affectedRowsPolicy == SqlAffectedRowsPolicy::Any &&
              writes[3].affectedRowsPolicy == SqlAffectedRowsPolicy::Any,
          "action policies/order");
    check(context.Execute().outcome == SqlWriteOutcome::Succeeded,
          "action commit");
    check(master.m_actionButtons[1].uState == ACTIONBUTTON_UNCHANGED &&
              master.m_actionButtons[2].uState == ACTIONBUTTON_UNCHANGED,
          "new/changed acknowledged");
    check(master.m_actionButtons.find(3) == master.m_actionButtons.end(),
          "deleted acknowledged and erased");
    check(master.m_actionButtons[4].uState == ACTIONBUTTON_UNCHANGED,
          "unchanged untouched");
}

void actionNewerStatePreserved()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);
    WorldSession session;
    MasterPlayer master(&session);
    session.accountId = 7;
    master.guid = 1;
    master.name = "Alice Smith";
    master.identityGeneration = 3;
    ActionButton action;
    action.packedData = 100;
    action.uState = ACTIONBUTTON_NEW;
    master.m_actionButtons[1] = action;
    CharacterPersistenceContext context(database, 67);
    check(context.AppendIdentityGuards({ownerIdentity(master)}), "action guard");
    check(master.AppendActionsToDB(context), "action newer assembly");
    master.m_actionButtons[1].packedData = 999;
    check(context.Execute().outcome == SqlWriteOutcome::Succeeded,
          "action newer commit");
    check(master.m_actionButtons[1].uState == ACTIONBUTTON_CHANGED,
          "newer packed action becomes CHANGED");
}

void composedWriterRollback()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),  // identity guard
        result(SqlWriteOutcome::Succeeded, 0),  // BG delete
        result(SqlWriteOutcome::Succeeded, 1),  // BG insert
        result(SqlWriteOutcome::Succeeded, 0),  // rep delete
        result(SqlWriteOutcome::Succeeded, 1),  // rep insert
        result(SqlWriteOutcome::Failed, 0, 1146)}; // action
    connection.commit = result(SqlWriteOutcome::Succeeded, 1);
    ExecutorDatabase database(connection, state);
    WorldSession session;
    Player player;
    setupOwner(player, session);
    player.m_bgData.m_needSave = true;
    player.m_bgData.bgInstanceID = 7;
    ReputationMgr reputation(&player);
    FactionState rep;
    rep.ID = 100;
    rep.ReputationListID = 10;
    rep.needSave = true;
    reputation.m_factions[10] = rep;
    MasterPlayer master(&session);
    master.guid = 1;
    master.name = "Alice Smith";
    master.identityGeneration = 3;
    ActionButton action;
    action.packedData = 100;
    action.uState = ACTIONBUTTON_NEW;
    master.m_actionButtons[1] = action;

    CharacterPersistenceContext context(database, 68);
    check(context.AppendIdentityGuards({ownerIdentity(player)}), "composed guard");
    check(player.AppendBGData(context), "composed BG");
    check(reputation.AppendSaveToDB(context), "composed reputation");
    check(master.AppendActionsToDB(context), "composed action");
    SqlBatchResult const composedResult = context.Execute();
    check(composedResult.outcome == SqlWriteOutcome::Failed &&
              composedResult.statementIndex == 5,
          "composed failure index");
    check(connection.rollbackCalls == 1 && connection.commitCalls == 0,
          "composed rollback");
    check(player.m_bgData.m_needSave && reputation.m_factions[10].needSave &&
              master.m_actionButtons[1].uState == ACTIONBUTTON_NEW,
          "composed failure acknowledges none");

    DatabaseState successState;
    FakeConnection successConnection;
    successConnection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 0),
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 0),
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase successDatabase(successConnection, successState);
    Player successPlayer;
    setupOwner(successPlayer, session);
    successPlayer.m_bgData.m_needSave = true;
    successPlayer.m_bgData.bgInstanceID = 7;
    ReputationMgr successRep(&successPlayer);
    FactionState successFaction;
    successFaction.ID = 100;
    successFaction.ReputationListID = 10;
    successFaction.needSave = true;
    successRep.m_factions[10] = successFaction;
    MasterPlayer successMaster(&session);
    successMaster.guid = 1;
    successMaster.name = "Alice Smith";
    successMaster.identityGeneration = 3;
    ActionButton successAction;
    successAction.packedData = 100;
    successAction.uState = ACTIONBUTTON_NEW;
    successMaster.m_actionButtons[1] = successAction;
    CharacterPersistenceContext successContext(successDatabase, 69);
    check(successContext.AppendIdentityGuards({ownerIdentity(successPlayer)}),
          "successful composed guard");
    check(successPlayer.AppendBGData(successContext), "successful composed BG");
    check(successRep.AppendSaveToDB(successContext), "successful composed reputation");
    check(successMaster.AppendActionsToDB(successContext), "successful composed action");
    check(successContext.Execute().outcome == SqlWriteOutcome::Succeeded,
          "successful composed commit");
    check(!successPlayer.m_bgData.m_needSave &&
              !successRep.m_factions[10].needSave &&
              successMaster.m_actionButtons[1].uState == ACTIONBUTTON_UNCHANGED,
          "successful composed acknowledges all");
}

void missingGuardRejectsWriters()
{
    DatabaseState state;
    FakeConnection connection;
    ExecutorDatabase database(connection, state);
    WorldSession session;
    Player player;
    setupOwner(player, session);

    CharacterPersistenceContext bg(database, 70);
    check(!player.AppendBGData(bg) && bg.IsInvalid(),
          "BG writer requires staged guard");
    CharacterPersistenceContext rep(database, 71);
    ReputationMgr reputation(&player);
    check(!reputation.AppendSaveToDB(rep) && rep.IsInvalid(),
          "reputation writer requires staged guard");
    CharacterPersistenceContext actions(database, 72);
    MasterPlayer master(&session);
    master.guid = 1;
    master.name = "Alice Smith";
    master.identityGeneration = 3;
    check(!master.AppendActionsToDB(actions) && actions.IsInvalid(),
          "actions writer requires staged guard");
    check(state.submissions == 0, "missing guards submit nothing");
}

void actionExistenceReconciliation()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);
    WorldSession session;
    session.accountId = 7;
    MasterPlayer master(&session);
    master.guid = 1;
    master.name = "Alice Smith";
    master.identityGeneration = 3;
    ActionButton inserted;
    inserted.packedData = 100;
    inserted.uState = ACTIONBUTTON_NEW;
    master.m_actionButtons[1] = inserted;
    CharacterPersistenceContext context(database, 73);
    check(context.AppendIdentityGuards({ownerIdentity(master)}), "tombstone guard");
    check(master.AppendActionsToDB(context), "tombstone assembly");
    master.m_actionButtons.erase(1);
    check(context.Execute().outcome == SqlWriteOutcome::Succeeded,
          "tombstone commit");
    check(master.m_actionButtons[1].uState == ACTIONBUTTON_DELETED &&
              master.m_actionButtons[1].packedData == 100,
          "missing committed insertion becomes tombstone");
}

void questWriter()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);
    WorldSession session;
    Player player;
    setupOwner(player, session);
    QuestStatusData quest;
    quest.m_status = QUEST_STATUS_INCOMPLETE;
    quest.m_rewarded = true;
    quest.m_explored = true;
    quest.m_timer = 5000;
    quest.m_reward_choice = 3;
    quest.uState = QUEST_NEW;
    for (uint32 i = 0; i < QUEST_OBJECTIVES_COUNT; ++i)
    {
        quest.m_itemcount[i] = i + 1;
        quest.m_creatureOrGOcount[i] = i + 10;
    }
    player.mQuestStatus[1] = quest;
    CharacterPersistenceContext context(database, 80);
    ownerContext(database, context, player);
    check(player.AppendQuestStatus(context, 100), "quest assembly");
    std::vector<SqlBoundWrite> writes = context.Batch().Writes();
    check(writes.size() == 2 &&
              writes[1].parameters.size() == 15 &&
              parameterUInt64(writes[1].parameters[5]) == 105,
          "quest insert/timer binding");
    check(player.mQuestStatus[1].uState == QUEST_NEW,
          "quest assembly preserves state");
    check(context.Execute().outcome == SqlWriteOutcome::Succeeded,
          "quest commit");
    check(player.mQuestStatus[1].uState == QUEST_UNCHANGED,
          "quest commit acknowledges");

    CharacterPersistenceContext overflow(database, 81);
    ownerContext(database, overflow, player);
    player.mQuestStatus[1].uState = QUEST_CHANGED;
    player.mQuestStatus[1].m_timer = std::numeric_limits<uint32>::max();
    check(!player.AppendQuestStatus(overflow, std::numeric_limits<uint64>::max()),
          "quest overflow rejected");
    check(overflow.IsInvalid() && overflow.Execute().outcome ==
              SqlWriteOutcome::NotExecuted,
          "quest overflow cannot submit");
}

void skillWriter()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);
    WorldSession session;
    Player player;
    setupOwner(player, session);
    player.values[1] = (10u << 16) | 5u;
    player.m_skillStatusMap.emplace(100, SkillStatusData{0, SKILL_NEW});
    CharacterPersistenceContext context(database, 82);
    ownerContext(database, context, player);
    check(player.AppendSkills(context), "skill assembly");
    std::vector<SqlBoundWrite> writes = context.Batch().Writes();
    check(writes.size() == 2 && writes[1].parameters.size() == 4 &&
              *static_cast<uint16 const*>(writes[1].parameters[2].buff()) == 5 &&
              *static_cast<uint16 const*>(writes[1].parameters[3].buff()) == 10,
          "skill value/max binding");
    check(context.Execute().outcome == SqlWriteOutcome::Succeeded,
          "skill commit");
    check(player.m_skillStatusMap.find(100)->second.uState == SKILL_UNCHANGED,
          "skill acknowledged");

    player.m_forgottenSkills[10] = 2;
    player.m_forgottenSkills[11] = 1;
    CharacterPersistenceContext forgotten(database, 91);
    ownerContext(database, forgotten, player);
    check(player.AppendSkills(forgotten), "forgotten assembly");
    std::vector<SqlBoundWrite> forgottenWrites = forgotten.Batch().Writes();
    check(forgottenWrites.size() == 2 &&
              forgottenWrites[1].sql.find("REPLACE INTO `character_forgotten_skills`") !=
                  std::string::npos,
          "forgotten value >1 produces replace");
}

void spellWriter()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);
    WorldSession session;
    Player player;
    setupOwner(player, session);
    PlayerSpell spell;
    spell.state = PLAYERSPELL_NEW;
    spell.active = true;
    player.m_spells[200] = spell;
    CharacterPersistenceContext context(database, 83);
    ownerContext(database, context, player);
    check(player.AppendSpells(context), "spell assembly");
    std::vector<SqlBoundWrite> writes = context.Batch().Writes();
    check(writes.size() == 2 && writes[1].parameters.size() == 4,
          "spell insert binding");
    check(context.Execute().outcome == SqlWriteOutcome::Succeeded,
          "spell commit");
    check(player.m_spells[200].state == PLAYERSPELL_UNCHANGED,
          "spell acknowledged");

    CharacterPersistenceContext dependent(database, 84);
    ownerContext(database, dependent, player);
    player.m_spells[201].state = PLAYERSPELL_NEW;
    player.m_spells[201].dependent = true;
    check(player.AppendSpells(dependent), "dependent assembly");
    check(dependent.Batch().Writes().size() == 1,
          "dependent spell not inserted");
}

void cooldownWriterAndNull()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 0),
        result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);
    WorldSession session;
    Player player;
    setupOwner(player, session);
    SpellEntry spell;
    spell.Id = 5;
    TimePoint now = TimePoint(std::chrono::milliseconds(1000000));
    player.m_cooldownMap[5] = std::unique_ptr<CooldownData>(
        new CooldownData(&spell, 2, 9, now, now, false));
    CharacterPersistenceContext context(database, 85);
    ownerContext(database, context, player);
    check(player.AppendSpellCooldowns(context), "cooldown assembly");
    std::vector<SqlBoundWrite> writes = context.Batch().Writes();
    check(writes.size() == 3 && writes[2].parameters.size() == 6 &&
              *static_cast<uint32 const*>(writes[2].parameters[5].buff()) == 9 &&
              parameterUInt64(writes[2].parameters[2]) == 1000 &&
              parameterUInt64(writes[2].parameters[4]) == 1000,
          "cooldown fields");
    check(context.Execute().outcome == SqlWriteOutcome::Succeeded,
          "cooldown commit");

    CharacterPersistenceContext malformed(database, 86);
    ownerContext(database, malformed, player);
    player.m_cooldownMap[6].reset();
    check(!player.AppendSpellCooldowns(malformed),
          "null cooldown rejected");
    check(malformed.IsInvalid() &&
              malformed.Execute().outcome == SqlWriteOutcome::NotExecuted,
          "null cooldown delete-all cannot submit");
}

void auraWriter()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 0),
        result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);
    WorldSession session;
    Player player;
    setupOwner(player, session);
    SpellAuraHolder holder;
    holder.admitted = true;
    holder.saved.casterGuid = ObjectGuid(42);
    holder.saved.spellId = 7;
    holder.saved.stacks = 2;
    holder.saved.charges = 3;
    holder.saved.damage[0] = 1.5f;
    holder.saved.periodicTime[1] = 9;
    holder.saved.maxDuration = 100;
    holder.saved.duration = 50;
    holder.saved.effIndexMask = 1;
    player.auraHolders[7] = &holder;
    CharacterPersistenceContext context(database, 87);
    ownerContext(database, context, player);
    check(player.AppendAuras(context), "aura assembly");
    std::vector<SqlBoundWrite> writes = context.Batch().Writes();
    check(writes.size() == 3 && writes[2].parameters.size() == 15 &&
              parameterUInt64(writes[2].parameters[1]) == 42 &&
              *static_cast<int32 const*>(writes[2].parameters[13].buff()) == 50,
          "aura 15-field binding");
    check(context.Execute().outcome == SqlWriteOutcome::Succeeded,
          "aura commit");
    player.auraHolders.clear();
}

void statsWriter()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 0),
        result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);
    WorldSession session;
    Player player;
    setupOwner(player, session);
    player.level = 60;
    for (uint32 i = 0; i < 120; ++i)
    {
        player.values[i] = 0;
        player.floats[i] = 0.0f;
    }
    CharacterPersistenceContext skipped(database, 88);
    ownerContext(database, skipped, player);
    check(player.AppendStats(skipped, 61), "stats below threshold skipped");
    check(skipped.Batch().Writes().size() == 1, "stats skip no payload");

    CharacterPersistenceContext context(database, 89);
    ownerContext(database, context, player);
    check(player.AppendStats(context, 60), "stats assembly");
    std::vector<SqlBoundWrite> writes = context.Batch().Writes();
    check(writes.size() == 3 && writes[2].parameters.size() == 29,
          "stats 29 parameters");
    check(context.Execute().outcome == SqlWriteOutcome::Succeeded,
          "stats commit");
}

void tutorialWriter()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);
    WorldSession session;
    Player player;
    setupOwner(player, session);
    player.session = &session;
    session._player = &player;
    session.accountId = 7;
    session.m_tutorials[0] = 11;
    session.m_tutorialState = TUTORIALDATA_NEW;
    CharacterPersistenceContext context(database, 90);
    ownerContext(database, context, player);
    check(session.AppendTutorialsDataToContext(context), "tutorial assembly");
    std::vector<SqlBoundWrite> writes = context.Batch().Writes();
    check(writes.size() == 2 && writes[1].parameters.size() == 9 &&
              *static_cast<uint32 const*>(writes[1].parameters[0].buff()) == 7,
          "tutorial account/order");
    session.m_tutorials[0] = 12;
    check(context.Execute().outcome == SqlWriteOutcome::Succeeded,
          "tutorial commit");
    check(session.m_tutorialState == TUTORIALDATA_CHANGED,
          "newer tutorial payload becomes CHANGED");
}

void allWriterModes()
{
    enum Mode { SUCCESS, GUARD_FAIL, LAST_FAIL, UNKNOWN };
    for (Mode mode : {SUCCESS, GUARD_FAIL, LAST_FAIL, UNKNOWN})
    {
        DatabaseState state;
        FakeConnection connection;
        ExecutorDatabase database(connection, state);
        WorldSession session;
        Player player;
        setupOwner(player, session);
        player.session = &session;
        session._player = &player;
        session.accountId = 7;
        for (uint32 i = 0; i < 120; ++i)
        {
            player.values[i] = i;
            player.floats[i] = float(i);
        }
        player.m_bgData.m_needSave = true;
        player.m_bgData.bgInstanceID = 7;
        ReputationMgr reputation(&player);
        FactionState faction;
        faction.ID = 100;
        faction.ReputationListID = 10;
        faction.needSave = true;
        reputation.m_factions[10] = faction;
        MasterPlayer master(&session);
        master.guid = 1;
        master.name = "Alice Smith";
        master.identityGeneration = 3;
        ActionButton action;
        action.packedData = 100;
        action.uState = ACTIONBUTTON_NEW;
        master.m_actionButtons[1] = action;
        QuestStatusData quest;
        quest.uState = QUEST_NEW;
        player.mQuestStatus[1] = quest;
        player.m_skillStatusMap.emplace(100, SkillStatusData{0, SKILL_NEW});
        PlayerSpell spell;
        spell.state = PLAYERSPELL_NEW;
        spell.active = true;
        player.m_spells[200] = spell;
        SpellEntry spellEntry;
        spellEntry.Id = 5;
        player.m_cooldownMap[5] = std::unique_ptr<CooldownData>(
            new CooldownData(&spellEntry, 2, 9,
                             TimePoint(std::chrono::milliseconds(1000000)),
                             TimePoint(std::chrono::milliseconds(1000000)),
                             false));
        SpellAuraHolder holder;
        holder.admitted = true;
        holder.saved.spellId = 7;
        holder.saved.effIndexMask = 1;
        player.auraHolders[7] = &holder;
        session.m_tutorials[0] = 11;
        session.m_tutorialState = TUTORIALDATA_NEW;

        CharacterPersistenceContext context(database, 100 + mode);
        ownerContext(database, context, player);
        check(player.AppendBGData(context), "all BG");
        check(reputation.AppendSaveToDB(context), "all reputation");
        check(master.AppendActionsToDB(context), "all actions");
        check(player.AppendQuestStatus(context, 100), "all quests");
        check(player.AppendSkills(context), "all skills");
        check(player.AppendSpells(context), "all spells");
        check(player.AppendSpellCooldowns(context), "all cooldowns");
        check(player.AppendAuras(context), "all auras");
        check(player.AppendStats(context, 60), "all stats");
        check(session.AppendTutorialsDataToContext(context), "all tutorials");

        size_t const count = context.Batch().Writes().size();
        connection.statements.assign(count, result(SqlWriteOutcome::Succeeded, 1));
        if (mode == GUARD_FAIL)
            connection.statements[0] = result(SqlWriteOutcome::Succeeded, 0);
        if (mode == LAST_FAIL)
            connection.statements.back() =
                result(SqlWriteOutcome::Failed, 0, 1146);
        if (mode == UNKNOWN)
            connection.commit =
                result(SqlWriteOutcome::OutcomeUnknown, 0, 2013);

        check(player.m_bgData.m_needSave && reputation.m_factions[10].needSave &&
                  master.m_actionButtons[1].uState == ACTIONBUTTON_NEW &&
                  player.mQuestStatus[1].uState == QUEST_NEW &&
                  player.m_skillStatusMap.find(100)->second.uState == SKILL_NEW &&
                  player.m_spells[200].state == PLAYERSPELL_NEW &&
                  session.m_tutorialState == TUTORIALDATA_NEW,
              "all assembly preserves dirty state");

        std::vector<std::string> reviewExpected={"raw:START TRANSACTION"};
        auto const& reviewWrites=context.Batch().Writes();
        std::size_t reviewCount=mode==GUARD_FAIL?1:reviewWrites.size();
        for(std::size_t i=0;i<reviewCount;++i)reviewExpected.push_back("stmt:"+reviewWrites[i].sql);
        if(mode==SUCCESS || mode==UNKNOWN)reviewExpected.push_back("raw:COMMIT");
        if(mode!=SUCCESS)reviewExpected.push_back("raw:ROLLBACK");
        int reviewGenerations=0;
        context.OnCommit([&](){
            check(!player.m_bgData.m_needSave && !reputation.m_factions[10].needSave &&
                  master.m_actionButtons[1].uState==ACTIONBUTTON_UNCHANGED &&
                  player.mQuestStatus[1].uState==QUEST_UNCHANGED &&
                  player.m_skillStatusMap.at(100).uState==SKILL_UNCHANGED &&
                  player.m_spells[200].state==PLAYERSPELL_UNCHANGED &&
                  session.m_tutorialState==TUTORIALDATA_UNCHANGED,"review leaves before generation");
            ++player.generation; ++master.identityGeneration; ++reviewGenerations;
        });
        SqlBatchResult const result = context.Execute();
        check(connection.events==reviewExpected,"review all-writer exact event order");
        check(reviewGenerations==(mode==SUCCESS?1:0),"review generation acknowledgment count");
        auto reviewSubmissions=state.submissions; auto reviewEvents=connection.events;
        check(context.Execute().outcome==SqlWriteOutcome::NotExecuted && state.submissions==reviewSubmissions &&
              connection.events==reviewEvents && reviewGenerations==(mode==SUCCESS?1:0),"review composed second execution no-op");

        if (mode == SUCCESS)
        {
            check(result.outcome == SqlWriteOutcome::Succeeded &&
                      connection.commitCalls == 1 && connection.rollbackCalls == 0,
                  "all success commit");
            check(!player.m_bgData.m_needSave &&
                      !reputation.m_factions[10].needSave &&
                      master.m_actionButtons[1].uState == ACTIONBUTTON_UNCHANGED &&
                      player.mQuestStatus[1].uState == QUEST_UNCHANGED &&
                      player.m_skillStatusMap.find(100)->second.uState == SKILL_UNCHANGED &&
                      player.m_spells[200].state == PLAYERSPELL_UNCHANGED &&
                      session.m_tutorialState == TUTORIALDATA_UNCHANGED,
                  "all success acknowledges every owner");
        }
        else
        {
            check(result.outcome != SqlWriteOutcome::Succeeded &&
                      connection.commitCalls == (mode == UNKNOWN ? 1 : 0) &&
                      connection.rollbackCalls == 1,
                  "all failure/unknown rollback");
            check(player.m_bgData.m_needSave &&
                      reputation.m_factions[10].needSave &&
                      master.m_actionButtons[1].uState == ACTIONBUTTON_NEW &&
                      player.mQuestStatus[1].uState == QUEST_NEW &&
                      player.m_skillStatusMap.find(100)->second.uState == SKILL_NEW &&
                      player.m_spells[200].state == PLAYERSPELL_NEW &&
                      session.m_tutorialState == TUTORIALDATA_NEW,
                  "all failure/unknown preserves every owner");
        }
    }
}


uint32 reviewUInt32(SqlStmtFieldData const& value) { return *static_cast<uint32 const*>(value.buff()); }
struct ReviewFixture
{
    DatabaseState state;
    FakeConnection connection;
    ExecutorDatabase database;
    WorldSession session;
    Player player;
    ReviewFixture() : database(connection, state)
    { setupOwner(player, session); session._player = &player; }
    void guard(CharacterPersistenceContext& context)
    { check(context.AppendIdentityGuards({ownerIdentity(player)}), "review guard"); }
    void complete(CharacterPersistenceContext& context)
    {
        connection.cursor=0; connection.events.clear(); connection.observed.clear();
        connection.statements.assign(context.Batch().Writes().size(), result(SqlWriteOutcome::Succeeded,1));
        check(context.Execute().outcome==SqlWriteOutcome::Succeeded,"review complete");
        ++player.generation;
    }
};
void reviewerTransitions()
{
    for (int mode=0;mode<3;++mode)
    {
        ReviewFixture f; QuestStatusData q; q.uState=mode==2?QUEST_DELETED:QUEST_NEW;
        f.player.mQuestStatus[1]=q;
        CharacterPersistenceContext first(f.database,1); f.guard(first);
        check(f.player.AppendQuestStatus(first,100),"review quest first");
        if(mode==0) ++f.player.mQuestStatus[1].m_itemcount[0];
        if(mode==1) f.player.mQuestStatus.erase(1);
        if(mode==2) {f.player.mQuestStatus[1]=QuestStatusData(); f.player.mQuestStatus[1].m_reward_choice=2;}
        f.complete(first);
        QuestUpdateState expected=mode==0?QUEST_CHANGED:(mode==1?QUEST_DELETED:QUEST_NEW);
        check(f.player.mQuestStatus.at(1).uState==expected,"review quest reconciliation");
        CharacterPersistenceContext next(f.database,1); f.guard(next);
        check(f.player.AppendQuestStatus(next,101),"review quest next");
        std::string op=mode==0?"UPDATE":(mode==1?"DELETE":"INSERT");
        check(next.Batch().Writes().at(1).sql.find(op)==0,"review quest next SQL");
        f.complete(next);
        check(mode==1?f.player.mQuestStatus.empty():f.player.mQuestStatus.at(1).uState==QUEST_UNCHANGED,"review quest next ack");
    }
    for (int mode=0;mode<3;++mode)
    {
        ReviewFixture f; f.player.values[1]=(10u<<16)|5u;
        f.player.m_skillStatusMap.emplace(100,SkillStatusData(0,mode==2?SKILL_DELETED:SKILL_NEW));
        CharacterPersistenceContext first(f.database,1); f.guard(first);
        check(f.player.AppendSkills(first),"review skill first");
        if(mode==0) f.player.values[1]=(20u<<16)|6u;
        if(mode==1) f.player.m_skillStatusMap.erase(100);
        if(mode==2) { f.player.m_skillStatusMap.erase(100); f.player.m_skillStatusMap.emplace(100,SkillStatusData(0,SKILL_NEW)); }
        f.complete(first);
        SkillUpdateState expected=mode==0?SKILL_CHANGED:(mode==1?SKILL_DELETED:SKILL_NEW);
        check(f.player.m_skillStatusMap.at(100).uState==expected,"review skill reconciliation");
        if(mode==1) f.player.values.clear();
        CharacterPersistenceContext next(f.database,1); f.guard(next);
        check(f.player.AppendSkills(next),"review skill next");
        std::string op=mode==0?"UPDATE":(mode==1?"DELETE":"INSERT");
        check(next.Batch().Writes().at(1).sql.find(op)==0,"review skill next SQL");
        f.complete(next);
        check(mode==1?f.player.m_skillStatusMap.empty():f.player.m_skillStatusMap.at(100).uState==SKILL_UNCHANGED,"review skill next ack");
    }
    {
        ReviewFixture f; PlayerSpell spell; spell.state=PLAYERSPELL_NEW; spell.active=true;
        f.player.m_spells[1]=spell;
        CharacterPersistenceContext first(f.database,1); f.guard(first);
        check(f.player.AppendSpells(first),"review spell first");
        f.player.m_spells[1].dependent=true; f.complete(first);
        check(f.player.m_spells[1].state==PLAYERSPELL_CHANGED,"review spell needs delete");
        CharacterPersistenceContext second(f.database,1); f.guard(second);
        check(f.player.AppendSpells(second),"review spell delete");
        check(second.Batch().Writes().size()==2 && second.Batch().Writes()[1].sql.find("DELETE")==0,"review dependent deletes only");
        f.complete(second);
        check(f.player.m_spells[1].state==PLAYERSPELL_UNCHANGED,"review dependent acknowledged absent");
        f.player.m_spells[1].dependent=false; f.player.m_spells[1].state=PLAYERSPELL_CHANGED;
        CharacterPersistenceContext third(f.database,1); f.guard(third);
        check(f.player.AppendSpells(third),"review independent again");
        check(third.Batch().Writes().back().sql.find("INSERT")==0,"review independent reinsert");
        f.complete(third);
    }
    {
        ReviewFixture f; PlayerSpell spell; spell.state=PLAYERSPELL_NEW; spell.dependent=true;
        f.player.m_spells[1]=spell; CharacterPersistenceContext first(f.database,1); f.guard(first);
        check(f.player.AppendSpells(first),"review dependent initial");
        f.player.m_spells[1].dependent=false; f.complete(first);
        check(f.player.m_spells[1].state==PLAYERSPELL_NEW,"review changed dependency requires insert");
        CharacterPersistenceContext second(f.database,1); f.guard(second);
        check(f.player.AppendSpells(second) && second.Batch().Writes().back().sql.find("INSERT")==0,"review changed dependency next insert");
    }
    {
        ReviewFixture f; f.session.m_tutorialState=TUTORIALDATA_NEW;
        for(uint32 i=0;i<8;++i) f.session.m_tutorials[i]=i+10;
        CharacterPersistenceContext first(f.database,1); f.guard(first);
        check(f.session.AppendTutorialsDataToContext(first),"review tutorial first");
        f.session.m_tutorials[0]=99; f.complete(first);
        check(f.session.m_tutorialState==TUTORIALDATA_CHANGED,"review tutorial dirty after insert");
        CharacterPersistenceContext second(f.database,1); f.guard(second);
        check(f.session.AppendTutorialsDataToContext(second),"review tutorial next");
        auto const& write=second.Batch().Writes().at(1);
        check(write.sql.find("UPDATE")==0 && reviewUInt32(write.parameters[0])==99 && reviewUInt32(write.parameters[8])==7,"review tutorial UPDATE bindings");
        f.complete(second); check(f.session.m_tutorialState==TUTORIALDATA_UNCHANGED,"review tutorial update ack");
        f.session.m_tutorialState=TUTORIALDATA_CHANGED; CharacterPersistenceContext third(f.database,1); f.guard(third);
        check(f.session.AppendTutorialsDataToContext(third),"review tutorial replacement setup");
        Player replacement; setupOwner(replacement,f.session); replacement.guid=2; f.session._player=&replacement;
        f.complete(third); check(f.session.m_tutorialState==TUTORIALDATA_CHANGED,"review tutorial replacement not acknowledged");
    }
}
void reviewParameterValues(std::vector<SqlStmtFieldData> const& actual,
                           std::vector<SqlStmtFieldData> const& expected)
{
    check(actual.size()==expected.size(),"review parameter count");
    if(actual.size()!=expected.size())return;
    for(std::size_t i=0;i<actual.size();++i)
        check(actual[i].type()==expected[i].type() && actual[i].size()==expected[i].size() &&
              std::memcmp(actual[i].buff(),expected[i].buff(),actual[i].size())==0,"review exact parameter "+std::to_string(i));
}
void reviewerBindings()
{
    {
        ReviewFixture f; SpellAuraHolder holder; holder.admitted=true;
        auto& a=holder.saved; a.casterGuid=ObjectGuid(0x1122334455ULL); a.itemLowGuid=12; a.spellId=13;
        a.stacks=14; a.charges=15; a.damage[0]=1.25f; a.damage[1]=-2.5f; a.damage[2]=3.75f;
        a.periodicTime[0]=16; a.periodicTime[1]=17; a.periodicTime[2]=4000000000u;
        a.maxDuration=9000; a.duration=-75; a.effIndexMask=7; f.player.auraHolders[13]=&holder;
        CharacterPersistenceContext context(f.database,1); f.guard(context);
        check(f.player.AppendAuras(context),"review aura assembly");
        std::vector<SqlStmtFieldData> expected={SqlStmtFieldData(uint32(1)),SqlStmtFieldData(uint64(0x1122334455ULL)),
        SqlStmtFieldData(uint32(12)),SqlStmtFieldData(uint32(13)),SqlStmtFieldData(uint32(14)),SqlStmtFieldData(uint8(15)),
        SqlStmtFieldData(1.25f),SqlStmtFieldData(-2.5f),SqlStmtFieldData(3.75f),SqlStmtFieldData(uint32(16)),
        SqlStmtFieldData(uint32(17)),SqlStmtFieldData(uint32(4000000000u)),SqlStmtFieldData(int32(9000)),SqlStmtFieldData(int32(-75)),SqlStmtFieldData(int8(7))};
        reviewParameterValues(context.Batch().Writes().back().parameters,expected);
    }
    {
        ReviewFixture f; f.player.maxHealth=500;
        for(int i=0;i<5;++i){f.player.maxPower[i]=11+i;f.player.stats[i]=21.25f+i;}
        for(int i=0;i<7;++i)f.player.resistances[i]=-31-i;
        for(int i=0;i<5;++i)f.player.floats[100+i]=41.25f+i;
        f.player.spellCrit=46.25f; f.player.values[105]=51;f.player.values[106]=52;f.player.values[108]=54;f.player.healing=55;
        CharacterPersistenceContext context(f.database,1); f.guard(context); check(f.player.AppendStats(context,60),"review stats assembly");
        std::vector<SqlStmtFieldData> expected={SqlStmtFieldData(uint32(1)),SqlStmtFieldData(uint32(500))};
        for(uint32 v=11;v<=15;++v)expected.emplace_back(v);
        for(int i=0;i<5;++i)expected.emplace_back(21.25f+i);
        for(int32 v=-31;v>=-37;--v)expected.emplace_back(v);
        for(int i=0;i<5;++i)expected.emplace_back(41.25f+i);
        expected.emplace_back(46.25f);for(uint32 v:{51u,52u,54u,55u})expected.emplace_back(v);
        reviewParameterValues(context.Batch().Writes().back().parameters,expected);
    }
}

void identityBuilderTests()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1)};
    ExecutorDatabase database(connection, state);
    WorldSession session;
    Player player;
    setupOwner(player, session);
    SoftCharacterDeletion deletion;
    deletion.identity = ownerIdentity(player);
    deletion.deletionTime = 1700000000;
    CharacterPersistenceContext context(database, 110);
    check(AppendSoftCharacterDeletion(database, context, deletion),
          "soft deletion assembly");
    std::vector<SqlBoundWrite> writes = context.Batch().Writes();
    check(writes.size() == 3 &&
              writes[1].affectedRowsPolicy == SqlAffectedRowsPolicy::ExactlyOne &&
              writes[2].affectedRowsPolicy == SqlAffectedRowsPolicy::ExactlyOne &&
              writes[2].parameters.size() == 5 &&
              parameterUInt64(writes[2].parameters[0]) == 1700000000ULL &&
              parameterUInt64(writes[2].parameters[4]) == 4,
          "soft deletion order/bindings");
    check(writes[0].parameters.size() == 6 &&
              writes[0].parameters[0].type() == FIELD_UI32 &&
              writes[0].parameters[1].type() == FIELD_UI32 &&
              writes[0].parameters[2].type() == FIELD_STRING &&
              writes[0].parameters[3].type() == FIELD_UI64 &&
              writes[0].parameters[4].type() == FIELD_UI32 &&
              writes[0].parameters[5].type() == FIELD_STRING,
          "soft guard parameter types");
    check(writes[1].parameters.size() == 2 &&
              writes[1].parameters[0].type() == FIELD_UI32 &&
              writes[1].parameters[1].type() == FIELD_STRING,
          "soft release parameter types");
    check(writes[2].parameters[0].type() == FIELD_UI64 &&
              writes[2].parameters[1].type() == FIELD_UI32 &&
              writes[2].parameters[2].type() == FIELD_UI32 &&
              writes[2].parameters[3].type() == FIELD_STRING &&
              writes[2].parameters[4].type() == FIELD_UI64,
          "soft tombstone parameter types");
    check(context.Execute().outcome == SqlWriteOutcome::Succeeded,
          "soft deletion commit");

    SoftCharacterDeletion malformed = deletion;
    malformed.deletionTime = 0;
    CharacterPersistenceContext bad(database, 111);
    check(!AppendSoftCharacterDeletion(database, bad, malformed) &&
              bad.IsInvalid(),
          "soft deletion zero time rejected");

    CharacterRestoration restore;
    restore.guid = 1;
    restore.expectedGeneration = 4;
    restore.deletedName = "Alice Smith";
    restore.deletedAccountId = 7;
    restore.deletedTime = 1700000000;
    restore.targetCanonicalName = "Bob Jones";
    restore.targetAccountId = 7;
    connection.cursor = 0;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::Succeeded, 1)};
    CharacterPersistenceContext restored(database, 112);
    check(AppendCharacterRestoration(database, restored, restore),
          "restoration assembly");
    check(restored.Batch().Writes().size() == 2 &&
              restored.Batch().Writes()[0].affectedRowsPolicy ==
                  SqlAffectedRowsPolicy::ExactlyOne,
          "restoration policies");
    std::vector<SqlBoundWrite> const restoreWrites = restored.Batch().Writes();
    check(restoreWrites[0].parameters.size() == 8 &&
              restoreWrites[0].parameters[0].type() == FIELD_STRING &&
              restoreWrites[0].parameters[1].type() == FIELD_UI32 &&
              restoreWrites[0].parameters[2].type() == FIELD_UI32 &&
              restoreWrites[0].parameters[3].type() == FIELD_UI64 &&
              restoreWrites[0].parameters[4].type() == FIELD_UI64 &&
              restoreWrites[0].parameters[5].type() == FIELD_UI32 &&
              restoreWrites[0].parameters[6].type() == FIELD_STRING &&
              restoreWrites[0].parameters[7].type() == FIELD_UI32 &&
              parameterString(restoreWrites[0].parameters[0]) == "Bob Jones" &&
              parameterUInt64(restoreWrites[0].parameters[3]) == 4 &&
              parameterUInt64(restoreWrites[0].parameters[4]) == 1700000000 &&
              restoreWrites[1].parameters.size() == 2 &&
              restoreWrites[1].parameters[0].type() == FIELD_STRING &&
              restoreWrites[1].parameters[1].type() == FIELD_UI32 &&
              restoreWrites[1].affectedRowsPolicy ==
                  SqlAffectedRowsPolicy::ExactlyOne,
          "restoration complete bindings");
    check(restored.Execute().outcome == SqlWriteOutcome::Succeeded,
          "restoration commit");

    CharacterPersistenceContext duplicate(database, 113);
    connection.cursor = 0;
    connection.statements = {
        result(SqlWriteOutcome::Succeeded, 1),
        result(SqlWriteOutcome::DuplicateKey, 0, 1062)};
    check(AppendCharacterRestoration(database, duplicate, restore),
          "restoration conflict assembly");
    SqlBatchResult duplicateResult = duplicate.Execute();
    check(ClassifyCharacterNameClaim(duplicateResult, 1) ==
              CharacterNameWriteStatus::NameInUse,
          "restoration conflict classified name in use");
}

void deletionBuilderFailureMatrix()
{
    SoftCharacterDeletion base;
    base.identity = identity(1, "Alice Smith", 3);
    base.deletionTime = 1700000000;

    struct BadCase
    {
        char const* label;
        SoftCharacterDeletion request;
    };
    std::vector<BadCase> bad;
    bad.push_back({"zero time", [&] { auto v = base; v.deletionTime = 0; return v; }()});
    bad.push_back({"zero guid", [&] { auto v = base; v.identity.guid = 0; return v; }()});
    bad.push_back({"zero account", [&] { auto v = base; v.identity.accountId = 0; return v; }()});
    bad.push_back({"zero generation", [&] { auto v = base; v.identity.generation = 0; return v; }()});
    bad.push_back({"maximum generation", [&] { auto v = base; v.identity.generation = std::numeric_limits<uint64>::max(); return v; }()});
    bad.push_back({"noncanonical", [&] { auto v = base; v.identity.canonicalName = "alice smith"; return v; }()});
    for (BadCase const& item : bad)
    {
        DatabaseState state;
        FakeConnection connection;
        ExecutorDatabase database(connection, state);
        CharacterPersistenceContext context(database, 120);
        check(!AppendSoftCharacterDeletion(database, context, item.request),
              std::string("soft reject ") + item.label);
        check(context.IsInvalid() &&
                  context.Execute().outcome == SqlWriteOutcome::NotExecuted &&
                  state.submissions == 0,
              std::string("soft reject no submit ") + item.label);
    }

    DatabaseState state;
    FakeConnection connection;
    ExecutorDatabase database(connection, state);
    CharacterPersistenceContext prefilled(database, 121);
    append(prefilled, "payload");
    check(!AppendSoftCharacterDeletion(database, prefilled, base),
          "soft nonempty rejected");
    CharacterPersistenceContext invalid(database, 122);
    invalid.Invalidate();
    check(!AppendSoftCharacterDeletion(database, invalid, base),
          "soft invalid rejected");
    CharacterPersistenceContext submitted(database, 123);
    check(AppendSoftCharacterDeletion(database, submitted, base),
          "soft submit fixture");
    connection.cursor = 0;
    connection.statements.assign(3, result(SqlWriteOutcome::Succeeded, 1));
    check(submitted.Execute().outcome == SqlWriteOutcome::Succeeded,
          "soft submit success");
    check(!AppendSoftCharacterDeletion(database, submitted, base),
          "soft submitted rejected");

    DatabaseState otherState;
    FakeConnection otherConnection;
    ExecutorDatabase other(otherConnection, otherState);
    CharacterPersistenceContext wrong(database, 124);
    check(!AppendSoftCharacterDeletion(other, wrong, base),
          "soft wrong database rejected");
}

void deletionBuilderExecution()
{
    uint64 const requestedTime = 1700000000;

    {
        DatabaseState state;
        FakeConnection connection;
        ExecutorDatabase database(connection, state);
        CharacterPersistenceContext context(database, 130);
        SoftCharacterDeletion request;
        request.identity = identity(1, "Alice Smith", 3);
        request.deletionTime = requestedTime;
        check(AppendSoftCharacterDeletion(database, context, request),
              "soft ack assembly");
        int acknowledgments = 0;
        context.OnCommit([&]() {
            check(connection.commitCalls == 1, "soft ack after commit");
            ++acknowledgments;
        });
        connection.statements = {result(SqlWriteOutcome::Succeeded, 1),
                                 result(SqlWriteOutcome::Succeeded, 1),
                                 result(SqlWriteOutcome::Succeeded, 1)};
        check(context.Execute().outcome == SqlWriteOutcome::Succeeded &&
                  acknowledgments == 1,
              "soft commit acknowledges once");
        check(context.Execute().outcome == SqlWriteOutcome::NotExecuted &&
                  acknowledgments == 1,
              "soft second execute no replay");
    }

    struct FailureCase
    {
        char const* label;
        std::vector<SqlWriteResult> statements;
        SqlWriteResult rollback;
        SqlWriteResult commit;
        int expectedObserved;
        SqlWriteOutcome expectedOutcome;
        int expectedRollbacks;
        int expectedCommits;
    };
    std::vector<FailureCase> cases;
    cases.push_back({"guard zero", {result(SqlWriteOutcome::Succeeded, 0)},
                     result(SqlWriteOutcome::Succeeded, 1),
                     result(SqlWriteOutcome::Succeeded, 1), 1,
                     SqlWriteOutcome::Failed, 1, 0});
    cases.push_back({"guard sql failure", {result(SqlWriteOutcome::Failed, 0, 1146)},
                     result(SqlWriteOutcome::Succeeded, 1),
                     result(SqlWriteOutcome::Succeeded, 1), 1,
                     SqlWriteOutcome::Failed, 1, 0});
    cases.push_back({"release zero", {result(SqlWriteOutcome::Succeeded, 1),
                                      result(SqlWriteOutcome::Succeeded, 0)},
                     result(SqlWriteOutcome::Succeeded, 1),
                     result(SqlWriteOutcome::Succeeded, 1), 2,
                     SqlWriteOutcome::Failed, 1, 0});
    cases.push_back({"release sql failure", {result(SqlWriteOutcome::Succeeded, 1),
                                             result(SqlWriteOutcome::Failed, 0, 1146)},
                     result(SqlWriteOutcome::Succeeded, 1),
                     result(SqlWriteOutcome::Succeeded, 1), 2,
                     SqlWriteOutcome::Failed, 1, 0});
    cases.push_back({"tombstone zero", {result(SqlWriteOutcome::Succeeded, 1),
                                        result(SqlWriteOutcome::Succeeded, 1),
                                        result(SqlWriteOutcome::Succeeded, 0)},
                     result(SqlWriteOutcome::Succeeded, 1),
                     result(SqlWriteOutcome::Succeeded, 1), 3,
                     SqlWriteOutcome::Failed, 1, 0});
    cases.push_back({"tombstone failure", {result(SqlWriteOutcome::Succeeded, 1),
                                           result(SqlWriteOutcome::Succeeded, 1),
                                           result(SqlWriteOutcome::Failed, 0, 1146)},
                     result(SqlWriteOutcome::Succeeded, 1),
                     result(SqlWriteOutcome::Succeeded, 1), 3,
                     SqlWriteOutcome::Failed, 1, 0});
    cases.push_back({"rollback failure", {result(SqlWriteOutcome::Succeeded, 1),
                                          result(SqlWriteOutcome::Succeeded, 1),
                                          result(SqlWriteOutcome::Failed, 0, 1146)},
                     result(SqlWriteOutcome::Failed, 0, 2013),
                     result(SqlWriteOutcome::Succeeded, 1), 3,
                     SqlWriteOutcome::OutcomeUnknown, 1, 0});
    cases.push_back({"unknown commit", {result(SqlWriteOutcome::Succeeded, 1),
                                        result(SqlWriteOutcome::Succeeded, 1),
                                        result(SqlWriteOutcome::Succeeded, 1)},
                     result(SqlWriteOutcome::Succeeded, 1),
                     result(SqlWriteOutcome::OutcomeUnknown, 0, 2013), 3,
                     SqlWriteOutcome::OutcomeUnknown, 1, 1});

    for (FailureCase const& item : cases)
    {
        DatabaseState state;
        FakeConnection connection;
        ExecutorDatabase database(connection, state);
        CharacterPersistenceContext context(database, 131);
        check(AppendSoftCharacterDeletion(
                  database, context,
                  SoftCharacterDeletion{identity(1, "Alice Smith", 3), requestedTime}),
              std::string("soft failure assembly ") + item.label);
        int acknowledgments = 0;
        context.OnCommit([&]() { ++acknowledgments; });
        connection.statements = item.statements;
        connection.rollback = item.rollback;
        connection.commit = item.commit;
        SqlBatchResult const result = context.Execute();
        check(result.outcome == item.expectedOutcome &&
                  state.submissions == 1 &&
                  connection.observed.size() == std::size_t(item.expectedObserved) &&
                  connection.rollbackCalls == item.expectedRollbacks &&
                  connection.commitCalls == item.expectedCommits &&
                  acknowledgments == 0,
              std::string("soft failure matrix ") + item.label);
    }

    DatabaseState state;
    FakeConnection connection;
    ExecutorDatabase database(connection, state);
    CharacterPersistenceContext context(database, 132);
    SoftCharacterDeletion owned;
    owned.identity = identity(1, "Alice Smith", 3);
    owned.deletionTime = requestedTime;
    check(AppendSoftCharacterDeletion(database, context, owned),
          "soft owned assembly");
    owned.identity.canonicalName = "Mutated Name";
    owned.identity.generation = 0;
    owned.deletionTime = 1;
    connection.statements = {result(SqlWriteOutcome::Succeeded, 1),
                             result(SqlWriteOutcome::Succeeded, 1),
                             result(SqlWriteOutcome::Succeeded, 1)};
    check(context.Execute().outcome == SqlWriteOutcome::Succeeded &&
              parameterString(connection.observed[0].parameters[2]) == "Alice Smith" &&
              parameterUInt64(connection.observed[2].parameters[0]) == requestedTime,
          "soft owned request values retained");
}

void restorationBuilderFailureMatrix()
{
    CharacterRestoration base;
    base.guid = 1;
    base.expectedGeneration = 4;
    base.deletedName = "Alice Smith";
    base.deletedAccountId = 7;
    base.deletedTime = 1700000000;
    base.targetCanonicalName = "Bob Jones";
    base.targetAccountId = 7;

    struct BadCase { char const* label; CharacterRestoration request; };
    std::vector<BadCase> bad;
    bad.push_back({"zero guid", [&] { auto v = base; v.guid = 0; return v; }()});
    bad.push_back({"zero generation", [&] { auto v = base; v.expectedGeneration = 0; return v; }()});
    bad.push_back({"max generation", [&] { auto v = base; v.expectedGeneration = std::numeric_limits<uint64>::max(); return v; }()});
    bad.push_back({"zero deleted account", [&] { auto v = base; v.deletedAccountId = 0; return v; }()});
    bad.push_back({"zero target account", [&] { auto v = base; v.targetAccountId = 0; return v; }()});
    bad.push_back({"zero deleted time", [&] { auto v = base; v.deletedTime = 0; return v; }()});
    bad.push_back({"bad deleted name", [&] { auto v = base; v.deletedName = "alice smith"; return v; }()});
    bad.push_back({"bad target name", [&] { auto v = base; v.targetCanonicalName = "Bob"; return v; }()});
    for (BadCase const& item : bad)
    {
        DatabaseState state;
        FakeConnection connection;
        ExecutorDatabase database(connection, state);
        CharacterPersistenceContext context(database, 140);
        check(!AppendCharacterRestoration(database, context, item.request),
              std::string("restore reject ") + item.label);
        check(context.IsInvalid() &&
                  context.Execute().outcome == SqlWriteOutcome::NotExecuted &&
                  state.submissions == 0,
              std::string("restore reject no submit ") + item.label);
    }

    DatabaseState state;
    FakeConnection connection;
    ExecutorDatabase database(connection, state);
    CharacterPersistenceContext prefilled(database, 141);
    append(prefilled, "payload");
    check(!AppendCharacterRestoration(database, prefilled, base),
          "restore nonempty rejected");
    CharacterPersistenceContext invalid(database, 142);
    invalid.Invalidate();
    check(!AppendCharacterRestoration(database, invalid, base),
          "restore invalid rejected");
    CharacterPersistenceContext submitted(database, 143);
    check(AppendCharacterRestoration(database, submitted, base),
          "restore submit fixture");
    connection.cursor = 0;
    connection.statements = {result(SqlWriteOutcome::Succeeded, 1),
                             result(SqlWriteOutcome::Succeeded, 1)};
    check(submitted.Execute().outcome == SqlWriteOutcome::Succeeded,
          "restore submit success");
    check(!AppendCharacterRestoration(database, submitted, base),
          "restore submitted rejected");

    DatabaseState otherState;
    FakeConnection otherConnection;
    ExecutorDatabase other(otherConnection, otherState);
    CharacterPersistenceContext wrong(database, 144);
    check(!AppendCharacterRestoration(other, wrong, base),
          "restore wrong database rejected");

    {
        DatabaseState okState;
        FakeConnection okConnection;
        ExecutorDatabase okDatabase(okConnection, okState);
        CharacterPersistenceContext context(okDatabase, 145);
        check(AppendCharacterRestoration(okDatabase, context, base),
              "restore ack assembly");
        int acknowledgments = 0;
        context.OnCommit([&]() {
            check(okConnection.commitCalls == 1, "restore ack after commit");
            ++acknowledgments;
        });
        okConnection.statements = {result(SqlWriteOutcome::Succeeded, 1),
                                   result(SqlWriteOutcome::Succeeded, 1)};
        check(context.Execute().outcome == SqlWriteOutcome::Succeeded &&
                  acknowledgments == 1,
              "restore commit acknowledges once");
        check(context.Execute().outcome == SqlWriteOutcome::NotExecuted &&
                  acknowledgments == 1,
              "restore second execute no replay");
    }

    struct FailureCase
    {
        char const* label;
        std::vector<SqlWriteResult> statements;
        SqlWriteResult rollback;
        SqlWriteResult commit;
        SqlWriteOutcome expectedOutcome;
        int observed, rollbacks, commits;
    };
    std::vector<FailureCase> cases;
    cases.push_back({"tombstone zero", {result(SqlWriteOutcome::Succeeded, 0)},
                     result(SqlWriteOutcome::Succeeded, 1),
                     result(SqlWriteOutcome::Succeeded, 1),
                     SqlWriteOutcome::Failed, 1, 1, 0});
    cases.push_back({"tombstone sql failure", {result(SqlWriteOutcome::Failed, 0, 1146)},
                     result(SqlWriteOutcome::Succeeded, 1),
                     result(SqlWriteOutcome::Succeeded, 1),
                     SqlWriteOutcome::Failed, 1, 1, 0});
    cases.push_back({"claim zero", {result(SqlWriteOutcome::Succeeded, 1),
                                    result(SqlWriteOutcome::Succeeded, 0)},
                     result(SqlWriteOutcome::Succeeded, 1),
                     result(SqlWriteOutcome::Succeeded, 1),
                     SqlWriteOutcome::Failed, 2, 1, 0});
    cases.push_back({"claim duplicate", {result(SqlWriteOutcome::Succeeded, 1),
                                         result(SqlWriteOutcome::DuplicateKey, 0, 1062)},
                     result(SqlWriteOutcome::Succeeded, 1),
                     result(SqlWriteOutcome::Succeeded, 1),
                     SqlWriteOutcome::DuplicateKey, 2, 1, 0});
    cases.push_back({"claim failure", {result(SqlWriteOutcome::Succeeded, 1),
                                       result(SqlWriteOutcome::Failed, 0, 1146)},
                     result(SqlWriteOutcome::Succeeded, 1),
                     result(SqlWriteOutcome::Succeeded, 1),
                     SqlWriteOutcome::Failed, 2, 1, 0});
    cases.push_back({"rollback failure", {result(SqlWriteOutcome::Succeeded, 1),
                                          result(SqlWriteOutcome::DuplicateKey, 0, 1062)},
                     result(SqlWriteOutcome::Failed, 0, 2013),
                     result(SqlWriteOutcome::Succeeded, 1),
                     SqlWriteOutcome::OutcomeUnknown, 2, 1, 0});
    cases.push_back({"unknown commit", {result(SqlWriteOutcome::Succeeded, 1),
                                        result(SqlWriteOutcome::Succeeded, 1)},
                     result(SqlWriteOutcome::Succeeded, 1),
                     result(SqlWriteOutcome::OutcomeUnknown, 0, 2013),
                     SqlWriteOutcome::OutcomeUnknown, 2, 1, 1});
    for (FailureCase const& item : cases)
    {
        DatabaseState failState;
        FakeConnection failConnection;
        ExecutorDatabase failDatabase(failConnection, failState);
        CharacterPersistenceContext context(failDatabase, 146);
        check(AppendCharacterRestoration(failDatabase, context, base),
              std::string("restore failure assembly ") + item.label);
        int acknowledgments = 0;
        context.OnCommit([&]() { ++acknowledgments; });
        failConnection.statements = item.statements;
        failConnection.rollback = item.rollback;
        failConnection.commit = item.commit;
        SqlBatchResult const result = context.Execute();
        check(result.outcome == item.expectedOutcome &&
                  failState.submissions == 1 &&
                  failConnection.observed.size() == std::size_t(item.observed) &&
                  failConnection.rollbackCalls == item.rollbacks &&
                  failConnection.commitCalls == item.commits &&
                  acknowledgments == 0,
              std::string("restore failure matrix ") + item.label);
    }

    DatabaseState ownedState;
    FakeConnection ownedConnection;
    ExecutorDatabase ownedDatabase(ownedConnection, ownedState);
    CharacterPersistenceContext ownedContext(ownedDatabase, 147);
    CharacterRestoration owned = base;
    check(AppendCharacterRestoration(ownedDatabase, ownedContext, owned),
          "restore owned assembly");
    owned.targetCanonicalName = "Mutated Name";
    owned.expectedGeneration = 0;
    owned.deletedTime = 1;
    ownedConnection.statements = {result(SqlWriteOutcome::Succeeded, 1),
                                  result(SqlWriteOutcome::Succeeded, 1)};
    check(ownedContext.Execute().outcome == SqlWriteOutcome::Succeeded &&
              parameterString(ownedConnection.observed[0].parameters[0]) == "Bob Jones" &&
              parameterUInt64(ownedConnection.observed[0].parameters[3]) == 4,
          "restore owned request values retained");
}

void classifierTests()
{
    SqlBatchResult result = EmptySqlBatchResult();
    result.outcome = SqlWriteOutcome::Succeeded;
    result.stage = SqlBatchStage::Complete;
    check(ClassifyCharacterNameClaim(result, 1) ==
              CharacterNameWriteStatus::Committed,
          "classifier committed");
    result.outcome = SqlWriteOutcome::NotExecuted;
    check(ClassifyCharacterNameClaim(result, 1) ==
              CharacterNameWriteStatus::Rejected,
          "classifier not executed");
    result = EmptySqlBatchResult();
    result.outcome = SqlWriteOutcome::DuplicateKey;
    result.stage = SqlBatchStage::Statement;
    result.statementIndex = 1;
    result.cause.outcome = SqlWriteOutcome::DuplicateKey;
    result.rollbackAttempted = true;
    result.rollbackResult.outcome = SqlWriteOutcome::Succeeded;
    check(ClassifyCharacterNameClaim(result, 1) ==
              CharacterNameWriteStatus::NameInUse,
          "classifier claim duplicate");
    check(ClassifyCharacterNameClaim(result, 0) ==
              CharacterNameWriteStatus::Rejected,
          "classifier wrong index");
    check(ClassifyCharacterNameClaim(
              result, std::numeric_limits<std::size_t>::max()) ==
              CharacterNameWriteStatus::Rejected,
          "classifier sentinel index");
    result.rollbackResult.outcome = SqlWriteOutcome::Failed;
    check(ClassifyCharacterNameClaim(result, 1) ==
              CharacterNameWriteStatus::OutcomeUnknown,
          "classifier failed rollback unknown");
    result = EmptySqlBatchResult();
    result.outcome = SqlWriteOutcome::OutcomeUnknown;
    result.stage = SqlBatchStage::Commit;
    check(ClassifyCharacterNameClaim(result, 1) ==
              CharacterNameWriteStatus::OutcomeUnknown,
          "classifier unknown precedence");

    SqlBatchResult incomplete = EmptySqlBatchResult();
    incomplete.outcome = SqlWriteOutcome::Succeeded;
    incomplete.stage = SqlBatchStage::Statement;
    check(ClassifyCharacterNameClaim(incomplete, 1) ==
              CharacterNameWriteStatus::Rejected,
          "classifier incomplete success rejected");

    SqlBatchResult wrongStage = EmptySqlBatchResult();
    wrongStage.outcome = SqlWriteOutcome::DuplicateKey;
    wrongStage.stage = SqlBatchStage::Commit;
    wrongStage.statementIndex = 1;
    wrongStage.cause.outcome = SqlWriteOutcome::DuplicateKey;
    wrongStage.rollbackAttempted = true;
    wrongStage.rollbackResult.outcome = SqlWriteOutcome::Succeeded;
    check(ClassifyCharacterNameClaim(wrongStage, 1) ==
              CharacterNameWriteStatus::Rejected,
          "classifier duplicate wrong stage rejected");

    SqlBatchResult wrongCause = wrongStage;
    wrongCause.stage = SqlBatchStage::Statement;
    wrongCause.cause.outcome = SqlWriteOutcome::Failed;
    check(ClassifyCharacterNameClaim(wrongCause, 1) ==
              CharacterNameWriteStatus::Rejected,
          "classifier duplicate wrong cause rejected");

    SqlBatchResult unconfirmed = wrongCause;
    unconfirmed.cause.outcome = SqlWriteOutcome::DuplicateKey;
    unconfirmed.rollbackAttempted = false;
    check(ClassifyCharacterNameClaim(unconfirmed, 1) ==
              CharacterNameWriteStatus::Rejected,
          "classifier duplicate without rollback rejected");
}

void recoveryBlockingTests()
{
    CharacterRecoveryRecord record;
    record.kind = CharacterRecoveryKind::Rename;
    record.guids = {11, 12};
    record.accountIds = {7};
    record.expectedGeneration = 3;
    record.detail = "unknown";
    check(BlockCharacterRecovery(record), "recovery block accepted");
    check(IsCharacterRecoveryBlocked(11) && IsCharacterRecoveryBlocked(12) &&
              IsAccountRecoveryBlocked(7),
          "recovery blocks guids/account");
    check(!IsCharacterRecoveryBlocked(13), "unrelated guid unblocked");
    check(GetCharacterRecoveryRecords().size() == 1,
          "recovery logical record deduplicated");
    check(ClearCharacterRecovery(11), "recovery clear one guid");
    check(!IsCharacterRecoveryBlocked(11) && IsCharacterRecoveryBlocked(12),
          "recovery clear is guid scoped");
    ClearCharacterRecovery(12);
}

void accountLifecycleRegistryTests()
{
    {
        AccountLifecycleRegistry registry;
        check(!registry.TryAcquire({}, AccountLifecycleKind::Mutation),
              "empty admission set rejected");
        check(!registry.TryAcquire({0}, AccountLifecycleKind::Mutation),
              "zero account admission rejected");

        AccountLifecycleLeasePtr first =
            registry.TryAcquire({2, 1, 2}, AccountLifecycleKind::Mutation);
        check(first && first->Covers(1) && first->Covers(2) &&
                  first->IsActive() &&
                  registry.Allows(*first, 1, AccountLifecycleKind::Mutation),
              "normalized multi-account admission");
        check(!registry.TryAcquire({2, 3}, AccountLifecycleKind::Mutation),
              "atomic all-or-none admission");
        AccountLifecycleLeasePtr third =
            registry.TryAcquire({3}, AccountLifecycleKind::Mutation);
        check(third && third->IsActive(), "unrelated account proceeds");
        check(!registry.Allows(*first, 3, AccountLifecycleKind::Mutation),
              "lease does not cover other account");
        check(!registry.Allows(*first, 1, AccountLifecycleKind::Deletion),
              "wrong kind rejected");
        check(!registry.MarkDeleted(*first, 1), "mutation cannot retire");
        first.reset();
        check(!third->Covers(1), "release does not disturb other lease");

        AccountLifecycleRegistry other;
        check(!other.Allows(*third, 3, AccountLifecycleKind::Mutation),
              "foreign registry lease rejected");

        AccountLifecycleLeasePtr deletion =
            registry.TryAcquire({9}, AccountLifecycleKind::Deletion);
        check(deletion && registry.MarkDeleted(*deletion, 9),
              "deletion marks retired");
        deletion.reset();
        check(!registry.TryAcquire({9}, AccountLifecycleKind::Mutation),
              "retired account rejects admission");

        AccountLifecycleLeasePtr held =
            registry.TryAcquire({4}, AccountLifecycleKind::Mutation);
        check(held && registry.BlockRecovery(CharacterRecoveryRecord{
                  CharacterRecoveryKind::WholeSave, {}, {4}, 0, "blocked"}),
              "recovery block recorded");
        check(!registry.TryAcquire({4}, AccountLifecycleKind::Mutation),
              "recovery blocks new admission");
        held.reset();
    }

    AccountLifecycleRegistry recordsRegistry;
    CharacterRecoveryRecord record;
    record.kind = CharacterRecoveryKind::Rename;
    record.guids = {1};
    record.accountIds = {7};
    record.detail = "same";
    check(recordsRegistry.BlockRecovery(record), "recorded recovery");
    check(recordsRegistry.BlockRecovery(record), "duplicate recovery accepted");
    check(recordsRegistry.Records().size() == 1,
          "identical recovery deduplicated");
    check(recordsRegistry.ClearCharacterBlock(1),
          "guid evidence cleared");
    check(recordsRegistry.Records().size() == 1 &&
              recordsRegistry.IsAccountBlocked(7) &&
              !recordsRegistry.IsCharacterBlocked(1),
          "account-only evidence retained and visible");
    CharacterRecoveryRecord invalid;
    invalid.kind = CharacterRecoveryKind::WholeSave;
    invalid.guids = {2, 0};
    invalid.accountIds = {8};
    check(!recordsRegistry.BlockRecovery(invalid) &&
              !recordsRegistry.IsCharacterBlocked(2) &&
              !recordsRegistry.IsAccountBlocked(8),
          "invalid record changes no index");

    AccountLifecycleLeasePtr outliving;
    {
        AccountLifecycleRegistry temporary;
        outliving = temporary.TryAcquire({5}, AccountLifecycleKind::Mutation);
        check(outliving && outliving->IsActive(), "temporary admission active");
    }
    check(outliving && outliving->Covers(5) && !outliving->IsActive(),
          "lease outlives registry without use-after-free");
    outliving.reset();

    AccountLifecycleRegistry contested;
    std::atomic<int> winners{0};
    std::atomic<bool> go{false};
    std::mutex leasesMutex;
    std::vector<AccountLifecycleLeasePtr> leases;
    auto contend = [&]() {
        while (!go.load()) std::this_thread::yield();
        AccountLifecycleLeasePtr lease =
            contested.TryAcquire({6}, AccountLifecycleKind::Mutation);
        if (lease)
        {
            std::lock_guard<std::mutex> lock(leasesMutex);
            leases.push_back(std::move(lease));
            ++winners;
        }
    };
    std::thread one(contend);
    std::thread two(contend);
    go.store(true);
    one.join();
    two.join();
    check(leases.size() == 1 && leases.front()->IsActive() &&
              winners.load() == 1,
          "exactly one active contended admission");
}
CharacterDeletionSnapshot activeDeletion()
{
    CharacterDeletionSnapshot snapshot;
    snapshot.identity.guid = 42;
    snapshot.identity.accountId = 7;
    snapshot.identity.canonicalName = "Alice Smith";
    snapshot.identity.generation = 3;
    return snapshot;
}

CharacterDeletionSnapshot tombstoneDeletion()
{
    CharacterDeletionSnapshot snapshot = activeDeletion();
    snapshot.deletedTime = 1700000000;
    return snapshot;
}

void permanentDeletionBuilderTests()
{
    DatabaseState state;
    FakeConnection connection;
    connection.statements.assign(27, result(SqlWriteOutcome::Succeeded, 1));
    ExecutorDatabase database(connection, state);
    CharacterPersistenceContext context(database, 150);
    check(AppendPermanentCharacterDeletion(database, context, activeDeletion()),
          "active permanent deletion assembly");
    std::vector<SqlBoundWrite> writes = context.Batch().Writes();
    check(writes.size() == 27 &&
              writes.front().sql.find("UPDATE `characters` SET `identity_generation`") == 0 &&
              writes.front().affectedRowsPolicy == SqlAffectedRowsPolicy::ExactlyOne &&
              writes[1].sql.find("DELETE FROM `character_name_ownership`") == 0 &&
              writes.back().sql.find("DELETE FROM `characters`") == 0 &&
              writes.back().affectedRowsPolicy == SqlAffectedRowsPolicy::ExactlyOne,
          "active guard/order/policy");
    check(writes.front().parameters.size() == 8 &&
              writes[1].parameters.size() == 2,
          "active guard/ownership bindings");
    int acknowledgments = 0;
    context.OnCommit([&]() { ++acknowledgments; });
    check(context.Execute().outcome == SqlWriteOutcome::Succeeded &&
              acknowledgments == 1,
          "active permanent deletion acknowledges once");

    DatabaseState tombstoneState;
    FakeConnection tombstoneConnection;
    tombstoneConnection.statements.assign(26, result(SqlWriteOutcome::Succeeded, 1));
    ExecutorDatabase tombstoneDatabase(tombstoneConnection, tombstoneState);
    CharacterPersistenceContext tombstone(tombstoneDatabase, 151);
    check(AppendPermanentCharacterDeletion(
              tombstoneDatabase, tombstone, tombstoneDeletion()),
          "tombstone permanent deletion assembly");
    std::vector<SqlBoundWrite> tombstoneWrites = tombstone.Batch().Writes();
    check(tombstoneWrites.size() == 26 &&
              tombstoneWrites.front().sql.find("`deleted_time`=?") != std::string::npos &&
              tombstoneWrites.front().parameters.size() == 8 &&
              tombstoneWrites[1].sql.find("DELETE FROM `character_action`") == 0 &&
              tombstoneWrites.back().sql.find("`deleted_name`") != std::string::npos,
          "tombstone guard skips ownership release");

    for (auto mutate : std::vector<std::function<void(CharacterDeletionSnapshot&)>>{
             [](CharacterDeletionSnapshot& s) { s.identity.guid = 0; },
             [](CharacterDeletionSnapshot& s) { s.identity.accountId = 0; },
             [](CharacterDeletionSnapshot& s) { s.identity.generation = 0; },
             [](CharacterDeletionSnapshot& s) {
                 s.identity.generation = std::numeric_limits<uint64>::max();
             },
             [](CharacterDeletionSnapshot& s) { s.identity.canonicalName = "Alice"; },
             [](CharacterDeletionSnapshot& s) { s.identity.canonicalName = "alice smith"; }})
    {
        DatabaseState invalidState;
        FakeConnection invalidConnection;
        ExecutorDatabase invalidDatabase(invalidConnection, invalidState);
        CharacterPersistenceContext invalid(invalidDatabase, 152);
        CharacterDeletionSnapshot snapshot = activeDeletion();
        mutate(snapshot);
        check(!AppendPermanentCharacterDeletion(invalidDatabase, invalid, snapshot) &&
                  invalid.IsInvalid() && invalidConnection.statements.empty(),
              "invalid deletion snapshot rejected before write");
    }

    for (std::size_t failure : {std::size_t(0), std::size_t(1), std::size_t(5),
                                std::size_t(26)})
    {
        DatabaseState failedState;
        FakeConnection failedConnection;
        failedConnection.statements.assign(27, result(SqlWriteOutcome::Succeeded, 1));
        failedConnection.statements[failure] = result(SqlWriteOutcome::Failed, 0, 1146);
        ExecutorDatabase failedDatabase(failedConnection, failedState);
        CharacterPersistenceContext failed(failedDatabase, 153);
        check(AppendPermanentCharacterDeletion(failedDatabase, failed, activeDeletion()),
              "failure matrix assembly");
        int failedAcks = 0;
        failed.OnCommit([&]() { ++failedAcks; });
        SqlBatchResult const failedResult = failed.Execute();
        check(failedResult.outcome == SqlWriteOutcome::Failed &&
                  failedConnection.rollbackCalls == 1 &&
                  failedConnection.commitCalls == 0 && failedAcks == 0,
              "deletion failure rolls back without publication");
    }

    DatabaseState unknownState;
    FakeConnection unknownConnection;
    unknownConnection.statements.assign(27, result(SqlWriteOutcome::Succeeded, 1));
    unknownConnection.commit = result(SqlWriteOutcome::OutcomeUnknown, 0, 2013);
    ExecutorDatabase unknownDatabase(unknownConnection, unknownState);
    CharacterPersistenceContext unknown(unknownDatabase, 154);
    check(AppendPermanentCharacterDeletion(unknownDatabase, unknown, activeDeletion()),
          "permanent unknown assembly");
    int unknownAcks = 0;
    unknown.OnCommit([&]() { ++unknownAcks; });
    check(unknown.Execute().outcome == SqlWriteOutcome::OutcomeUnknown &&
              unknownConnection.commitCalls == 1 && unknownAcks == 0,
          "permanent unknown publishes nothing");
}

void persistenceAdmissionTests()
{
    AccountLifecycleRegistry& registry = GetAccountLifecycleRegistry();

    {
        // A caller-owned lease is validated and stays active, never consumed.
        AccountLifecycleLeasePtr borrowed =
            registry.TryAcquire({90001}, AccountLifecycleKind::Mutation);
        check(borrowed && borrowed->IsActive(), "borrowed lease acquired");
        {
            CharacterPersistenceAdmission admission;
            check(admission.Admit({90001}, AccountLifecycleKind::Mutation,
                                  borrowed.get()),
                  "borrowed lease validates admission");
            check(admission.IsAdmitted() && borrowed->IsActive(),
                  "borrowed lease remains active");
            check(!registry.TryAcquire({90001}, AccountLifecycleKind::Mutation),
                  "admitted account stays exclusive");
        }
        check(borrowed->IsActive(), "borrowed lease survives admission scope");
    }

    {
        // Owned multi-account admission covers all participants.
        {
            CharacterPersistenceAdmission admission;
            check(admission.Admit({90003, 90002, 90003},
                                  AccountLifecycleKind::Mutation),
                  "owned multi-account admission");
            check(admission.Accounts().size() == 2,
                  "duplicate accounts normalized");
            check(!registry.TryAcquire({90002}, AccountLifecycleKind::Mutation),
                  "all owned accounts held");
        }
        check(registry.TryAcquire({90002}, AccountLifecycleKind::Mutation) != nullptr,
              "owned accounts released on destruction");
    }

    {
        // Busy accounts reject before any staging.
        AccountLifecycleLeasePtr held =
            registry.TryAcquire({90004}, AccountLifecycleKind::Mutation);
        CharacterPersistenceAdmission admission;
        check(!admission.Admit({90004}, AccountLifecycleKind::Mutation),
              "busy account rejects admission");
        check(!admission.IsAdmitted(), "failed admission stays unadmitted");
    }

    {
        // Account-only recovery block is observed by admission.
        check(registry.BlockRecovery(CharacterRecoveryRecord{
                  CharacterRecoveryKind::WholeSave, {}, {90005}, 0,
                  "account-only block"}),
              "account-only recovery block recorded");
        CharacterPersistenceAdmission admission;
        check(!admission.Admit({90005}, AccountLifecycleKind::Mutation),
              "account-only recovery block rejects admission");
    }

    {
        // Wrong-kind borrowed leases, foreign registries and missing coverage.
        AccountLifecycleLeasePtr borrowed =
            registry.TryAcquire({90006}, AccountLifecycleKind::Mutation);
        CharacterPersistenceAdmission wrongKind;
        check(!wrongKind.Admit({90006}, AccountLifecycleKind::Deletion,
                               borrowed.get()),
              "wrong-kind borrowed lease rejected");

        AccountLifecycleRegistry foreignRegistry;
        AccountLifecycleLeasePtr foreign =
            foreignRegistry.TryAcquire({90007}, AccountLifecycleKind::Mutation);
        CharacterPersistenceAdmission foreignAdmission;
        check(!foreignAdmission.Admit({90007}, AccountLifecycleKind::Mutation,
                                      foreign.get()),
              "foreign-registry lease rejected");

        CharacterPersistenceAdmission uncovered;
        check(uncovered.Admit({90008}, AccountLifecycleKind::Mutation,
                              borrowed.get()),
              "uncovered affected account is acquired, not silently accepted");
        check(!registry.TryAcquire({90008}, AccountLifecycleKind::Mutation),
              "acquired uncovered account is held");
    }

    {
        // Borrowed coverage plus an acquired remainder, both held until end.
        AccountLifecycleLeasePtr borrowed =
            registry.TryAcquire({90009}, AccountLifecycleKind::Mutation);
        {
            CharacterPersistenceAdmission admission;
            check(admission.Admit({90009, 90010},
                                  AccountLifecycleKind::Mutation,
                                  borrowed.get()),
                  "borrowed plus acquired coverage");
            check(borrowed->IsActive(), "borrowed part still active");
            check(!registry.TryAcquire({90010}, AccountLifecycleKind::Mutation),
                  "acquired remainder held");
        }
        check(borrowed->IsActive(), "borrowed part survives completion");
        check(registry.TryAcquire({90010}, AccountLifecycleKind::Mutation) != nullptr,
              "acquired remainder released");
    }
}
void runTests()
{
    permanentDeletionBuilderTests();
    accountLifecycleRegistryTests();
    recoveryBlockingTests();
    identityBuilderTests();
    deletionBuilderFailureMatrix();
    deletionBuilderExecution();
    restorationBuilderFailureMatrix();
    classifierTests();
    reviewerTransitions();
    reviewerBindings();
    emptyOrInvalidBatchDoesNotSubmit();
    invalidationDiscardsAcknowledgments();
    destructionRunsNoAcknowledgments();
    guardZeroRowsStopsRelatedWrite();
    relatedFailureRollsBackWithoutAcknowledgment();
    uncertainCommitDoesNotAcknowledge();
    successfulCommitAcknowledgesOnce();
    failedRollbackDiscardsConnection();
    presetNonCompleteStageDoesNotAcknowledge();
    parametersAreOwnedByBatch();
    invalidIdentitySetPreventsSubmission();
    guardsPrecedePayloadInGuidOrder();
    secondCharacterGuardFailureStopsPayload();
    staleGuardStopsPayload();
    successfulGuardsAcknowledgeAfterCommit();
    uncertainCommitDoesNotAdvanceGenerations();
    guardAssemblyMustBeFirstAndSingle();
    snapshotValuesAreOwned();
    generationBoundary();
    validWithdrawalAssembly();
    invalidWithdrawalDoesNotSubmit();
    mailClaimFailureStopsBalanceWrite();
    balanceWriteFailureRollsBackClaim();
    unchangedStoredBalanceCanCommit();
    successfulWithdrawalPublishesOnlyAfterCommit();
    uncertainWithdrawalDoesNotPublish();
    ownedWithdrawalValues();
    bgDataAssemblyAndAck();
    bgDataFailureAndNewerState();
    reputationAssemblyAndAck();
    reputationNewerValuePreserved();
    actionAssemblyAndAck();
    actionNewerStatePreserved();
    actionExistenceReconciliation();
    composedWriterRollback();
    missingGuardRejectsWriters();
    questWriter();
    skillWriter();
    spellWriter();
    cooldownWriterAndNull();
    auraWriter();
    statsWriter();
    tutorialWriter();
    allWriterModes();
    persistenceAdmissionTests();
}
}

int main()
{
    runTests();
    std::cout << "Executed " << checks << " checks; " << failures
              << " failure(s)\n";
    return failures == 0 ? 0 : 1;
}

// Unused raw-write substitutes keep SqlWriteCallback linkable. Context tests
// use the checked executor and never call the raw write executor.
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
