#include "Database/SqlWrite.h"
#include "Database/SqlPreparedWrite.h"

#include <mysql.h>

#include <cstdint>
#include <functional>
#include <iostream>
#include <limits>
#include <string>
#include <thread>
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

struct MockState
{
    int optionCalls;
    int queryCalls;
    int freeCalls;
    int optionResult;
    int queryResult;
    uint32 error;
    unsigned int fieldCount;
    my_ulonglong affected;
    MYSQL_RES* result;
    std::string sql;
    unsigned long length;
    bool reconnectOption;
    bool reconnectValue;
};

MockState mock;

struct PreparedMock
{
    int initCalls;
    int prepareCalls;
    int bindCalls;
    int executeCalls;
    int metadataCalls;
    int closeCalls;
    int initResult;
    int prepareResult;
    int bindResult;
    int executeResult;
    int closeResult;
    uint32 closeConnectionError;
    bool resultMetadata;
    uint32 prepareError;
    uint32 bindError;
    uint32 executeError;
    uint32 metadataError;
    uint32 currentError;
    unsigned long paramCount;
    unsigned long preparedLength;
    std::string preparedSql;
    my_ulonglong affected;
    std::vector<MYSQL_BIND> bindings;
    std::vector<unsigned long> lengths;
};

PreparedMock prepared;

void resetMock()
{
    mock.optionCalls = 0;
    mock.queryCalls = 0;
    mock.freeCalls = 0;
    mock.optionResult = 0;
    mock.queryResult = 0;
    mock.error = 0;
    mock.fieldCount = 0;
    mock.affected = 0;
    mock.result = nullptr;
    mock.sql.clear();
    mock.length = 0;
    mock.reconnectOption = false;
    mock.reconnectValue = true;

    prepared.initCalls = 0;
    prepared.prepareCalls = 0;
    prepared.bindCalls = 0;
    prepared.executeCalls = 0;
    prepared.metadataCalls = 0;
    prepared.closeCalls = 0;
    prepared.initResult = 0;
    prepared.prepareResult = 0;
    prepared.bindResult = 0;
    prepared.executeResult = 0;
    prepared.closeResult = 0;
    prepared.closeConnectionError = 0;
    prepared.resultMetadata = false;
    prepared.prepareError = 0;
    prepared.bindError = 0;
    prepared.executeError = 0;
    prepared.metadataError = 0;
    prepared.currentError = 0;
    prepared.paramCount = 0;
    prepared.preparedLength = 0;
    prepared.preparedSql.clear();
    prepared.affected = 0;
    prepared.bindings.clear();
    prepared.lengths.clear();
}

void expectResult(SqlWriteResult const& result, SqlWriteOutcome outcome,
                  uint64 affectedRows, uint32 nativeError,
                  std::string const& label)
{
    check(result.outcome == outcome, label + " outcome");
    check(result.affectedRows == affectedRows, label + " affected rows");
    check(result.nativeError == nativeError, label + " native error");
}

void invalidWriteDoesNotExecute()
{
    resetMock();
    MYSQL connection = {};

    expectResult(ExecuteMySQLWrite(nullptr, "UPDATE t SET c=1"),
                 SqlWriteOutcome::NotExecuted, 0, 0, "invalid/null");
    expectResult(ExecuteMySQLWrite(&connection, ""),
                 SqlWriteOutcome::NotExecuted, 0, 0, "invalid/empty");
    std::string embedded("UPDATE t SET c=1");
    embedded.push_back('\0');
    embedded.append("SELECT 1");
    expectResult(ExecuteMySQLWrite(&connection, embedded),
                 SqlWriteOutcome::NotExecuted, 0, 0, "invalid/nul");
    check(mock.queryCalls == 0, "invalid write must not query");
}

void reconnectDisableFailureDoesNotExecute()
{
    resetMock();
    MYSQL connection = {};
    mock.optionResult = -1;
    mock.error = 4321;

    expectResult(ExecuteMySQLWrite(&connection, "UPDATE t SET c=1"),
                 SqlWriteOutcome::NotExecuted, 0, 4321, "option failure");
    check(mock.optionCalls == 1, "option failure calls mysql_options once");
    check(mock.queryCalls == 0, "option failure must not query");
}

