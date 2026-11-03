/*
 * Checked single-statement write execution.
 *
 * This is deliberately not a general SQL executor: callers must pass one
 * already constructed, trusted DML statement.  The result describes what the
 * native call observed; it does not promise transaction commit or durability.
 */

#ifndef SQLWRITE_H
#define SQLWRITE_H

#include "Database/DatabaseCallback.h"

#include <cstdint>
#include <functional>
#include <memory>
#include <string>

struct st_mysql;

enum class SqlWriteOutcome
{
    NotExecuted,
    Succeeded,
    DuplicateKey,
    Failed,
    OutcomeUnknown
};

struct SqlWriteResult
{
    SqlWriteOutcome outcome;
    uint64 affectedRows;
    uint32 nativeError;
};

using SqlWriteCompletion = std::function<void(SqlWriteResult)>;

/// Execute one trusted DML statement without reconnect or replay.
SqlWriteResult ExecuteMySQLWrite(
    st_mysql* connection,
    std::string const& sql);

/// Completion callback for SqlCheckedWrite.  Deliberately marked thread-unsafe
/// so the existing result queue delivers it on the Update()/CancelAll() caller.
class SqlWriteCallback : public MaNGOS::IQueryCallback
{
    public:
        SqlWriteCallback(SqlWriteResult result, SqlWriteCompletion completion);

        void Execute() override;
        void SetResult(std::unique_ptr<QueryResult> result) override;
        std::unique_ptr<QueryResult>& GetResult() override;

    private:
        SqlWriteResult m_writeResult;
        SqlWriteCompletion m_completion;
        bool m_delivered;
        std::unique_ptr<QueryResult> m_queryResult;
};

#endif
