#ifndef CHARACTER_NAME_SCHEMA_CHECKS_H
#define CHARACTER_NAME_SCHEMA_CHECKS_H

#include <cstdint>
#include <string>

/// Validates the character-name storage contract through metadata COUNT queries.
/// `query` returns false when the query or its result shape failed; that is never
/// treated as a zero count. The caller owns the connection/query implementation.
template<class CountQuery>
bool CheckCharacterNameSchemaCounts(CountQuery&& query, std::string& reason)
{
    reason.clear();

    static std::string const tableNames =
        "'auction','characters','characters_guid_delete','characters_item_delete',"
        "'character_action','character_aura','character_battleground_data',"
        "'character_deleted_items','account_data',"
        "'character_forgotten_skills','character_gifts','character_homebind',"
        "'character_honor_cp','character_instance','character_inventory',"
        "'character_pet','character_queststatus','character_reputation',"
        "'character_skills','character_social','character_spell',"
        "'character_spell_cooldown','character_stats','character_tutorial',"
        "'corpse','creature_respawn','gameobject_respawn','game_event_status',"
        "'gm_subsurveys','gm_surveys','gm_tickets','groups','group_instance',"
        "'group_member','guild','guild_eventlog','guild_member','guild_rank',"
        "'instance','instance_reset','item_instance','item_loot','item_text',"
        "'mail','mail_items','migrations','petition','petition_sign','pet_aura',"
        "'pet_spell','pet_spell_cooldown','playerbot','saved_variables','world',"
        "'character_account_data','character_name_ownership'";

    std::string const tables =
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE' "
        "AND TABLE_NAME IN (" + tableNames + ")";

    struct Check
    {
        char const* reason;
        std::string sql;
        std::uint64_t expected;
    };

    Check const checks[] =
    {
        {
            "required character-name schema tables are missing",
            tables,
            56
        },
        {
            "required character-name schema tables are not InnoDB",
            tables + " AND ENGINE='InnoDB'",
            56
        },
        {
            "characters.identity_generation has an incompatible definition",
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='characters' "
            "AND COLUMN_NAME='identity_generation' AND DATA_TYPE='bigint' "
            "AND COLUMN_TYPE LIKE '%unsigned%' AND IS_NULLABLE='NO' "
            "AND COLUMN_DEFAULT='1'",
            1
        },
        {
            "character naming columns are missing or not varchar(25)",
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND DATA_TYPE='varchar' "
            "AND CHARACTER_MAXIMUM_LENGTH=25 AND "
            "((TABLE_NAME='characters' AND COLUMN_NAME IN ('name','deleted_name')) "
            "OR (TABLE_NAME='gm_tickets' AND COLUMN_NAME='name'))",
            3
        },
        {
            "character_name_ownership columns have incompatible definitions",
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() "
            "AND TABLE_NAME='character_name_ownership' AND "
            "((COLUMN_NAME='canonical_name' AND DATA_TYPE='varbinary' "
            "AND CHARACTER_MAXIMUM_LENGTH=75 AND IS_NULLABLE='NO') "
            "OR (COLUMN_NAME='guid' AND DATA_TYPE='int' "
            "AND COLUMN_TYPE LIKE '%unsigned%' AND IS_NULLABLE='NO')) "
            "AND (SELECT COUNT(*) FROM information_schema.COLUMNS c2 "
            "WHERE c2.TABLE_SCHEMA=DATABASE() "
            "AND c2.TABLE_NAME='character_name_ownership')=2",
            2
        },
        {
            "character_name_ownership primary key is not canonical_name",
            "SELECT COUNT(*) FROM information_schema.STATISTICS s "
            "WHERE s.TABLE_SCHEMA=DATABASE() "
            "AND s.TABLE_NAME='character_name_ownership' "
            "AND s.INDEX_NAME='PRIMARY' AND s.COLUMN_NAME='canonical_name' "
            "AND s.SEQ_IN_INDEX=1 AND s.NON_UNIQUE=0 AND s.SUB_PART IS NULL "
            "AND (SELECT COUNT(*) FROM information_schema.STATISTICS s2 "
            "WHERE s2.TABLE_SCHEMA=DATABASE() "
            "AND s2.TABLE_NAME='character_name_ownership' "
            "AND s2.INDEX_NAME='PRIMARY')=1",
            1
        },
        {
            "character_name_ownership guid unique key is invalid",
            "SELECT COUNT(*) FROM information_schema.STATISTICS s "
            "WHERE s.TABLE_SCHEMA=DATABASE() "
            "AND s.TABLE_NAME='character_name_ownership' "
            "AND s.INDEX_NAME='uq_character_name_guid' "
            "AND s.COLUMN_NAME='guid' AND s.SEQ_IN_INDEX=1 "
            "AND s.NON_UNIQUE=0 AND s.SUB_PART IS NULL "
            "AND (SELECT COUNT(*) FROM information_schema.STATISTICS s2 "
            "WHERE s2.TABLE_SCHEMA=DATABASE() "
            "AND s2.TABLE_NAME='character_name_ownership' "
            "AND s2.INDEX_NAME='uq_character_name_guid')=1",
            1
        },
        {
            "character_name_ownership foreign key target is invalid",
            "SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE k "
            "WHERE k.TABLE_SCHEMA=DATABASE() "
            "AND k.TABLE_NAME='character_name_ownership' "
            "AND k.CONSTRAINT_NAME='fk_character_name_owner' "
            "AND k.COLUMN_NAME='guid' "
            "AND k.REFERENCED_TABLE_SCHEMA=DATABASE() "
            "AND k.REFERENCED_TABLE_NAME='characters' "
            "AND k.REFERENCED_COLUMN_NAME='guid' "
            "AND (SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE k2 "
            "WHERE k2.TABLE_SCHEMA=DATABASE() "
            "AND k2.TABLE_NAME='character_name_ownership' "
            "AND k2.CONSTRAINT_NAME='fk_character_name_owner')=1",
            1
        },
        {
            "character_name_ownership foreign key rules are invalid",
            "SELECT COUNT(*) FROM information_schema.REFERENTIAL_CONSTRAINTS "
            "WHERE CONSTRAINT_SCHEMA=DATABASE() "
            "AND TABLE_NAME='character_name_ownership' "
            "AND CONSTRAINT_NAME='fk_character_name_owner' "
            "AND UPDATE_RULE='RESTRICT' AND DELETE_RULE='RESTRICT'",
            1
        },
    };

    for (Check const& check : checks)
    {
        std::uint64_t count = 0;
        if (!query(check.sql, count))
        {
            reason = check.reason;
            return false;
        }
        if (count != check.expected)
        {
            reason = check.reason;
            return false;
        }
    }

    return true;
}

