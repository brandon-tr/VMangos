#ifndef PLAYER_IDENTITY_CACHE_TEST_DATABASE_H
#define PLAYER_IDENTITY_CACHE_TEST_DATABASE_H

#include "Database/QueryResult.h"

#include <memory>
#include <string>

class Database
{
    public:
        virtual ~Database() = default;
        virtual std::unique_ptr<QueryResult> Query(
            std::string const& sql) = 0;
};

#endif