void writeSuccessPreservesAffectedRows()
{
    uint64 const counts[] = {0, 1, static_cast<uint64>(std::numeric_limits<uint32>::max()) + 5};
    for (uint64 count : counts)
    {
        resetMock();
        MYSQL connection = {};
        mock.affected = static_cast<my_ulonglong>(count);

        expectResult(ExecuteMySQLWrite(&connection, "UPDATE t SET c=1"),
                     SqlWriteOutcome::Succeeded, count, 0, "success/count");
        check(mock.queryCalls == 1, "success queries once");
        check(mock.reconnectOption, "success sets reconnect option");
        check(!mock.reconnectValue, "success disables reconnect");
    }
}

void duplicateKeyIsDistinct()
{
    resetMock();
    MYSQL connection = {};
    mock.queryResult = 1;
    mock.error = 1062;
    mock.affected = 77;

    expectResult(ExecuteMySQLWrite(&connection, "INSERT INTO t VALUES (1)"),
                 SqlWriteOutcome::DuplicateKey, 0, 1062, "duplicate");
    check(mock.queryCalls == 1, "duplicate queries once");
}

void serverErrorIsFailed()
{
    resetMock();
    MYSQL connection = {};
    mock.queryResult = 1;
    mock.error = 1146;
    mock.affected = 9;

    expectResult(ExecuteMySQLWrite(&connection, "UPDATE missing SET c=1"),
                 SqlWriteOutcome::Failed, 0, 1146, "server error");
    check(mock.queryCalls == 1, "server error queries once");
}

void transportFailureIsUnknownAndNotReplayed()
{
    uint32 const errors[] = {2006, 2013, 2055, 2050};
    for (uint32 error : errors)
    {
        resetMock();
        MYSQL connection = {};
        mock.queryResult = 1;
        mock.error = error;

        expectResult(ExecuteMySQLWrite(&connection, "UPDATE t SET c=1"),
                     SqlWriteOutcome::OutcomeUnknown, 0, error,
                     "transport/" + std::to_string(error));
        check(mock.queryCalls == 1, "transport query exactly once");
    }
}

void missingErrorIsUnknown()
{
    resetMock();
    MYSQL connection = {};
    mock.queryResult = 1;
    mock.error = 0;

    expectResult(ExecuteMySQLWrite(&connection, "UPDATE t SET c=1"),
                 SqlWriteOutcome::OutcomeUnknown, 0, 0, "missing error");
    check(mock.queryCalls == 1, "missing error queries once");
}

void invalidAffectedRowsIsUnknown()
{
    resetMock();
    MYSQL connection = {};
    mock.affected = static_cast<my_ulonglong>(-1);

    expectResult(ExecuteMySQLWrite(&connection, "UPDATE t SET c=1"),
                 SqlWriteOutcome::OutcomeUnknown, 0, 0, "affected sentinel");
    check(mock.queryCalls == 1, "affected sentinel queries once");
}

void resultSetIsConsumedAndRejected()
{
    resetMock();
    MYSQL connection = {};
    mock.fieldCount = 1;
    mock.result = reinterpret_cast<MYSQL_RES*>(0x1);

    SqlWriteResult const result =
        ExecuteMySQLWrite(&connection, "SELECT 1");
    check(result.outcome != SqlWriteOutcome::Succeeded,
          "result set must not be success");
    check(mock.freeCalls == 1, "result set freed exactly once");
    check(mock.queryCalls == 1, "result set queries once");
}

void checkedWriteUsesFullLength()
{
    resetMock();
    MYSQL connection = {};
    std::string const sql = "UPDATE `t` SET `c`='value' WHERE `id`=42";
    mock.affected = 1;

    expectResult(ExecuteMySQLWrite(&connection, sql),
                 SqlWriteOutcome::Succeeded, 1, 0, "full length");
    check(mock.sql == sql, "native query received complete bytes");
    check(mock.length == static_cast<unsigned long>(sql.size()),
          "native query received explicit full length");
}

