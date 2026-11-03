#ifndef CHARACTER_DELETION_RELATIONSHIPS_H
#define CHARACTER_DELETION_RELATIONSHIPS_H

#include "Database/CharacterPersistenceContext.h"
#include "ObjectGuid.h"

#include <string>
#include <vector>

class Guild;
class Group;
class Petition;
class DungeonPersistentState;

/// Guild membership removal staged by a character deletion.  The SQL is part of
/// the deletion's checked batch; ApplyDeletionMemberRemoval performs the local
/// (broadcast/cache) consequences only after a confirmed commit.
struct GuildDeletionMemberPlan
{
    Guild* guild = nullptr;
    uint32 guildId = 0;
    uint32 memberGuid = 0;
    bool leaderRemoved = false;
    uint32 newLeaderGuid = 0;
    std::string oldLeaderName;
    std::string newLeaderName;
    bool becomesEmpty = false;
};

/// Group membership removal staged by a character deletion.
struct GroupDeletionMemberPlan
{
    enum class Kind
    {
        None,
        Remove,
        Succession,
        Disband
    };

    struct BindOp
    {
        uint32 mapId = 0;
        uint32 instanceId = 0;
        bool permanent = false;
        DungeonPersistentState* state = nullptr;
    };

    Kind kind = Kind::None;
    Group* group = nullptr;
    uint32 groupId = 0;
    uint32 memberGuid = 0;
    uint32 newLeaderGuid = 0;
    std::string newLeaderName;
    bool isRaid = false;
    bool isBg = false;
    bool inLfg = false;
    uint32 lfgAreaId = 0;
    std::vector<BindOp> removeGroupBinds;   // erased from the group locally
    std::vector<BindOp> addGroupBinds;      // new leader's binds copied to group
    std::vector<BindOp> resetGroupBinds;    // disband: local reset/unbind list
    BindOp transferBind;                    // disband: instance handed to a member
    bool hasTransferBind = false;
    uint32 transferPlayerGuid = 0;
};

/// Petition rows (owned and signed) and the in-memory signatures removed for a
/// deleted character.
struct PetitionDeletionPlan
{
    std::vector<Petition*> ownedPetitions;
    std::vector<Petition*> changedPetitions;
    std::vector<ObjectGuid> signatureGuids;
};

struct CharacterDeletionRelationshipPlan
{
    GuildDeletionMemberPlan guild;
    bool hasGuild = false;
    GroupDeletionMemberPlan group;
    bool hasGroup = false;
    PetitionDeletionPlan petitions;
};

/// Stages guild membership/succession/disband SQL, group membership/succession/
/// disband SQL and petition/signature SQL into the deletion's checked batch.
/// No local state is mutated here.
bool AppendCharacterDeletionRelationships(
    CharacterPersistenceContext& context, ObjectGuid playerGuid,
    CharacterDeletionRelationshipPlan& plan);

/// Local-only consequences, applied only after the batch committed.  May
/// destroy the Guild/Group objects.
void ApplyCharacterDeletionRelationships(
    CharacterDeletionRelationshipPlan& plan);

#endif
