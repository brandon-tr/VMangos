/*
 * Deletion-dependent relationship persistence.
 *
 * Guild membership/succession/disband, group membership/succession/disband and
 * petition/signature SQL are appended to the character deletion's checked batch
 * before any local relationship state changes.  The Apply functions then run
 * only after Execute() confirmed a complete commit and never submit SQL.
 *
 * Item/character ownership is always GUID-based; a reused full name cannot
 * redirect these relationships to a different character.
 */

#include "Database/CharacterDeletionRelationships.h"

#include "Database/DatabaseEnv.h"
#include "Database/SqlPreparedStatement.h"
#include "Guild/Guild.h"
#include "Guild/GuildMgr.h"
#include "Group/Group.h"
#include "Log.h"
#include "ObjectMgr.h"
#include "Objects/Player.h"

bool AppendCharacterDeletionRelationships(
    CharacterPersistenceContext& context, ObjectGuid playerGuid,
    CharacterDeletionRelationshipPlan& plan)
{
    Database* database = context.Batch().GetDatabase();
    if (!database || playerGuid.GetCounter() == 0 || !playerGuid.IsPlayer())
    {
        context.Invalidate();
        return false;
    }

    // The in-memory corpse conversion runs after commit with its own row
    // deletion suppressed, so this is the only corpse write of the operation.
    static SqlStatementID deleteCorpse;
    SqlStatement corpse = database->CreateStatement(
        deleteCorpse,
        "DELETE FROM `corpse` WHERE `player_guid` = ? AND `corpse_type` <> 0");
    corpse.addUInt32(playerGuid.GetCounter());
    if (!corpse.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::Any))
    {
        context.Invalidate();
        return false;
    }

    if (Guild* guild = sGuildMgr.GetPlayerGuild(playerGuid))
    {
        plan.hasGuild = true;
        if (!guild->AppendDeletionMemberRemoval(context, playerGuid,
                                               plan.guild))
            return false;
    }

    if (Group* group = sObjectMgr.GetGroupByMember(playerGuid))
    {
        plan.hasGroup = true;
        if (!group->AppendDeletionMemberRemoval(context, playerGuid,
                                                plan.group))
            return false;
    }

    return sGuildMgr.AppendDeletionPetitionCleanup(context, playerGuid,
                                                   plan.petitions);
}

void ApplyCharacterDeletionRelationships(
    CharacterDeletionRelationshipPlan& plan)
{
    if (plan.hasGuild)
    {
        Guild* guild = plan.guild.guild;
        bool const becomesEmpty = plan.guild.becomesEmpty;
        if (guild)
        {
            guild->ApplyDeletionMemberRemoval(plan.guild);
            if (becomesEmpty)
                delete guild;
        }
    }

    if (plan.hasGroup)
    {
        Group* group = plan.group.group;
        bool const disband =
            plan.group.kind == GroupDeletionMemberPlan::Kind::Disband;
        if (group)
        {
            group->ApplyDeletionMemberRemoval(plan.group);
            if (disband)
            {
                sObjectMgr.RemoveGroup(group);
                delete group;
            }
        }
    }

    sGuildMgr.ApplyDeletionPetitionCleanup(plan.petitions);
}
