#include "Database/SqlPreparedWrite.h"

#include <errmsg.h>
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

bool ValidTemplate(std::string const& sql,
                   std::vector<SqlStmtFieldData> const& parameters)
{
    if (sql.empty() || sql.find('\0') != std::string::npos ||
        sql.size() > std::numeric_limits<unsigned long>::max())
        return false;

    for (SqlStmtFieldData const& parameter : parameters)
    {
        if (parameter.type() == FIELD_STRING &&
            parameter.size() > std::numeric_limits<unsigned long>::max())
            return false;
    }
    return true;
}

class StatementGuard
{
    public:
        explicit StatementGuard(MYSQL_STMT* statement)
            : m_statement(statement), m_closed(false) {}

        ~StatementGuard()
        {
            if (m_statement && !m_closed)
                mysql_stmt_close(m_statement);
        }

        bool Close()
        {
            if (!m_statement || m_closed)
                return true;
            m_closed = true;
            return mysql_stmt_close(m_statement) == 0;
        }

    private:
        MYSQL_STMT* m_statement;
        bool m_closed;
};

SqlWriteResult CloseWithResult(StatementGuard& guard, MYSQL* connection,
                               SqlWriteResult result)
{
    if (!guard.Close())
    {
        result.outcome = SqlWriteOutcome::OutcomeUnknown;
        result.affectedRows = 0;
        if (result.nativeError == 0 && connection)
            result.nativeError = mysql_errno(connection);
    }
    return result;
}
}

void BindMySQLWriteParameter(MYSQL_BIND& binding,
                             SqlStmtFieldData const& value)
{
    binding = MYSQL_BIND();
    binding.length = nullptr;
    binding.is_null = nullptr;
    binding.error = nullptr;

    my_bool isUnsigned = 0;
    switch (value.type())
    {
        case FIELD_NONE:
            binding.buffer_type = MYSQL_TYPE_NULL;
            binding.buffer = nullptr;
            binding.buffer_length = 0;
            return;
        case FIELD_BOOL:
            binding.buffer_type = MYSQL_TYPE_BIT;
            isUnsigned = 1;
            break;
        case FIELD_I8:
            binding.buffer_type = MYSQL_TYPE_TINY;
            break;
        case FIELD_UI8:
            binding.buffer_type = MYSQL_TYPE_TINY;
            isUnsigned = 1;
            break;
        case FIELD_I16:
            binding.buffer_type = MYSQL_TYPE_SHORT;
            break;
        case FIELD_UI16:
            binding.buffer_type = MYSQL_TYPE_SHORT;
            isUnsigned = 1;
            break;
        case FIELD_I32:
            binding.buffer_type = MYSQL_TYPE_LONG;
            break;
        case FIELD_UI32:
            binding.buffer_type = MYSQL_TYPE_LONG;
            isUnsigned = 1;
            break;
        case FIELD_I64:
            binding.buffer_type = MYSQL_TYPE_LONGLONG;
            break;
        case FIELD_UI64:
            binding.buffer_type = MYSQL_TYPE_LONGLONG;
            isUnsigned = 1;
            break;
        case FIELD_FLOAT:
            binding.buffer_type = MYSQL_TYPE_FLOAT;
            break;
        case FIELD_DOUBLE:
            binding.buffer_type = MYSQL_TYPE_DOUBLE;
            break;
        case FIELD_STRING:
            binding.buffer_type = MYSQL_TYPE_STRING;
            break;
        default:
            binding.buffer_type = MYSQL_TYPE_NULL;
            binding.buffer = nullptr;
            binding.buffer_length = 0;
            return;
    }

    binding.is_unsigned = isUnsigned;
    binding.buffer = value.buff();
    binding.buffer_length = value.type() == FIELD_STRING
        ? static_cast<unsigned long>(value.size())
        : 0;
}

SqlWriteResult ExecuteMySQLPreparedWrite(
    st_mysql* connection,
    std::string const& sql,
    std::vector<SqlStmtFieldData> const& parameters)
{
    if (!connection || !ValidTemplate(sql, parameters))
        return Result(SqlWriteOutcome::NotExecuted);

    my_bool reconnect = 0;
    if (mysql_options(connection, MYSQL_OPT_RECONNECT, &reconnect) != 0)
        return Result(SqlWriteOutcome::NotExecuted, 0, mysql_errno(connection));

    MYSQL_STMT* statement = mysql_stmt_init(connection);
    if (!statement)
        return Result(SqlWriteOutcome::NotExecuted, 0, mysql_errno(connection));

    StatementGuard guard(statement);

    if (mysql_stmt_prepare(statement, sql.data(),
                           static_cast<unsigned long>(sql.size())) != 0)
    {
        uint32 const error = mysql_stmt_errno(statement);
        return CloseWithResult(
            guard, connection, Result(SqlWriteOutcome::NotExecuted, 0, error));
    }

    if (mysql_stmt_param_count(statement) != parameters.size())
        return CloseWithResult(
            guard, connection, Result(SqlWriteOutcome::NotExecuted));

    MYSQL_RES* metadata = mysql_stmt_result_metadata(statement);
    if (metadata)
    {
        mysql_free_result(metadata);
        return CloseWithResult(
            guard, connection, Result(SqlWriteOutcome::NotExecuted));
    }
    if (uint32 const metadataError = mysql_stmt_errno(statement))
    {
        return CloseWithResult(
            guard, connection,
            Result(SqlWriteOutcome::NotExecuted, 0, metadataError));
    }

    std::vector<MYSQL_BIND> bindings(parameters.size());
    std::vector<unsigned long> stringLengths(parameters.size(), 0);
    for (std::size_t i = 0; i < parameters.size(); ++i)
    {
        BindMySQLWriteParameter(bindings[i], parameters[i]);
        if (parameters[i].type() == FIELD_STRING)
        {
            stringLengths[i] =
                static_cast<unsigned long>(parameters[i].size());
            bindings[i].length = &stringLengths[i];
        }
    }

    if (!bindings.empty() &&
        mysql_stmt_bind_param(statement, bindings.data()) != 0)
    {
        uint32 const error = mysql_stmt_errno(statement);
        return CloseWithResult(
            guard, connection, Result(SqlWriteOutcome::NotExecuted, 0, error));
    }

    if (mysql_stmt_execute(statement) != 0)
    {
        uint32 const error = mysql_stmt_errno(statement);
        SqlWriteOutcome outcome = SqlWriteOutcome::Failed;
        if (error == ER_DUP_ENTRY)
            outcome = SqlWriteOutcome::DuplicateKey;
        else if (error >= CR_MIN_ERROR && error <= CR_MAX_ERROR)
            outcome = SqlWriteOutcome::OutcomeUnknown;
        else if (error == 0)
            outcome = SqlWriteOutcome::OutcomeUnknown;

        return CloseWithResult(
            guard, connection, Result(outcome, 0, error));
    }

    my_ulonglong const affected = mysql_stmt_affected_rows(statement);
    uint32 const affectedError = mysql_stmt_errno(statement);
    if (affected == static_cast<my_ulonglong>(-1))
        return CloseWithResult(
            guard, connection,
            Result(SqlWriteOutcome::OutcomeUnknown, 0, affectedError));

    return CloseWithResult(
        guard, connection,
        Result(SqlWriteOutcome::Succeeded, static_cast<uint64>(affected)));
}