void completionIsDeferredAndOnce()
{
    SqlWriteResult const result = {SqlWriteOutcome::Succeeded, 3, 0};
    int calls = 0;
    SqlWriteCallback* callbackPtr = nullptr;
    SqlWriteCallback callback(result, [&](SqlWriteResult observed) {
        ++calls;
        check(observed.outcome == SqlWriteOutcome::Succeeded &&
                  observed.affectedRows == 3 && observed.nativeError == 0,
              "completion result");
        if (callbackPtr)
            callbackPtr->Execute();
    });
    callbackPtr = &callback;

    check(calls == 0, "construction does not invoke completion");
    callback.Execute();
    check(calls == 1, "execute invokes completion once");
    callback.Execute();
    check(calls == 1, "reentrant/repeated execute is ignored");
}

void completionStaysOnCaller()
{
    SqlWriteResult const result = {SqlWriteOutcome::Failed, 0, 9};
    std::thread::id const caller = std::this_thread::get_id();
    std::thread::id observed;
    int calls = 0;
    SqlWriteCallback callback(result, [&](SqlWriteResult) {
        ++calls;
        observed = std::this_thread::get_id();
    });

    check(!callback.IsThreadSafe(), "write callback is thread-unsafe routed");
    callback.Execute();
    check(calls == 1, "caller completion invoked");
    check(observed == caller, "completion observed the invoking thread");
}

void cancellationPreservesWriteResult()
{
    SqlWriteResult const outcomes[] = {
        {SqlWriteOutcome::Succeeded, 12, 0},
        {SqlWriteOutcome::DuplicateKey, 0, 1062},
        {SqlWriteOutcome::Failed, 0, 1146},
        {SqlWriteOutcome::OutcomeUnknown, 0, 2013},
    };

    for (SqlWriteResult const& expected : outcomes)
    {
        int calls = 0;
        SqlWriteCallback callback(expected, [&](SqlWriteResult observed) {
            ++calls;
            expectResult(observed, expected.outcome, expected.affectedRows,
                         expected.nativeError, "cancellation");
        });

        callback.SetResult(nullptr);
        callback.Execute();
        check(calls == 1, "cancellation still delivers completion");
    }
}

void completionDestructionDoesNotInvoke()
{
    int calls = 0;
    {
        SqlWriteResult const result = {SqlWriteOutcome::Succeeded, 1, 0};
        SqlWriteCallback callback(result, [&](SqlWriteResult) { ++calls; });
    }
    check(calls == 0, "destructor does not invoke completion");
}

void preparedInvalidInputDoesNotExecute()
{
    resetMock();
    MYSQL connection = {};
    std::vector<SqlStmtFieldData> parameters;

    expectResult(ExecuteMySQLPreparedWrite(nullptr, "UPDATE t SET c=?", parameters),
                 SqlWriteOutcome::NotExecuted, 0, 0, "prepared/null");
    expectResult(ExecuteMySQLPreparedWrite(&connection, "", parameters),
                 SqlWriteOutcome::NotExecuted, 0, 0, "prepared/empty");
    std::string embedded("UPDATE t SET c=?");
    embedded.push_back('\0');
    expectResult(ExecuteMySQLPreparedWrite(&connection, embedded, parameters),
                 SqlWriteOutcome::NotExecuted, 0, 0, "prepared/nul");
    check(prepared.initCalls == 0, "invalid prepared input must not init");
}

void preparedReconnectFailureDoesNotInitialize()
{
    resetMock();
    MYSQL connection = {};
    std::vector<SqlStmtFieldData> parameters;
    mock.optionResult = -1;
    mock.error = 4321;

    expectResult(ExecuteMySQLPreparedWrite(&connection, "UPDATE t SET c=?", parameters),
                 SqlWriteOutcome::NotExecuted, 0, 4321, "prepared/option");
    check(prepared.initCalls == 0, "option failure must not init");
    check(prepared.executeCalls == 0, "option failure must not execute");
}

void preparedInitializationFailure()
{
    resetMock();
    MYSQL connection = {};
    std::vector<SqlStmtFieldData> parameters;
    prepared.initResult = -1;
    mock.error = 1234;

    expectResult(ExecuteMySQLPreparedWrite(&connection, "UPDATE t SET c=?", parameters),
                 SqlWriteOutcome::NotExecuted, 0, 1234, "prepared/init");
    check(prepared.initCalls == 1, "init attempted once");
    check(prepared.executeCalls == 0, "init failure must not execute");
}

