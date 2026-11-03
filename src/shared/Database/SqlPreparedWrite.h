#ifndef SQLPREPAREDWRITE_H
#define SQLPREPAREDWRITE_H

#include "Database/SqlPreparedStatement.h"
#include "Database/SqlWrite.h"

#include <mysql.h>

#include <string>
#include <vector>

/// Execute one trusted prepared DML statement with typed bound values.
/// The native statement is owned per execution; no prepared cache is used.
SqlWriteResult ExecuteMySQLPreparedWrite(
    st_mysql* connection,
    std::string const& sql,
    std::vector<SqlStmtFieldData> const& parameters);

/// Shared mapping for legacy prepared binds and checked prepared writes.
void BindMySQLWriteParameter(
    MYSQL_BIND& binding,
    SqlStmtFieldData const& value);

#endif
