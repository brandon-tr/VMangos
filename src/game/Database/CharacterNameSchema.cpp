#include "Database/CharacterNameSchema.h"
#include "Database/CharacterNameSchemaChecks.h"
#include "Database/Database.h"
#include "Database/Field.h"
#include "Database/QueryResult.h"

#include <cstdint>
#include <limits>
#include <memory>

bool CheckCharacterNameSchema(Database& database, std::string& reason)
{
    auto countQuery = [&database](std::string const& sql,
                                  std::uint64_t& count) -> bool
    {
        std::unique_ptr<QueryResult> result = database.Query(sql);
        if (!result || result->GetRowCount() != 1 ||
            result->GetFieldCount() != 1)
            return false;

        Field* field = result->Fetch();
        if (!field)
            return false;

        count = field->GetUInt64();
        return true;
    };

    return CheckCharacterNameSchemaCounts(countQuery, reason);
}

bool CheckCharacterNameOwnership(Database& database, std::string& reason)
{
    auto countQuery = [&database](std::string const& sql,
                                  std::uint64_t& count) -> bool
    {
        std::unique_ptr<QueryResult> result = database.Query(sql);
        if (!result || result->GetRowCount() != 1 ||
            result->GetFieldCount() != 1)
            return false;

        Field* field = result->Fetch();
        if (!field || field->IsNULL())
            return false;

        char const* text = field->GetString();
        if (!text || !*text)
            return false;

        std::uint64_t parsed = 0;
        for (char const* current = text; *current; ++current)
        {
            unsigned char const byte = static_cast<unsigned char>(*current);
            if (byte < '0' || byte > '9')
                return false;
            std::uint64_t const digit = byte - '0';
            if (parsed > (std::numeric_limits<std::uint64_t>::max() - digit) / 10)
                return false;
            parsed = parsed * 10 + digit;
        }
        count = parsed;
        return true;
    };

    return CheckCharacterNameOwnershipCounts(countQuery, reason);
}