void preparedPreparationFailure()
{
    resetMock();
    MYSQL connection = {};
    std::vector<SqlStmtFieldData> parameters;
    prepared.prepareResult = -1;
    prepared.prepareError = 1064;

    expectResult(ExecuteMySQLPreparedWrite(&connection, "UPDATE t SET c=?", parameters),
                 SqlWriteOutcome::NotExecuted, 0, 1064, "prepared/prepare");
    check(prepared.prepareCalls == 1, "prepare attempted once");
    check(prepared.executeCalls == 0, "prepare failure must not execute");
    check(prepared.closeCalls == 1, "prepare failure closes once");
}

void preparedParameterCountMismatch()
{
    resetMock();
    MYSQL connection = {};
    std::vector<SqlStmtFieldData> parameters;
    parameters.emplace_back(static_cast<uint32>(1));
    prepared.paramCount = 2;

    expectResult(ExecuteMySQLPreparedWrite(&connection, "UPDATE t SET c=?, d=?", parameters),
                 SqlWriteOutcome::NotExecuted, 0, 0, "prepared/param count");
    check(prepared.executeCalls == 0, "param mismatch must not execute");
    check(prepared.closeCalls == 1, "param mismatch closes once");
}

void preparedBindingFailureDoesNotExecute()
{
    resetMock();
    MYSQL connection = {};
    std::vector<SqlStmtFieldData> parameters;
    parameters.emplace_back(static_cast<uint32>(1));
    prepared.paramCount = 1;
    prepared.bindResult = -1;
    prepared.bindError = 9999;

    expectResult(ExecuteMySQLPreparedWrite(&connection, "UPDATE t SET c=?", parameters),
                 SqlWriteOutcome::NotExecuted, 0, 9999, "prepared/bind");
    check(prepared.bindCalls == 1, "bind attempted once");
    check(prepared.executeCalls == 0, "bind failure must not execute");
    check(prepared.closeCalls == 1, "bind failure closes once");
}

void preparedResultSetRejected()
{
    resetMock();
    MYSQL connection = {};
    prepared.resultMetadata = true;

    expectResult(ExecuteMySQLPreparedWrite(&connection, "SELECT ?", {}),
                 SqlWriteOutcome::NotExecuted, 0, 0, "prepared/result");
    check(prepared.metadataCalls == 1, "metadata inspected");
    check(mock.freeCalls == 1, "result metadata freed once");
    check(prepared.executeCalls == 0, "result set must not execute");
}

