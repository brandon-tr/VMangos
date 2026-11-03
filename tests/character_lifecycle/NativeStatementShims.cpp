#include "Database/Database.h"
#include "Database/SqlCheckedBatch.h"
#include "Database/SqlPreparedStatement.h"

SqlStmtParameters::SqlStmtParameters(int nParams)
{
    m_params.reserve(nParams);
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

bool SqlStatement::AppendToCheckedBatch(SqlCheckedBatch& batch)
{
    return AppendToCheckedBatch(batch, SqlAffectedRowsPolicy::Any);
}