/// Validates persisted active-name ownership against the authoritative
/// characters rows. Every query is an aggregate scoped to DATABASE(); a false
/// callback or any nonzero count fails closed. Canonical spelling itself is
/// enforced by the cache loader that runs after this audit.
template<class CountQuery>
bool CheckCharacterNameOwnershipCounts(CountQuery&& query, std::string& reason)
{
    reason.clear();

    struct Check
    {
        char const* reason;
        char const* sql;
    };

    Check const checks[] =
    {
        {
            "an active character has no matching canonical name claim",
            "SELECT COUNT(*) FROM `characters` c "
            "LEFT JOIN `character_name_ownership` o "
            "ON o.guid=c.guid AND BINARY o.canonical_name=BINARY c.name "
            "WHERE c.deleted_time IS NULL AND c.account<>0 AND o.guid IS NULL"
        },
        {
            "a name claim does not exactly match an active character",
            "SELECT COUNT(*) FROM `character_name_ownership` o "
            "LEFT JOIN `characters` c ON c.guid=o.guid "
            "WHERE c.guid IS NULL OR c.deleted_time IS NOT NULL "
            "OR c.account=0 OR BINARY c.name<>BINARY o.canonical_name"
        },
        {
            "an active character has an invalid identity generation",
            "SELECT COUNT(*) FROM `characters` "
            "WHERE deleted_time IS NULL AND account<>0 "
            "AND (identity_generation IS NULL OR identity_generation=0)"
        },
        {
            "an active character name is empty or malformed",
            "SELECT COUNT(*) FROM `characters` "
            "WHERE deleted_time IS NULL AND account<>0 "
            "AND (name IS NULL OR name='' OR name LIKE '% % %')"
        },
    };

    for (Check const& check : checks)
    {
        std::uint64_t count = 0;
        if (!query(check.sql, count))
        {
            reason = check.reason;
            return false;
        }
        if (count != 0)
        {
            reason = check.reason;
            return false;
        }
    }

    return true;
}

#endif
