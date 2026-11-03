#ifndef SQLTRANSACTIONEXECUTION_H
#define SQLTRANSACTIONEXECUTION_H

/// The transaction sequence shared by production SqlTransaction::Execute and
/// offline fake-connection tests.  It deliberately owns no operations.
template<class Connection, class Operations>
bool ExecuteSqlTransaction(Connection& connection, Operations const& operations)
{
    if (operations.empty())
        return true;

    if (!connection.BeginTransaction())
        return false;

    for (typename Operations::const_iterator itr = operations.begin();
         itr != operations.end(); ++itr)
    {
        if (!(*itr)->Execute(&connection))
        {
            connection.RollbackTransaction();
            return false;
        }
    }

    return connection.CommitTransaction();
}

#endif
