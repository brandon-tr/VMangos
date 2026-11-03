#include "Database/SqlCheckedBatch.h"

#include <limits>
#include <utility>

namespace
{
bool ValidSql(std::string const& sql)
{
    return !sql.empty() && sql.find('\0') == std::string::npos &&
           sql.size() <= std::numeric_limits<unsigned long>::max();
}

bool ValidParameters(std::vector<SqlStmtFieldData> const& parameters)
{
    for (SqlStmtFieldData const& parameter : parameters)
    {
        if (parameter.type() == FIELD_STRING &&
            parameter.size() > std::numeric_limits<unsigned long>::max())
            return false;
    }
    return true;
}

bool ValidPolicy(SqlAffectedRowsPolicy policy)
{
    return policy == SqlAffectedRowsPolicy::Any ||
           policy == SqlAffectedRowsPolicy::ExactlyOne;
}
}

SqlCheckedBatch::SqlCheckedBatch(Database* database, uint32 serialId)
    : m_database(database), m_serialId(serialId)
{
}

SqlCheckedBatch::SqlCheckedBatch(SqlCheckedBatch&& other) noexcept
    : m_database(other.m_database),
      m_serialId(other.m_serialId),
      m_writes(std::move(other.m_writes))
{
    other.m_database = nullptr;
    other.m_serialId = 0;
    other.m_writes.clear();
}

SqlCheckedBatch& SqlCheckedBatch::operator=(SqlCheckedBatch&& other) noexcept
{
    if (this != &other)
    {
        m_database = other.m_database;
        m_serialId = other.m_serialId;
        m_writes = std::move(other.m_writes);
        other.m_database = nullptr;
        other.m_serialId = 0;
        other.m_writes.clear();
    }
    return *this;
}

bool SqlCheckedBatch::IsValid() const
{
    if (!m_database || m_writes.empty())
        return false;

    for (SqlBoundWrite const& write : m_writes)
    {
        if (!ValidPolicy(write.affectedRowsPolicy) ||
            !ValidSql(write.sql) || !ValidParameters(write.parameters))
            return false;
    }
    return true;
}

bool SqlCheckedBatch::Append(std::string sql,
                             std::vector<SqlStmtFieldData> parameters,
                             SqlAffectedRowsPolicy policy)
{
    if (!m_database || !ValidPolicy(policy) || !ValidSql(sql) ||
        !ValidParameters(parameters))
        return false;

    SqlBoundWrite write;
    write.sql = std::move(sql);
    write.parameters = std::move(parameters);
    write.affectedRowsPolicy = policy;
    m_writes.push_back(std::move(write));
    return true;
}

std::unique_ptr<MaNGOS::IQueryCallback> MakeSqlBatchCallback(
    SqlBatchResult result,
    SqlBatchCompletion completion)
{
    SqlWriteResult const ignored = {SqlWriteOutcome::NotExecuted, 0, 0};
    return std::unique_ptr<MaNGOS::IQueryCallback>(
        new SqlWriteCallback(
            ignored,
            [result = std::move(result),
             completion = std::move(completion)](SqlWriteResult) mutable {
                if (completion)
                    completion(result);
            }));
}
