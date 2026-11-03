#include "Database/SqlTransactionExecution.h"

#include <iostream>
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

struct FakeConnection
{
    bool beginResult;
    bool commitResult;
    bool rollbackResult;
    std::vector<std::string> events;

    FakeConnection()
        : beginResult(true), commitResult(true), rollbackResult(true)
    {
    }

    bool BeginTransaction()
    {
        events.push_back("begin");
        return beginResult;
    }

    bool CommitTransaction()
    {
        events.push_back("commit");
        return commitResult;
    }

    bool RollbackTransaction()
    {
        events.push_back("rollback");
        return rollbackResult;
    }
};

struct FakeOperation
{
    std::string name;
    bool result;

    FakeOperation(std::string operationName, bool operationResult)
        : name(std::move(operationName)), result(operationResult)
    {
    }

    bool Execute(FakeConnection* connection)
    {
        connection->events.push_back(name);
        return result;
    }
};

void emptyTransactionHasNoNativeCalls()
{
    FakeConnection connection;
    std::vector<FakeOperation*> operations;

    check(ExecuteSqlTransaction(connection, operations),
          "empty transaction succeeds");
    check(connection.events.empty(), "empty transaction has no native calls");
}

void beginFailureExecutesNothing()
{
    FakeConnection connection;
    connection.beginResult = false;
    FakeOperation operation("op", true);
    std::vector<FakeOperation*> operations = {&operation};

    check(!ExecuteSqlTransaction(connection, operations),
          "begin failure is failure");
    check(connection.events.size() == 1 && connection.events[0] == "begin",
          "begin failure executes no operation/commit/rollback");
}

void statementFailureStopsAndRollsBackOnce()
{
    FakeConnection connection;
    FakeOperation first("one", true);
    FakeOperation second("two", false);
    FakeOperation third("three", true);
    std::vector<FakeOperation*> operations = {&first, &second, &third};

    check(!ExecuteSqlTransaction(connection, operations),
          "statement failure is failure");
    std::vector<std::string> const expected = {"begin", "one", "two", "rollback"};
    check(connection.events == expected,
          "statement failure stops, rolls back once, no retry");
}

void rollbackFailureRemainsFailure()
{
    FakeConnection connection;
    connection.rollbackResult = false;
    FakeOperation first("one", true);
    FakeOperation second("two", false);
    std::vector<FakeOperation*> operations = {&first, &second};

    check(!ExecuteSqlTransaction(connection, operations),
          "rollback failure remains failure");
    std::vector<std::string> const expected = {"begin", "one", "two", "rollback"};
    check(connection.events == expected,
          "rollback failure does not retry/commit");
}

void successfulStatementsCommitOnceInOrder()
{
    FakeConnection connection;
    FakeOperation first("one", true);
    FakeOperation second("two", true);
    std::vector<FakeOperation*> operations = {&first, &second};

    check(ExecuteSqlTransaction(connection, operations),
          "successful transaction succeeds");
    std::vector<std::string> const expected = {"begin", "one", "two", "commit"};
    check(connection.events == expected,
          "successful statements execute in order and commit once");
}

void commitFailureReturnsFalse()
{
    FakeConnection connection;
    connection.commitResult = false;
    FakeOperation first("one", true);
    std::vector<FakeOperation*> operations = {&first};

    check(!ExecuteSqlTransaction(connection, operations),
          "commit failure is failure");
    std::vector<std::string> const expected = {"begin", "one", "commit"};
    check(connection.events == expected,
          "commit failure does not retry/rollback");
}

void runTests()
{
    emptyTransactionHasNoNativeCalls();
    beginFailureExecutesNothing();
    statementFailureStopsAndRollsBackOnce();
    rollbackFailureRemainsFailure();
    successfulStatementsCommitOnceInOrder();
    commitFailureReturnsFalse();
}
}

int main()
{
    runTests();
    std::cout << "Executed " << checks << " checks; " << failures
              << " failure(s)\n";
    return failures == 0 ? 0 : 1;
}
