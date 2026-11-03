#ifndef SQLCHECKEDBATCH_H
#define SQLCHECKEDBATCH_H

#include "Database/SqlPreparedStatement.h"
#include "Database/SqlWrite.h"

#include <cstddef>
#include <functional>
#include <memory>
#include <string>
#include <vector>

class Database;
class SqlCheckedBatchOperation;
class SqlStatement;
class SqlResultQueue;

enum class SqlAffectedRowsPolicy
{
    Any,
    ExactlyOne
};

struct SqlBoundWrite
{
    std::string sql;
    std::vector<SqlStmtFieldData> parameters;
    SqlAffectedRowsPolicy affectedRowsPolicy;
};

enum class SqlBatchStage
{
    NotStarted,
    Begin,
    Statement,
    RowCount,
    Commit,
    Complete
};

struct SqlBatchResult
{
    SqlWriteOutcome outcome;
    SqlBatchStage stage;
    std::size_t statementIndex;
    SqlWriteResult cause;
    std::vector<SqlWriteResult> statementResults;
    bool rollbackAttempted;
    SqlWriteResult rollbackResult;
};

using SqlBatchCompletion = std::function<void(SqlBatchResult)>;

class SqlCheckedBatch
{
    public:
        SqlCheckedBatch(SqlCheckedBatch const&) = delete;
        SqlCheckedBatch& operator=(SqlCheckedBatch const&) = delete;

        SqlCheckedBatch(SqlCheckedBatch&& other) noexcept;
        SqlCheckedBatch& operator=(SqlCheckedBatch&& other) noexcept;

        bool IsValid() const;
        bool Empty() const { return m_writes.empty(); }
        uint32 GetSerialId() const { return m_serialId; }
        Database* GetDatabase() const { return m_database; }
        std::vector<SqlBoundWrite> const& Writes() const { return m_writes; }

    private:
        friend class Database;
        friend class SqlStatement;
        friend struct SqlCheckedBatchTestAccess;

        SqlCheckedBatch(Database* database, uint32 serialId);

        bool Append(std::string sql, std::vector<SqlStmtFieldData> parameters,
                    SqlAffectedRowsPolicy policy);

        Database* m_database;
        uint32 m_serialId;
        std::vector<SqlBoundWrite> m_writes;
};

/// Adapter that preserves the complete typed batch result through the existing
/// unsafe SqlWriteCallback delivery/cancellation path.
std::unique_ptr<MaNGOS::IQueryCallback> MakeSqlBatchCallback(
    SqlBatchResult result,
    SqlBatchCompletion completion);

#endif
