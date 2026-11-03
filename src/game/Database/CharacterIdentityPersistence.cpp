#include "Database/CharacterIdentityPersistence.h"

#include "Database/Database.h"
#include "Database/SqlPreparedStatement.h"
#include "PlayerName.h"

#include <limits>

bool AppendPermanentCharacterDeletion(
    Database& database,
    CharacterPersistenceContext& context,
    CharacterDeletionSnapshot const& expected)
{
    CharacterIdentitySnapshot const& identity = expected.identity;
    bool const tombstone = expected.deletedTime != 0;
    if (&database != context.Batch().GetDatabase() ||
        context.IsInvalid() || context.WasSubmitted() ||
        !context.Batch().Empty() ||
        identity.guid == 0 || identity.accountId == 0 ||
        identity.generation == 0 ||
        identity.generation == std::numeric_limits<uint64>::max() ||
        !isCanonicalFullPlayerName(identity.canonicalName))
    {
        context.Invalidate();
        return false;
    }

    static SqlStatementID statements[30];
    size_t next = 0;
    auto append = [&](char const* sql, uint32 value,
                      SqlAffectedRowsPolicy policy) {
        SqlStatement statement = database.CreateStatement(statements[next++], sql);
        statement.addUInt32(value);
        if (!statement.AppendToCheckedBatch(context.Batch(), policy))
        {
            context.Invalidate();
            return false;
        }
        return true;
    };
    auto appendTwo = [&](char const* sql, uint32 first, uint32 second,
                         SqlAffectedRowsPolicy policy) {
        SqlStatement statement = database.CreateStatement(statements[next++], sql);
        statement.addUInt32(first);
        statement.addUInt32(second);
        if (!statement.AppendToCheckedBatch(context.Batch(), policy))
        {
            context.Invalidate();
            return false;
        }
        return true;
    };

    if (!tombstone)
    {
        static SqlStatementID guardActive;
        SqlStatement guard = database.CreateStatement(
            guardActive,
            "UPDATE `characters` SET `identity_generation`=? "
            "WHERE `guid`=? AND `account`=? AND BINARY `name`=BINARY ? "
            "AND `identity_generation`=? AND `deleted_time` IS NULL "
            "AND EXISTS (SELECT 1 FROM `character_name_ownership` "
            "WHERE `guid`=? AND BINARY `canonical_name`=BINARY ?) "
            "AND NOT EXISTS (SELECT 1 FROM `mail` WHERE `receiver_guid`=? "
            "AND `has_items`<>0 AND `cod`<>0)");
        guard.addUInt64(identity.generation + 1);
        guard.addUInt32(identity.guid);
        guard.addUInt32(identity.accountId);
        guard.addString(identity.canonicalName);
        guard.addUInt64(identity.generation);
        guard.addUInt32(identity.guid);
        guard.addString(identity.canonicalName);
        guard.addUInt32(identity.guid);
        if (!guard.AppendToCheckedBatch(
                context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
        {
            context.Invalidate();
            return false;
        }

        static SqlStatementID releaseOwnership;
        SqlStatement release = database.CreateStatement(
            releaseOwnership,
            "DELETE FROM `character_name_ownership` "
            "WHERE `guid`=? AND BINARY `canonical_name`=BINARY ?");
        release.addUInt32(identity.guid);
        release.addString(identity.canonicalName);
        if (!release.AppendToCheckedBatch(
                context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
        {
            context.Invalidate();
            return false;
        }
    }
    else
    {
        static SqlStatementID guardTombstone;
        SqlStatement guard = database.CreateStatement(
            guardTombstone,
            "UPDATE `characters` SET `identity_generation`=? "
            "WHERE `guid`=? AND `identity_generation`=? AND `deleted_time`=? "
            "AND `deleted_account`=? AND BINARY `deleted_name`=BINARY ? "
            "AND `account`=0 AND `name`='' "
            "AND NOT EXISTS (SELECT 1 FROM `character_name_ownership` "
            "WHERE `guid`=?) "
            "AND NOT EXISTS (SELECT 1 FROM `mail` WHERE `receiver_guid`=? "
            "AND `has_items`<>0 AND `cod`<>0)");
        guard.addUInt64(identity.generation + 1);
        guard.addUInt32(identity.guid);
        guard.addUInt64(identity.generation);
        guard.addUInt64(expected.deletedTime);
        guard.addUInt32(identity.accountId);
        guard.addString(identity.canonicalName);
        guard.addUInt32(identity.guid);
        guard.addUInt32(identity.guid);
        if (!guard.AppendToCheckedBatch(
                context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
        {
            context.Invalidate();
            return false;
        }
    }

    if (!append("DELETE FROM `character_action` WHERE `guid` = ?",
                identity.guid, SqlAffectedRowsPolicy::Any) ||
        !append("DELETE FROM `character_aura` WHERE `guid` = ?",
                identity.guid, SqlAffectedRowsPolicy::Any) ||
        !append("DELETE FROM `character_battleground_data` WHERE `guid` = ?",
                identity.guid, SqlAffectedRowsPolicy::Any) ||
        !append("DELETE FROM `character_deleted_items` WHERE `player_guid` = ?",
                identity.guid, SqlAffectedRowsPolicy::Any) ||
        !append("DELETE FROM `character_gifts` WHERE `guid` = ?",
                identity.guid, SqlAffectedRowsPolicy::Any) ||
        !append("DELETE FROM `character_homebind` WHERE `guid` = ?",
                identity.guid, SqlAffectedRowsPolicy::Any) ||
        !append("DELETE FROM `character_instance` WHERE `guid` = ?",
                identity.guid, SqlAffectedRowsPolicy::Any) ||
        !append("DELETE FROM `group_instance` WHERE `leader_guid` = ?",
                identity.guid, SqlAffectedRowsPolicy::Any) ||
        !append("DELETE FROM `character_inventory` WHERE `guid` = ?",
                identity.guid, SqlAffectedRowsPolicy::Any) ||
        !append("DELETE FROM `character_queststatus` WHERE `guid` = ?",
                identity.guid, SqlAffectedRowsPolicy::Any) ||
        !append("DELETE FROM `character_reputation` WHERE `guid` = ?",
                identity.guid, SqlAffectedRowsPolicy::Any) ||
        !append("DELETE FROM `character_skills` WHERE `guid` = ?",
                identity.guid, SqlAffectedRowsPolicy::Any) ||
        !append("DELETE FROM `character_forgotten_skills` WHERE `guid` = ?",
                identity.guid, SqlAffectedRowsPolicy::Any) ||
        !append("DELETE FROM `character_spell` WHERE `guid` = ?",
                identity.guid, SqlAffectedRowsPolicy::Any) ||
        !append("DELETE FROM `character_spell_cooldown` WHERE `guid` = ?",
                identity.guid, SqlAffectedRowsPolicy::Any) ||
        !append("DELETE FROM `item_instance` WHERE `owner_guid` = ?",
                identity.guid, SqlAffectedRowsPolicy::Any) ||
        !appendTwo("DELETE FROM `character_social` WHERE `guid` = ? OR `friend` = ?",
                   identity.guid, identity.guid, SqlAffectedRowsPolicy::Any) ||
        !append("DELETE FROM `mail` WHERE `receiver_guid` = ?",
                identity.guid, SqlAffectedRowsPolicy::Any) ||
        !append("DELETE FROM `mail_items` WHERE `receiver_guid` = ?",
                identity.guid, SqlAffectedRowsPolicy::Any) ||
        !append("DELETE FROM `pet_aura` WHERE `guid` IN (SELECT `id` FROM `character_pet` WHERE `owner_guid` = ?)",
                identity.guid, SqlAffectedRowsPolicy::Any) ||
        !append("DELETE FROM `pet_spell` WHERE `guid` IN (SELECT `id` FROM `character_pet` WHERE `owner_guid` = ?)",
                identity.guid, SqlAffectedRowsPolicy::Any) ||
        !append("DELETE FROM `pet_spell_cooldown` WHERE `guid` IN (SELECT `id` FROM `character_pet` WHERE `owner_guid` = ?)",
                identity.guid, SqlAffectedRowsPolicy::Any) ||
        !append("DELETE FROM `character_pet` WHERE `owner_guid` = ?",
                identity.guid, SqlAffectedRowsPolicy::Any) ||
        !appendTwo("DELETE FROM `guild_eventlog` WHERE `player_guid1` = ? OR `player_guid2` = ?",
                   identity.guid, identity.guid, SqlAffectedRowsPolicy::Any))
        return false;

    static SqlStatementID deleteActive;
    static SqlStatementID deleteTombstone;
    SqlStatement finalDelete = tombstone
        ? database.CreateStatement(
              deleteTombstone,
              "DELETE FROM `characters` WHERE `guid`=? "
              "AND `identity_generation`=? AND `deleted_time`=? "
              "AND `deleted_account`=? AND BINARY `deleted_name`=BINARY ? "
              "AND `account`=0 AND `name`=''")
        : database.CreateStatement(
              deleteActive,
              "DELETE FROM `characters` WHERE `guid`=? AND `account`=? "
              "AND BINARY `name`=BINARY ? AND `identity_generation`=? "
              "AND `deleted_time` IS NULL");
    finalDelete.addUInt32(identity.guid);
    if (tombstone)
    {
        finalDelete.addUInt64(identity.generation + 1);
        finalDelete.addUInt64(expected.deletedTime);
        finalDelete.addUInt32(identity.accountId);
        finalDelete.addString(identity.canonicalName);
    }
    else
    {
        finalDelete.addUInt32(identity.accountId);
        finalDelete.addString(identity.canonicalName);
        finalDelete.addUInt64(identity.generation + 1);
    }
    if (!finalDelete.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
    {
        context.Invalidate();
        return false;
    }
    return true;
}
CharacterNameWriteStatus ClassifyCharacterNameClaim(
    SqlBatchResult const& result, std::size_t claimStatementIndex)
{
    if (result.outcome == SqlWriteOutcome::OutcomeUnknown)
        return CharacterNameWriteStatus::OutcomeUnknown;
    if (result.rollbackAttempted &&
        result.rollbackResult.outcome != SqlWriteOutcome::Succeeded)
        return CharacterNameWriteStatus::OutcomeUnknown;
    if (result.outcome == SqlWriteOutcome::Succeeded &&
        result.stage == SqlBatchStage::Complete)
        return CharacterNameWriteStatus::Committed;
    if (result.outcome == SqlWriteOutcome::DuplicateKey &&
        result.stage == SqlBatchStage::Statement &&
        claimStatementIndex != std::numeric_limits<std::size_t>::max() &&
        result.statementIndex == claimStatementIndex &&
        result.cause.outcome == SqlWriteOutcome::DuplicateKey &&
        result.rollbackAttempted &&
        result.rollbackResult.outcome == SqlWriteOutcome::Succeeded)
        return CharacterNameWriteStatus::NameInUse;
    return CharacterNameWriteStatus::Rejected;
}

bool AppendSoftCharacterDeletion(
    Database& database,
    CharacterPersistenceContext& context,
    SoftCharacterDeletion const& request)
{
    if (&database != context.Batch().GetDatabase() ||
        context.IsInvalid() || context.WasSubmitted() ||
        !context.Batch().Empty() || request.deletionTime == 0)
    {
        context.Invalidate();
        return false;
    }
    if (!context.AppendIdentityGuards({request.identity}))
        return false;

    static SqlStatementID releaseOwnership;
    SqlStatement release = database.CreateStatement(
        releaseOwnership,
        "DELETE FROM `character_name_ownership` "
        "WHERE `guid`=? AND BINARY `canonical_name`=BINARY ?");
    release.addUInt32(request.identity.guid);
    release.addString(request.identity.canonicalName);
    if (!release.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
    {
        context.Invalidate();
        return false;
    }

    static SqlStatementID tombstoneCharacter;
    SqlStatement tombstone = database.CreateStatement(
        tombstoneCharacter,
        "UPDATE `characters` SET "
        "`deleted_name`=`name`, `deleted_account`=`account`, "
        "`deleted_time`=?, `name`='', `account`=0 "
        "WHERE `guid`=? AND `account`=? AND BINARY `name`=BINARY ? "
        "AND `identity_generation`=? AND `deleted_time` IS NULL");
    tombstone.addUInt64(request.deletionTime);
    tombstone.addUInt32(request.identity.guid);
    tombstone.addUInt32(request.identity.accountId);
    tombstone.addString(request.identity.canonicalName);
    tombstone.addUInt64(request.identity.generation + 1);
    if (!tombstone.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
    {
        context.Invalidate();
        return false;
    }
    return true;
}

bool AppendCharacterRestoration(
    Database& database,
    CharacterPersistenceContext& context,
    CharacterRestoration const& request)
{
    if (&database != context.Batch().GetDatabase() ||
        context.IsInvalid() || context.WasSubmitted() ||
        !context.Batch().Empty() || request.guid == 0 ||
        request.deletedAccountId == 0 || request.targetAccountId == 0 ||
        request.deletedTime == 0 || request.expectedGeneration == 0 ||
        request.expectedGeneration == std::numeric_limits<uint64>::max() ||
        !isCanonicalFullPlayerName(request.deletedName) ||
        !isCanonicalFullPlayerName(request.targetCanonicalName))
    {
        context.Invalidate();
        return false;
    }

    static SqlStatementID restoreCharacter;
    SqlStatement restore = database.CreateStatement(
        restoreCharacter,
        "UPDATE `characters` SET `name`=?, `account`=?, "
        "`deleted_name`='', `deleted_account`=0, `deleted_time`=NULL, "
        "`identity_generation`=`identity_generation`+1 "
        "WHERE `guid`=? AND `identity_generation`=? "
        "AND `identity_generation` < 18446744073709551615 "
        "AND `deleted_time`=? AND `deleted_account`=? "
        "AND BINARY `deleted_name`=BINARY ? AND `account`=0 "
        "AND BINARY `name`=BINARY '' "
        "AND NOT EXISTS (SELECT 1 FROM `character_name_ownership` "
        "WHERE `guid`=?)");
    restore.addString(request.targetCanonicalName);
    restore.addUInt32(request.targetAccountId);
    restore.addUInt32(request.guid);
    restore.addUInt64(request.expectedGeneration);
    restore.addUInt64(request.deletedTime);
    restore.addUInt32(request.deletedAccountId);
    restore.addString(request.deletedName);
    restore.addUInt32(request.guid);
    if (!restore.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
    {
        context.Invalidate();
        return false;
    }

    static SqlStatementID insertRestoredOwnership;
    SqlStatement ownership = database.CreateStatement(
        insertRestoredOwnership,
        "INSERT INTO `character_name_ownership` (`canonical_name`, `guid`) "
        "VALUES (?, ?)");
    ownership.addString(request.targetCanonicalName);
    ownership.addUInt32(request.guid);
    if (!ownership.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
    {
        context.Invalidate();
        return false;
    }
    return true;
}

bool AppendMailMoneyWithdrawal(
    Database& database,
    CharacterPersistenceContext& context,
    MailMoneyWithdrawal const& request,
    uint32& projectedBalance)
{
    if (&database != context.Batch().GetDatabase() ||
        context.IsInvalid() || context.WasSubmitted() ||
        !context.Batch().Empty() || request.mailId == 0 ||
        request.amount == 0 ||
        request.currentBalance > request.maximumBalance)
    {
        context.Invalidate();
        return false;
    }

    uint64 const sum = static_cast<uint64>(request.currentBalance) +
                       static_cast<uint64>(request.amount);
    if (sum > request.maximumBalance ||
        sum > std::numeric_limits<uint32>::max())
    {
        context.Invalidate();
        return false;
    }

    if (!context.AppendIdentityGuards({request.identity}))
        return false;

    static SqlStatementID claimMailMoney;
    SqlStatement claim = database.CreateStatement(
        claimMailMoney,
        "UPDATE `mail` SET `money`=0 "
        "WHERE `id`=? AND `receiver_guid`=? AND `money`=?");
    claim.addUInt32(request.mailId);
    claim.addUInt32(request.identity.guid);
    claim.addUInt32(request.amount);
    if (!claim.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
    {
        context.Invalidate();
        return false;
    }

    static SqlStatementID updateBalance;
    SqlStatement balance = database.CreateStatement(
        updateBalance,
        "UPDATE `characters` SET `money`=? "
        "WHERE `guid`=? AND `account`=? AND BINARY `name`=BINARY ? "
        "AND `identity_generation`=? AND `deleted_time` IS NULL");
    balance.addUInt32(static_cast<uint32>(sum));
    balance.addUInt32(request.identity.guid);
    balance.addUInt32(request.identity.accountId);
    balance.addString(request.identity.canonicalName);
    balance.addUInt64(request.identity.generation + 1);
    if (!balance.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::Any))
    {
        context.Invalidate();
        return false;
    }

    projectedBalance = static_cast<uint32>(sum);
    return true;
}
