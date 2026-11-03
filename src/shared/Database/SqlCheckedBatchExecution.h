#ifndef SQLCHECKEDBATCHEXECUTION_H
#define SQLCHECKEDBATCHEXECUTION_H

#include "Database/SqlCheckedBatch.h"

#include <cstddef>
#include <utility>

inline SqlBatchResult EmptySqlBatchResult()
{
    SqlWriteResult empty = {SqlWriteOutcome::NotExecuted, 0, 0};
    SqlBatchResult result;
    result.outcome = SqlWriteOutcome::NotExecuted;
    result.stage = SqlBatchStage::NotStarted;
    result.statementIndex = static_cast<std::size_t>(-1);
    result.cause = empty;
    result.rollbackAttempted = false;
    result.rollbackResult = empty;
    return result;
}

template<class Connection>
SqlBatchResult ExecuteSqlCheckedBatch(
    Connection& connection,
    std::vector<SqlBoundWrite> const& writes)
{
    SqlBatchResult result = EmptySqlBatchResult();
    if (writes.empty())
        return result;

    result.stage = SqlBatchStage::Begin;
    SqlWriteResult const begin = connection.ExecuteChecked("START TRANSACTION");
    result.cause = begin;
    if (begin.outcome != SqlWriteOutcome::Succeeded)
    {
        if (begin.outcome == SqlWriteOutcome::OutcomeUnknown)
        {
            result.rollbackAttempted = true;
            result.rollbackResult = connection.ExecuteChecked("ROLLBACK");
            if (result.rollbackResult.outcome != SqlWriteOutcome::Succeeded)
                connection.DiscardConnection();
        }
        result.outcome = begin.outcome;
        return result;
    }

    bool stopped = false;
    for (std::size_t i = 0; i < writes.size(); ++i)
    {
        SqlBoundWrite const& write = writes[i];
        result.stage = SqlBatchStage::Statement;
        result.statementIndex = i;

        SqlWriteResult const statement = connection.ExecutePreparedChecked(
            write.sql, write.parameters);
        result.statementResults.push_back(statement);
        if (statement.outcome != SqlWriteOutcome::Succeeded)
        {
            result.cause = statement;
            stopped = true;
            break;
        }

        if (write.affectedRowsPolicy == SqlAffectedRowsPolicy::ExactlyOne &&
            statement.affectedRows != 1)
        {
            result.stage = SqlBatchStage::RowCount;
            result.cause = statement;
            stopped = true;
            break;
        }
    }

    if (stopped)
    {
        result.rollbackAttempted = true;
        result.rollbackResult = connection.ExecuteChecked("ROLLBACK");
        bool const rollbackOk =
            result.rollbackResult.outcome == SqlWriteOutcome::Succeeded;
        if (!rollbackOk)
            connection.DiscardConnection();

        if (result.cause.outcome == SqlWriteOutcome::OutcomeUnknown || !rollbackOk)
            result.outcome = SqlWriteOutcome::OutcomeUnknown;
        else if (result.cause.outcome == SqlWriteOutcome::DuplicateKey)
            result.outcome = SqlWriteOutcome::DuplicateKey;
        else
            result.outcome = SqlWriteOutcome::Failed;
        return result;
    }

    result.statementIndex = static_cast<std::size_t>(-1);
    result.stage = SqlBatchStage::Commit;
    SqlWriteResult const commit = connection.ExecuteChecked("COMMIT");
    result.cause = commit;
    if (commit.outcome == SqlWriteOutcome::Succeeded)
    {
        result.outcome = SqlWriteOutcome::Succeeded;
        result.stage = SqlBatchStage::Complete;
        return result;
    }

    result.outcome = SqlWriteOutcome::OutcomeUnknown;
    result.rollbackAttempted = true;
    result.rollbackResult = connection.ExecuteChecked("ROLLBACK");
    if (result.rollbackResult.outcome != SqlWriteOutcome::Succeeded)
        connection.DiscardConnection();
    return result;
}

#endif