void preparedTypesAndBuffers()
{
    resetMock();
    MYSQL connection = {};
    std::string const text = "h\xC3\xA9llo'\"";
    std::vector<SqlStmtFieldData> parameters;
    parameters.emplace_back(nullptr);
    parameters.emplace_back(true);
    parameters.emplace_back(static_cast<uint8>(1));
    parameters.emplace_back(static_cast<int8>(-2));
    parameters.emplace_back(static_cast<uint16>(3));
    parameters.emplace_back(static_cast<int16>(-4));
    parameters.emplace_back(static_cast<uint32>(5));
    parameters.emplace_back(static_cast<int32>(-6));
    parameters.emplace_back(static_cast<uint64>(7));
    parameters.emplace_back(static_cast<int64>(-8));
    parameters.emplace_back(1.5f);
    parameters.emplace_back(2.5);
    parameters.emplace_back("");
    parameters.emplace_back(text.c_str());
    prepared.paramCount = parameters.size();
    prepared.affected = 0;

    expectResult(ExecuteMySQLPreparedWrite(&connection, "UPDATE t SET c=?", parameters),
                 SqlWriteOutcome::Succeeded, 0, 0, "prepared/types");
    check(prepared.executeCalls == 1, "types execute once");
    check(prepared.bindCalls == 1, "types bind once");
    check(prepared.bindings.size() == parameters.size(), "all binds captured");
    check(prepared.bindings[0].buffer_type == MYSQL_TYPE_NULL,
          "NULL bind type");
    check(prepared.bindings[0].buffer == nullptr, "NULL bind buffer");
    check(prepared.bindings[1].buffer_type == MYSQL_TYPE_BIT &&
              prepared.bindings[1].is_unsigned == 1, "bool bind type");
    check(prepared.bindings[2].buffer_type == MYSQL_TYPE_TINY &&
              prepared.bindings[2].is_unsigned == 1, "uint8 bind type");
    check(prepared.bindings[3].buffer_type == MYSQL_TYPE_TINY &&
              prepared.bindings[3].is_unsigned == 0, "int8 bind type");
    check(prepared.bindings[4].buffer_type == MYSQL_TYPE_SHORT &&
              prepared.bindings[4].is_unsigned == 1, "uint16 bind type");
    check(prepared.bindings[5].buffer_type == MYSQL_TYPE_SHORT &&
              prepared.bindings[5].is_unsigned == 0, "int16 bind type");
    check(prepared.bindings[6].buffer_type == MYSQL_TYPE_LONG &&
              prepared.bindings[6].is_unsigned == 1, "uint32 bind type");
    check(prepared.bindings[7].buffer_type == MYSQL_TYPE_LONG &&
              prepared.bindings[7].is_unsigned == 0, "int32 bind type");
    check(prepared.bindings[8].buffer_type == MYSQL_TYPE_LONGLONG &&
              prepared.bindings[8].is_unsigned == 1, "uint64 bind type");
    check(prepared.bindings[9].buffer_type == MYSQL_TYPE_LONGLONG &&
              prepared.bindings[9].is_unsigned == 0, "int64 bind type");
    check(prepared.bindings[10].buffer_type == MYSQL_TYPE_FLOAT, "float bind type");
    check(prepared.bindings[11].buffer_type == MYSQL_TYPE_DOUBLE, "double bind type");
    check(prepared.bindings[12].buffer_type == MYSQL_TYPE_STRING &&
              prepared.lengths[12] == 0, "empty string length");
    check(prepared.bindings[13].buffer_type == MYSQL_TYPE_STRING &&
              prepared.lengths[13] == text.size(), "nonascii string length");
    check(prepared.preparedLength == std::string("UPDATE t SET c=?").size(),
          "prepared template length");
    check(prepared.closeCalls == 1, "types close once");
}

void preparedAffectedRowsAndErrors()
{
    uint64 const counts[] = {0, 1, static_cast<uint64>(std::numeric_limits<uint32>::max()) + 5};
    for (uint64 count : counts)
    {
        resetMock();
        MYSQL connection = {};
        prepared.affected = static_cast<my_ulonglong>(count);
        expectResult(ExecuteMySQLPreparedWrite(&connection, "UPDATE t SET c=?", {}),
                     SqlWriteOutcome::Succeeded, count, 0, "prepared/count");
        check(prepared.executeCalls == 1, "count execute once");
        check(prepared.closeCalls == 1, "count close once");
    }

    struct ErrorCase { uint32 error; SqlWriteOutcome outcome; };
    ErrorCase const cases[] = {
        {1062, SqlWriteOutcome::DuplicateKey},
        {2006, SqlWriteOutcome::OutcomeUnknown},
        {1146, SqlWriteOutcome::Failed},
        {0, SqlWriteOutcome::OutcomeUnknown},
    };
    for (ErrorCase const& item : cases)
    {
        resetMock();
        MYSQL connection = {};
        prepared.executeResult = -1;
        prepared.executeError = item.error;
        expectResult(ExecuteMySQLPreparedWrite(&connection, "UPDATE t SET c=?", {}),
                     item.outcome, 0, item.error, "prepared/error");
        check(prepared.executeCalls == 1, "error execute once");
        check(prepared.closeCalls == 1, "error close once");
    }

    resetMock();
    MYSQL connection = {};
    prepared.affected = static_cast<my_ulonglong>(-1);
    expectResult(ExecuteMySQLPreparedWrite(&connection, "UPDATE t SET c=?", {}),
                 SqlWriteOutcome::OutcomeUnknown, 0, 0, "prepared/sentinel");
}

