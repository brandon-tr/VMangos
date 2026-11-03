#include "Database/SqlWrite.h"

#include <errmsg.h>
#include <mysql.h>
#include <mysqld_error.h>

#include <limits>
#include <utility>

namespace
{
SqlWriteResult Result(SqlWriteOutcome outcome, uint64 affectedRows = 0,
                      uint32 nativeError = 0)
{
    SqlWriteResult result;
    result.outcome = outcome;
    result.affectedRows = affectedRows;
    result.nativeError = nativeError;
    return result;
}

bool ValidWriteInput(std::string const& sql)
{
    return !sql.empty() &&
           sql.find('\0') == std::string::npos &&
           sql.size() <= std::numeric_limits<unsigned long>::max();
}
}

SqlWriteResult ExecuteMySQLWrite(st_mysql* connection, std::string const& sql)
{
    if (!connection || !ValidWriteInput(sql))
        return Result(SqlWriteOutcome::NotExecuted);

    my_bool reconnect = 0;
    if (mysql_options(connection, MYSQL_OPT_RECONNECT, &reconnect) != 0)
        return Result(SqlWriteOutcome::NotExecuted, 0, mysql_errno(connection));

    int const queryResult = mysql_real_query(
        connection, sql.data(), static_cast<unsigned long>(sql.size()));
    if (queryResult != 0)
    {
        uint32 const error = mysql_errno(connection);
        if (error == ER_DUP_ENTRY)
            return Result(SqlWriteOutcome::DuplicateKey, 0, error);
        if (error >= CR_MIN_ERROR && error <= CR_MAX_ERROR)
            return Result(SqlWriteOutcome::OutcomeUnknown, 0, error);
        if (error != 0)
            return Result(SqlWriteOutcome::Failed, 0, error);
        return Result(SqlWriteOutcome::OutcomeUnknown, 0, error);
    }

    if (mysql_field_count(connection) != 0)
    {
        MYSQL_RES* result = mysql_store_result(connection);
        if (!result)
        {
            uint32 const error = mysql_errno(connection);
            return Result(SqlWriteOutcome::OutcomeUnknown, 0, error);
        }

        mysql_free_result(result);
        return Result(SqlWriteOutcome::Failed);
    }

    my_ulonglong const affected = mysql_affected_rows(connection);
    if (affected == static_cast<my_ulonglong>(-1))
    {
        uint32 const error = mysql_errno(connection);
        return Result(SqlWriteOutcome::OutcomeUnknown, 0, error);
    }

    return Result(SqlWriteOutcome::Succeeded, static_cast<uint64>(affected));
}

SqlWriteCallback::SqlWriteCallback(SqlWriteResult result,
                                   SqlWriteCompletion completion)
    : m_writeResult(result),
      m_completion(std::move(completion)),
      m_delivered(false)
{
    threadSafe = false;
}

void SqlWriteCallback::Execute()
{
    if (m_delivered)
        return;

    m_delivered = true;
    SqlWriteCompletion completion = std::move(m_completion);
    m_completion = SqlWriteCompletion();
    if (completion)
        completion(m_writeResult);
}

void SqlWriteCallback::SetResult(std::unique_ptr<QueryResult> result)
{
    // Legacy cancellation must not erase the typed write result.
    m_queryResult = std::move(result);
}

std::unique_ptr<QueryResult>& SqlWriteCallback::GetResult()
{
    return m_queryResult;
}
