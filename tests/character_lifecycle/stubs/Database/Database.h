#ifndef CHARACTER_PERSISTENCE_TEST_DATABASE_H
#define CHARACTER_PERSISTENCE_TEST_DATABASE_H

#include "Database/SqlCheckedBatch.h"
#include "Database/SqlPreparedStatement.h"
#include "Database/SqlWrite.h"

#include <algorithm>
#include <cstring>
#include <map>
#include <memory>
#include <string>

class Database
{
    public:
        virtual ~Database() = default;

        SqlCheckedBatch CreateCheckedBatch(uint32 serialId = 0)
        {
            return SqlCheckedBatch(this, serialId);
        }

        virtual SqlBatchResult DirectExecuteCheckedBatch(
            SqlCheckedBatch batch) = 0;

        SqlStatement CreateStatement(SqlStatementID& index, char const* fmt)
        {
            int const id = m_nextStatement++;
            int const arguments = static_cast<int>(
                std::count(fmt, fmt + std::strlen(fmt), '?'));
            index.init(id, arguments);
            m_statementSql[id] = fmt;
            return SqlStatement(index, *this);
        }

        std::string GetStmtString(int id) const
        {
            auto itr = m_statementSql.find(id);
            return itr == m_statementSql.end() ? std::string() : itr->second;
        }

    private:
        int m_nextStatement = 1;
        std::map<int, std::string> m_statementSql;
};

#endif