void preparedCloseFailureIsUnknown()
{
    resetMock();
    MYSQL connection = {};
    prepared.affected = 1;
    prepared.closeResult = 1;
    prepared.closeConnectionError = 2013;

    expectResult(ExecuteMySQLPreparedWrite(&connection, "UPDATE t SET c=?", {}),
                 SqlWriteOutcome::OutcomeUnknown, 0, 2013,
                 "prepared/close failure");
    check(prepared.executeCalls == 1, "close failure executed once");
    check(prepared.closeCalls == 1, "close failure closed once");
}

void preparedEarlyCloseFailureIsUnknown()
{
    resetMock();
    MYSQL connection = {};
    prepared.prepareResult = -1;
    prepared.prepareError = 1064;
    prepared.closeResult = 1;
    prepared.closeConnectionError = 2013;

    expectResult(ExecuteMySQLPreparedWrite(&connection, "UPDATE t SET c=?", {}),
                 SqlWriteOutcome::OutcomeUnknown, 0, 1064,
                 "prepared/early close failure");
    check(prepared.prepareCalls == 1, "early close prepare attempted once");
    check(prepared.executeCalls == 0, "early close must not execute");
    check(prepared.closeCalls == 1, "early close observed once");
}

void preparedClosePathCoverage()
{
    struct CloseCase
    {
        char const* label;
        std::function<void()> setup;
        std::vector<SqlStmtFieldData> parameters;
        uint32 expectedNative;
        int expectedExecutions;
    };

    std::vector<CloseCase> cases;
    cases.push_back({"prepared/close/prepare", [&]() {
        prepared.prepareResult = -1;
        prepared.prepareError = 1064;
    }, {}, 1064, 0});
    cases.push_back({"prepared/close/param_count", [&]() {
        prepared.paramCount = 2;
    }, {SqlStmtFieldData(static_cast<uint32>(1))}, 2013, 0});
    cases.push_back({"prepared/close/result_metadata", [&]() {
        prepared.resultMetadata = true;
    }, {}, 2013, 0});
    cases.push_back({"prepared/close/metadata_error", [&]() {
        prepared.metadataError = 1234;
    }, {}, 1234, 0});
    cases.push_back({"prepared/close/bind", [&]() {
        prepared.paramCount = 1;
        prepared.bindResult = -1;
        prepared.bindError = 9999;
    }, {SqlStmtFieldData(static_cast<uint32>(1))}, 9999, 0});
    cases.push_back({"prepared/close/execute", [&]() {
        prepared.executeResult = -1;
        prepared.executeError = 1146;
    }, {}, 1146, 1});
    cases.push_back({"prepared/close/affected_sentinel", [&]() {
        prepared.affected = static_cast<my_ulonglong>(-1);
    }, {}, 2013, 1});
    cases.push_back({"prepared/close/success", [&]() {
        prepared.affected = 1;
    }, {}, 2013, 1});

    for (CloseCase const& item : cases)
    {
        resetMock();
        MYSQL connection = {};
        prepared.closeResult = 1;
        prepared.closeConnectionError = 2013;
        item.setup();

        expectResult(ExecuteMySQLPreparedWrite(
                         &connection, "UPDATE t SET c=?", item.parameters),
                     SqlWriteOutcome::OutcomeUnknown, 0, item.expectedNative,
                     item.label);
        check(prepared.executeCalls == item.expectedExecutions,
              std::string(item.label) + " execute count");
        check(prepared.closeCalls == 1,
              std::string(item.label) + " close once");
    }
}

void runTests()
{
    invalidWriteDoesNotExecute();
    reconnectDisableFailureDoesNotExecute();
    writeSuccessPreservesAffectedRows();
    duplicateKeyIsDistinct();
    serverErrorIsFailed();
    transportFailureIsUnknownAndNotReplayed();
    missingErrorIsUnknown();
    invalidAffectedRowsIsUnknown();
    resultSetIsConsumedAndRejected();
    checkedWriteUsesFullLength();
    completionIsDeferredAndOnce();
    completionStaysOnCaller();
    cancellationPreservesWriteResult();
    completionDestructionDoesNotInvoke();
    preparedInvalidInputDoesNotExecute();
    preparedReconnectFailureDoesNotInitialize();
    preparedInitializationFailure();
    preparedPreparationFailure();
    preparedParameterCountMismatch();
    preparedBindingFailureDoesNotExecute();
    preparedResultSetRejected();
    preparedTypesAndBuffers();
    preparedAffectedRowsAndErrors();
    preparedCloseFailureIsUnknown();
    preparedEarlyCloseFailureIsUnknown();
    preparedClosePathCoverage();
}
}

// Native MySQL substitutes: no mysqlclient is linked and no network calls occur.
extern "C"
{
int STDCALL mysql_options(MYSQL*, enum mysql_option option, void const* argument)
{
    ++mock.optionCalls;
    mock.reconnectOption = option == MYSQL_OPT_RECONNECT;
    if (argument)
        mock.reconnectValue =
            *static_cast<my_bool const*>(argument) != 0;
    return mock.optionResult;
}

int STDCALL mysql_real_query(MYSQL*, char const* query, unsigned long length)
{
    ++mock.queryCalls;
    mock.sql.assign(query, length);
    mock.length = length;
    return mock.queryResult;
}

unsigned int STDCALL mysql_errno(MYSQL*)
{
    return mock.error;
}

unsigned int STDCALL mysql_field_count(MYSQL*)
{
    return mock.fieldCount;
}

MYSQL_RES* STDCALL mysql_store_result(MYSQL*)
{
    return mock.result;
}

void STDCALL mysql_free_result(MYSQL_RES*)
{
    ++mock.freeCalls;
}

my_ulonglong STDCALL mysql_affected_rows(MYSQL*)
{
    return mock.affected;
}

MYSQL_STMT* STDCALL mysql_stmt_init(MYSQL*)
{
    ++prepared.initCalls;
    if (prepared.initResult != 0)
        return nullptr;
    return reinterpret_cast<MYSQL_STMT*>(0x1);
}

int STDCALL mysql_stmt_prepare(MYSQL_STMT*, char const* query,
                               unsigned long length)
{
    ++prepared.prepareCalls;
    prepared.preparedSql.assign(query, length);
    prepared.preparedLength = length;
    prepared.currentError = prepared.prepareError;
    return prepared.prepareResult;
}

unsigned long STDCALL mysql_stmt_param_count(MYSQL_STMT*)
{
    return prepared.paramCount;
}

MYSQL_RES* STDCALL mysql_stmt_result_metadata(MYSQL_STMT*)
{
    ++prepared.metadataCalls;
    prepared.currentError = prepared.metadataError;
    return prepared.resultMetadata
        ? reinterpret_cast<MYSQL_RES*>(0x2)
        : nullptr;
}

my_bool STDCALL mysql_stmt_bind_param(MYSQL_STMT*,
                                      MYSQL_BIND* bindings)
{
    ++prepared.bindCalls;
    prepared.bindings.assign(bindings, bindings + prepared.paramCount);
    prepared.lengths.clear();
    prepared.lengths.reserve(prepared.paramCount);
    for (unsigned long i = 0; i < prepared.paramCount; ++i)
        prepared.lengths.push_back(bindings[i].length ? *bindings[i].length : 0);
    prepared.currentError = prepared.bindError;
    return prepared.bindResult;
}

int STDCALL mysql_stmt_execute(MYSQL_STMT*)
{
    ++prepared.executeCalls;
    prepared.currentError = prepared.executeError;
    return prepared.executeResult;
}

my_ulonglong STDCALL mysql_stmt_affected_rows(MYSQL_STMT*)
{
    return prepared.affected;
}

unsigned int STDCALL mysql_stmt_errno(MYSQL_STMT*)
{
    return prepared.currentError;
}

my_bool STDCALL mysql_stmt_close(MYSQL_STMT*)
{
    ++prepared.closeCalls;
    if (prepared.closeResult != 0)
        mock.error = prepared.closeConnectionError;
    return prepared.closeResult;
}
}

int main()
{
    runTests();
    std::cout << "Executed " << checks << " checks; " << failures
              << " failure(s)\n";
    return failures == 0 ? 0 : 1;
}
