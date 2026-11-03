/*
 * Checked ordinary-save persistence for Player and MasterPlayer.
 *
 * One composed save is a single CharacterPersistenceContext unit:
 *   identity guards -> characters row -> inventory/gold -> state appenders ->
 *   master actions/mails -> identity-generation publication.
 *
 * Nothing is acknowledged into gameplay state from assembly.  Item state,
 * dirty flags, caches and the identity generation advance only from OnCommit
 * callbacks, which Execute() runs after a confirmed complete commit.  A known
 * failure leaves every dirty flag untouched; an unknown outcome blocks the
 * identity through the existing recovery registry and is never retried here.
 */

#include "Database/CharacterPersistenceContext.h"
#include "Database/CharacterPersistenceRecovery.h"
#include "Database/DatabaseEnv.h"
#include "Database/SqlPreparedStatement.h"
#include "Anticheat/Anticheat.h"
#include "Objects/Bag.h"
#include "Objects/Item.h"
#include "Objects/Pet.h"
#include "Objects/Player.h"
#include "Chat/MasterPlayer.h"
#include "Geometry.h"
#include "HonorMgr.h"
#include "ObjectMgr.h"
#include "ReputationMgr.h"
#include "World.h"
#include "WorldSession.h"

#include <algorithm>
#include <limits>

namespace
{
CharacterIdentitySnapshot SaveIdentity(Player const& player)
{
    CharacterIdentitySnapshot identity;
    identity.guid = player.GetGUIDLow();
    identity.accountId = player.GetSession() ? player.GetSession()->GetAccountId() : 0;
    identity.canonicalName = player.GetName();
    identity.generation = player.GetIdentityGeneration();
    return identity;
}

bool SaveIdentityMatches(Player const& player,
                         CharacterIdentitySnapshot const& expected)
{
    return player.GetGUIDLow() == expected.guid &&
           player.GetSession() &&
           player.GetSession()->GetAccountId() == expected.accountId &&
           player.GetName() == expected.canonicalName &&
           player.GetIdentityGeneration() == expected.generation;
}

bool AppendUnguardedStatement(Database& database,
                              CharacterPersistenceContext& context,
                              SqlStatementID& id, char const* sql,
                              SqlAffectedRowsPolicy policy)
{
    SqlStatement statement = database.CreateStatement(id, sql);
    if (!statement.AppendToCheckedBatch(context.Batch(), policy))
    {
        context.Invalidate();
        return false;
    }
    return true;
}
}

bool Player::AppendCharacterRowToDB(CharacterPersistenceContext& context,
                                    bool online)
{
    CharacterIdentitySnapshot const identity = SaveIdentity(*this);
    if (!GetSession() || !context.HasIdentityGuard(identity) ||
        identity.generation == 0 ||
        identity.generation == std::numeric_limits<uint64>::max())
    {
        context.Invalidate();
        return false;
    }

    Database* database = context.Batch().GetDatabase();
    if (!database)
    {
        context.Invalidate();
        return false;
    }

    static SqlStatementID insChar;

    SqlStatement uberInsert = database->CreateStatement(insChar, "UPDATE `characters` SET `race` = ?, `class` = ?, `gender` = ?, `level` = ?, `xp` = ?, `money` = ?, `skin` = ?, `face` = ?, `hair_style` = ?, `hair_color` = ?, `facial_hair` = ?, `bank_bag_slots` = ?, `character_flags` = ?,"
                              "`map` = ?, `instance` = ?, `position_x` = ?, `position_y` = ?, `position_z` = ?, `orientation` = ?, "
                              "`transport_guid` = ?, `transport_x` = ?, `transport_y` = ?, `transport_z` = ?, `transport_o` = ?, "
                              "`known_taxi_mask` = ?, `current_taxi_path` = ?, `online` = ?, `played_time_total` = ?, `played_time_level` = ?, "
                              "`rest_bonus` = ?, `logout_time` = ?, `reset_talents_multiplier` = ?, `reset_talents_time` = ?, "
                              "`extra_flags` = ?, `stable_slots` = ?, `zone` = ?, `death_expire_time` = ?,"
                              "`honor_rank_points` = ?, `honor_highest_rank` = ?, `honor_standing` = ?, `honor_last_week_hk` = ?, `honor_last_week_cp` = ?, `honor_stored_hk` = ?, `honor_stored_dk` = ?, "
                              "`watched_faction` = ?, `drunk` = ?, `health` = ?, `power1` = ?, `power2` = ?, `power3` = ?, `power4` = ?, `power5` = ?, "
                              "`explored_zones` = ?, `equipment_cache` = ?, `ammo_id` = ?, `action_bars` = ?, `world_phase_mask` = ?, `create_time` = ? "
                              "WHERE `guid` = ? AND `account` = ? AND BINARY `name` = BINARY ? AND `deleted_time` IS NULL "
                              "AND `identity_generation` = ?");

    uberInsert.addUInt8(GetRace());
    uberInsert.addUInt8(GetClass());
    uberInsert.addUInt8(GetGender());
    uberInsert.addUInt32(GetLevel());
    uberInsert.addUInt32(GetUInt32Value(PLAYER_XP));
    uberInsert.addUInt32(GetMoney());
    uberInsert.addUInt8(GetByteValue(PLAYER_BYTES, PLAYER_BYTES_OFFSET_SKIN_ID));
    uberInsert.addUInt8(GetByteValue(PLAYER_BYTES, PLAYER_BYTES_OFFSET_FACE_ID));
    uberInsert.addUInt8(GetByteValue(PLAYER_BYTES, PLAYER_BYTES_OFFSET_HAIR_STYLE_ID));
    uberInsert.addUInt8(GetByteValue(PLAYER_BYTES, PLAYER_BYTES_OFFSET_HAIR_COLOR_ID));
    uberInsert.addUInt32(GetByteValue(PLAYER_BYTES_2, PLAYER_BYTES_2_OFFSET_FACIAL_STYLE));
    uberInsert.addUInt32(GetByteValue(PLAYER_BYTES_2, PLAYER_BYTES_2_OFFSET_BANK_BAG_SLOTS));

    UpdateCharacterFlags();
    uberInsert.addUInt32(m_characterFlags);

    if (!IsBeingTeleported())
    {
        uberInsert.addUInt32(GetMapId());
        uberInsert.addUInt32(GetInstanceId());
        uberInsert.addFloat(finiteAlways(GetPositionX()));
        uberInsert.addFloat(finiteAlways(GetPositionY()));
        uberInsert.addFloat(finiteAlways(GetPositionZ()));
        uberInsert.addFloat(Geometry::NormalizeOrientation(finiteAlways(GetOrientation())));
    }
    else
    {
        uberInsert.addUInt32(GetTeleportDest().mapId);
        uberInsert.addUInt32(0);
        uberInsert.addFloat(finiteAlways(GetTeleportDest().x));
        uberInsert.addFloat(finiteAlways(GetTeleportDest().y));
        uberInsert.addFloat(finiteAlways(GetTeleportDest().z));
        uberInsert.addFloat(Geometry::NormalizeOrientation(finiteAlways(GetTeleportDest().o)));
    }

    if (m_transport)
        uberInsert.addUInt32(m_transport->GetGUIDLow());
    else
        uberInsert.addUInt32(0);
    uberInsert.addFloat(finiteAlways(m_movementInfo.GetTransportPos().x));
    uberInsert.addFloat(finiteAlways(m_movementInfo.GetTransportPos().y));
    uberInsert.addFloat(finiteAlways(m_movementInfo.GetTransportPos().z));
    uberInsert.addFloat(Geometry::NormalizeOrientation(finiteAlways(m_movementInfo.GetTransportPos().o)));

    std::ostringstream ss;
    ss << m_taxi;                                   // string with TaxiMaskSize numbers
    uberInsert.addString(ss);

    ss << m_taxi.SaveTaxiDestinationsToString();
    uberInsert.addString(ss);

    if (!IsInWorld())
        online = false;
    uberInsert.addUInt32(online);

    uberInsert.addUInt32(m_playedTime[PLAYED_TIME_TOTAL]);
    uberInsert.addUInt32(m_playedTime[PLAYED_TIME_LEVEL]);

    uberInsert.addFloat(finiteAlways(m_restBonus));
    uberInsert.addUInt64(uint64(time(nullptr)));
    uberInsert.addUInt32(m_resetTalentsMultiplier);
    uberInsert.addUInt64(uint64(m_resetTalentsTime));
    uberInsert.addUInt32(m_ExtraFlags);
    uberInsert.addUInt32(uint32(m_stableSlots));                    // to prevent save uint8 as char
    uberInsert.addUInt32(GetCachedZoneId());
    uberInsert.addUInt64(uint64(m_deathExpireTime));

    // Honor stored data
    uberInsert.addFloat(finiteAlways(m_honorMgr.GetRankPoints()));
    uberInsert.addUInt32(uint32(m_honorMgr.GetHighestRank().rank));
    uberInsert.addUInt32(m_honorMgr.GetStanding());
    uberInsert.addUInt32(m_honorMgr.GetLastWeekHK());
    uberInsert.addFloat(finiteAlways(m_honorMgr.GetLastWeekCP()));
    uberInsert.addUInt32(m_honorMgr.GetStoredHK());
    uberInsert.addUInt32(m_honorMgr.GetStoredDK());

#if SUPPORTED_CLIENT_BUILD > CLIENT_BUILD_1_9_4
    uberInsert.addInt32(GetInt32Value(PLAYER_FIELD_WATCHED_FACTION_INDEX));
#else
    uberInsert.addInt32(-1);
#endif

    uberInsert.addUInt16(uint16(GetUInt32Value(PLAYER_BYTES_3) & 0xFFFE));

    uberInsert.addUInt32(GetHealth());

    for (uint32 i = 0; i < MAX_POWERS; ++i)
        uberInsert.addUInt32(GetPower(Powers(i)));

    for (uint32 i = 0; i < PLAYER_EXPLORED_ZONES_SIZE; ++i)         //string
        ss << GetUInt32Value(PLAYER_EXPLORED_ZONES_1 + i) << " ";
    uberInsert.addString(ss);

    for (uint32 i = 0; i < EQUIPMENT_SLOT_END; ++i)         //string: item id, ench (perm/temp)
    {
        ss << GetUInt32Value(PLAYER_VISIBLE_ITEM_1_0 + i * MAX_VISIBLE_ITEM_OFFSET) << " ";

        uint32 ench1 = GetUInt32Value(PLAYER_VISIBLE_ITEM_1_0 + i * MAX_VISIBLE_ITEM_OFFSET + 1 + PERM_ENCHANTMENT_SLOT);
        uint32 ench2 = GetUInt32Value(PLAYER_VISIBLE_ITEM_1_0 + i * MAX_VISIBLE_ITEM_OFFSET + 1 + TEMP_ENCHANTMENT_SLOT);
        ss << uint32(MAKE_PAIR32(ench1, ench2)) << " ";
    }
    // 1 in vanilla/tbc - 4 in wotlk
    for (uint32 i = INVENTORY_SLOT_BAG_START; i < INVENTORY_SLOT_BAG_START + 1; ++i) // string: item id, ench (perm/temp)
    {
        ss << (m_items[i] ? m_items[i]->GetEntry() : 0) << " ";
        ss << uint32(MAKE_PAIR32(0, 0)) << " ";
    }
    uberInsert.addString(ss);

    uberInsert.addUInt32(GetUInt32Value(PLAYER_AMMO_ID));

    uberInsert.addUInt32(uint32(GetByteValue(PLAYER_FIELD_BYTES, PLAYER_FIELD_BYTES_OFFSET_ACTION_BARS)));
    uberInsert.addUInt32(GetWorldMask());
    uberInsert.addUInt64(uint64(m_createTime));
    uberInsert.addUInt32(GetGUIDLow());
    uberInsert.addUInt32(GetSession()->GetAccountId());
    uberInsert.addString(m_name);
    uberInsert.addUInt64(identity.generation + 1);
    // The identity guard in the same batch (generation+1, ExactlyOne) is the
    // exact claim.  This value update may set identical values, and MySQL
    // reports changed rows rather than matched rows, so it must not require
    // ExactlyOne.
    if (!uberInsert.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::Any))
    {
        context.Invalidate();
        return false;
    }
    return true;
}

bool Player::AppendGoldToDB(CharacterPersistenceContext& context)
{
    return AppendGoldProjectedToDB(context, GetMoney());
}

bool Player::AppendGoldProjectedToDB(CharacterPersistenceContext& context,
                                     uint32 projectedMoney)
{
    CharacterIdentitySnapshot const identity = SaveIdentity(*this);
    if (!GetSession() || !context.HasIdentityGuard(identity) ||
        identity.generation == 0 ||
        identity.generation == std::numeric_limits<uint64>::max())
    {
        context.Invalidate();
        return false;
    }
    Database* database = context.Batch().GetDatabase();
    if (!database)
    {
        context.Invalidate();
        return false;
    }

    static SqlStatementID updateGold;
    SqlStatement statement = database->CreateStatement(
        updateGold,
        "UPDATE `characters` SET `money` = ? "
        "WHERE `guid` = ? AND `account` = ? AND BINARY `name` = BINARY ? "
        "AND `deleted_time` IS NULL AND `identity_generation` = ?");
    statement.addUInt32(projectedMoney);
    statement.addUInt32(identity.guid);
    statement.addUInt32(identity.accountId);
    statement.addString(identity.canonicalName);
    statement.addUInt64(identity.generation + 1);
    // Balance may legitimately be unchanged; the identity guard is the exact
    // claim for this row and generation.
    if (!statement.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::Any))
    {
        context.Invalidate();
        return false;
    }
    return true;
}

bool Player::AppendInventoryToDB(CharacterPersistenceContext& context)
{
    CharacterIdentitySnapshot const identity = SaveIdentity(*this);
    if (!GetSession() || !context.HasIdentityGuard(identity))
    {
        context.Invalidate();
        return false;
    }
    Database* database = context.Batch().GetDatabase();
    if (!database)
    {
        context.Invalidate();
        return false;
    }

    // force items in buyback slots to new state
    // and remove those that aren't already
    std::vector<Item*> buybackResets;
    for (uint8 i = BUYBACK_SLOT_START; i < BUYBACK_SLOT_END; ++i)
    {
        Item* item = m_items[i];
        if (!item || item->GetState() == ITEM_NEW)
            continue;

        static SqlStatementID delInv;
        static SqlStatementID delItemInst;

        SqlStatement stmt = database->CreateStatement(
            delInv, "DELETE FROM `character_inventory` WHERE `item_guid` = ?");
        stmt.addUInt32(item->GetGUIDLow());
        if (!stmt.AppendToCheckedBatch(
                context.Batch(), SqlAffectedRowsPolicy::Any))
        {
            context.Invalidate();
            return false;
        }

        stmt = database->CreateStatement(
            delItemInst, "DELETE FROM `item_instance` WHERE `guid` = ?");
        stmt.addUInt32(item->GetGUIDLow());
        if (!stmt.AppendToCheckedBatch(
                context.Batch(), SqlAffectedRowsPolicy::Any))
        {
            context.Invalidate();
            return false;
        }

        buybackResets.push_back(item);
    }

    // update enchantment durations
    for (auto& itr : m_enchantDuration)
        itr.item->SetEnchantmentDuration(itr.slot, itr.leftduration);

    if (buybackResets.empty() && m_itemUpdateQueue.empty())
        return true;

    if (!buybackResets.empty())
    {
        context.OnCommit([buybackResets]() {
            for (Item* item : buybackResets)
                item->FSetState(ITEM_NEW);
        });
    }

    // if no changes
    if (m_itemUpdateQueue.empty())
        return true;

    std::vector<uint32> stagedItemGuids;
    stagedItemGuids.reserve(m_itemUpdateQueue.size());
    for (Item* item : m_itemUpdateQueue)
        if (item)
            stagedItemGuids.push_back(item->GetGUIDLow());

    for (Item* item : m_itemUpdateQueue)
    {
        // items remember their place in the update queue, and when they need to
        // be removed from the queue they just set that index to null
        if (!item)
            continue;

        if (item->GetState() != ITEM_REMOVED)
        {
            // Several anti-duplicate tests ...
            Item* test = GetItemByPos(item->GetBagSlot(), item->GetSlot());

            if (test == nullptr)
            {
                uint32 bagTestGUID = 0;
                if (Item* test2 = GetItemByPos(INVENTORY_SLOT_BAG_0, item->GetBagSlot()))
                    bagTestGUID = test2->GetGUIDLow();
                // according to the test that was just performed nothing should be in this slot, delete
                static SqlStatementID deleteInventoryAntiDupli;
                SqlStatement stmt = database->CreateStatement(
                    deleteInventoryAntiDupli,
                    "DELETE FROM `character_inventory` WHERE `bag`=? AND `slot`=? AND `guid`=?");
                stmt.addUInt32(bagTestGUID);
                stmt.addUInt32(item->GetSlot());
                stmt.addUInt32(GetGUIDLow());
                if (!stmt.AppendToCheckedBatch(
                        context.Batch(), SqlAffectedRowsPolicy::Any))
                {
                    context.Invalidate();
                    return false;
                }
                // also THIS item should be somewhere else, cheat attempt
                GetSession()->ProcessAnticheatAction("PassiveAnticheat", "_SaveInventory: item not found", CHEAT_ACTION_LOG | CHEAT_ACTION_REPORT_GMS);
                item->FSetState(ITEM_REMOVED); // we are IN updateQueue right now, can't use SetState which modifies the queue
                // don't skip, let the switch delete it
            }
            else if (test != item)
            {
                if (item->GetState() != ITEM_NEW) // only for existing items, no dupes
                    if (!item->AppendSaveToDB(context, this))
                        return false;
                // ...but do not save position in inventory
                continue;
            }
        }

        if (item->GetOwnerGuid() != GetObjectGuid())
            GetSession()->ProcessAnticheatAction("PassiveAnticheat", "_SaveInventory: attempting to save not owned item", CHEAT_ACTION_LOG | CHEAT_ACTION_REPORT_GMS);

        static SqlStatementID insertInventory;
        static SqlStatementID updateInventory;
        static SqlStatementID deleteInventory;

        Bag* container = item->GetContainer();
        uint32 bagGuid = container ? container->GetGUIDLow() : 0;

        switch (item->GetState())
        {
            case ITEM_NEW:
            {
                SqlStatement stmt = database->CreateStatement(
                    insertInventory,
                    "INSERT INTO `character_inventory` (`guid`, `bag`, `slot`, `item_guid`, `item_id`) VALUES (?, ?, ?, ?, ?)");
                stmt.addUInt32(GetGUIDLow());
                stmt.addUInt32(bagGuid);
                stmt.addUInt8(item->GetSlot());
                stmt.addUInt32(item->GetGUIDLow());
                stmt.addUInt32(item->GetEntry());
                if (!stmt.AppendToCheckedBatch(
                        context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
                {
                    context.Invalidate();
                    return false;
                }
                break;
            }
            case ITEM_CHANGED:
            {
                SqlStatement stmt = database->CreateStatement(
                    updateInventory,
                    "UPDATE `character_inventory` SET `guid` = ?, `bag` = ?, `slot` = ?, `item_id` = ? WHERE `item_guid` = ?");
                stmt.addUInt32(GetGUIDLow());
                stmt.addUInt32(bagGuid);
                stmt.addUInt8(item->GetSlot());
                stmt.addUInt32(item->GetEntry());
                stmt.addUInt32(item->GetGUIDLow());
                if (!stmt.AppendToCheckedBatch(
                        context.Batch(), SqlAffectedRowsPolicy::Any))
                {
                    context.Invalidate();
                    return false;
                }
                break;
            }
            case ITEM_REMOVED:
            {
                SqlStatement stmt = database->CreateStatement(
                    deleteInventory,
                    "DELETE FROM `character_inventory` WHERE `item_guid` = ?");
                stmt.addUInt32(item->GetGUIDLow());
                if (!stmt.AppendToCheckedBatch(
                        context.Batch(), SqlAffectedRowsPolicy::Any))
                {
                    context.Invalidate();
                    return false;
                }
                break;
            }
            case ITEM_UNCHANGED:
                break;
        }

        // item have unchanged inventory record and can be saved standalone
        if (!item->AppendSaveToDB(context, this))
            return false;
    }

    // Acknowledge only the entries staged by this unit, using this Player as the
    // authoritative owner (it may already be outside the world during logout).
    // Unrelated/newer entries are preserved and re-indexed.
    context.OnCommit([this, identity, stagedItemGuids]() {
        if (!SaveIdentityMatches(*this, identity))
            return;

        bool acknowledgmentFailed = false;
        std::vector<Item*> destroyAfterAck;
        for (Item*& item : m_itemUpdateQueue)
        {
            if (!item)
                continue;
            if (std::find(stagedItemGuids.begin(), stagedItemGuids.end(),
                          item->GetGUIDLow()) == stagedItemGuids.end())
                continue;                               // newer/unrelated entry

            bool const wasRemoved = item->GetState() == ITEM_REMOVED;
            if (!item->AcknowledgeSave(this))
            {
                acknowledgmentFailed = true;
                continue;                               // keep its queue entry
            }
            if (wasRemoved)
                destroyAfterAck.push_back(item);
        }

        std::size_t write = 0;
        for (std::size_t read = 0; read < m_itemUpdateQueue.size(); ++read)
        {
            Item* item = m_itemUpdateQueue[read];
            if (!item)
                continue;
            m_itemUpdateQueue[write] = item;
            item->uQueuePos = int16(write);
            ++write;
        }
        m_itemUpdateQueue.resize(write);

        for (Item* item : destroyAfterAck)
        {
            if (item->IsInWorld())
                item->RemoveFromWorld();
            delete item;
        }

        if (acknowledgmentFailed)
            BlockCharacterRecovery(CharacterRecoveryRecord{
                CharacterRecoveryKind::WholeSave, {identity.guid},
                {identity.accountId}, identity.generation,
                "committed inventory acknowledgment failed"});
    });
    return true;
}

void Player::AppendIdentityGenerationPublication(
    CharacterPersistenceContext& context)
{
    std::vector<CharacterIdentitySnapshot> const& staged =
        context.StagedIdentities();
    if (staged.size() != 1)
        return;

    CharacterIdentitySnapshot const identity = staged.front();
    MasterPlayer* master = GetSession() ? GetSession()->GetMasterPlayer() : nullptr;
    if (master && master->identityGeneration != identity.generation)
    {
        sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                 "[PLAYER SAVE] %s master identity generation %llu does not match "
                 "player generation %llu; generation publication skipped.",
                 GetGuidStr().c_str(),
                 (unsigned long long)master->identityGeneration,
                 (unsigned long long)identity.generation);
        return;
    }

    context.OnCommit([this, identity, master]() {
        if (GetIdentityGeneration() == identity.generation)
            SetIdentityGeneration(identity.generation + 1);
        if (master && master->identityGeneration == identity.generation)
            master->SetIdentityGeneration(identity.generation + 1);
    });
}

bool Player::AppendSaveToDB(CharacterPersistenceContext& context, bool online,
                            bool includeStats)
{
    CharacterIdentitySnapshot const identity = SaveIdentity(*this);
    if (!GetSession() || !context.HasIdentityGuard(identity))
    {
        context.Invalidate();
        return false;
    }
    if (IsSavingDisabled() || IsCharacterRecoveryBlocked(GetGUIDLow()))
    {
        context.Invalidate();
        return false;
    }
    if (m_identityGeneration == 0)
    {
        context.Invalidate();
        return false;
    }
    MasterPlayer* master = GetSession()->GetMasterPlayer();
    if (master && master->identityGeneration != identity.generation)
    {
        sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                 "[PLAYER SAVE] %s master identity generation mismatch; save rejected.",
                 GetGuidStr().c_str());
        context.Invalidate();
        return false;
    }

    m_honorMgr.Update();

    if (!AppendCharacterRowToDB(context, online) ||
        !AppendBGData(context) ||
        !AppendInventoryToDB(context) ||
        !AppendQuestStatus(context, uint64(sWorld.GetGameTime())) ||
        !AppendSpells(context) ||
        !AppendSpellCooldowns(context) ||
        !AppendAuras(context) ||
        !AppendSkills(context) ||
        !m_reputationMgr.AppendSaveToDB(context) ||
        !m_honorMgr.AppendSaveToDB(context) ||
        !GetSession()->AppendTutorialsDataToContext(context) ||
        (includeStats && !AppendStats(
            context, sWorld.getConfig(CONFIG_UINT32_MIN_LEVEL_STAT_SAVE))) ||
        (master && (!master->AppendActionsToDB(context) ||
                    !master->AppendMailsToDB(context))))
        return false;

    // Local cache publication is not a persistence acknowledgment, but it must
    // still wait for the commit like every other publication.
    context.OnCommit([this, identity]() {
        if (!SaveIdentityMatches(*this, identity))
            return;
        sObjectMgr.SetPlayerWorldMask(GetGUIDLow(), GetWorldMask());
        sObjectMgr.UpdatePlayerCachedLevelAndZone(GetGUIDLow(), GetLevel(),
                                                  GetCachedZoneId());
    });

    // Registered last: every dirty-state acknowledgment above still observes
    // the staged generation.  Both surviving representations that perform later
    // guarded writes advance exactly once.
    AppendIdentityGenerationPublication(context);
    return true;
}

void Player::SaveToDB(bool online, bool force)
{
    // delay auto save at any saves (manual, in code, or autosave)
    m_nextSave = sWorld.getConfig(CONFIG_UINT32_INTERVAL_SAVE);

    // Do not save bots
    if (IsSavingDisabled())
        return;

    if (IsCharacterRecoveryBlocked(GetGUIDLow()))
    {
        sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                 "Save blocked by unresolved persistence outcome for guid %u",
                 GetGUIDLow());
        return;
    }

    // lets allow only players in world to be saved
    if (!force && IsBeingTeleportedFar())
    {
        ScheduleDelayedOperation(DELAYED_SAVE_PLAYER);
        return;
    }

    if (m_identityGeneration == 0)
    {
        sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                 "[PLAYER SAVE] %s has no identity generation; save rejected.",
                 GetGuidStr().c_str());
        return;
    }
    if (!GetSession())
    {
        sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                 "[PLAYER SAVE] %s has no session; save rejected.",
                 GetGuidStr().c_str());
        return;
    }

    CharacterIdentitySnapshot const identity = SaveIdentity(*this);
    if (identity.accountId == 0)
        return;

    CharacterPersistenceAdmission admission;
    if (!admission.Admit({identity.accountId}, AccountLifecycleKind::Mutation))
    {
        sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                 "[PLAYER SAVE] admission unavailable for guid %u", GetGUIDLow());
        return;
    }

    CharacterPersistenceContext context(CharacterDatabase, GetGUIDLow());
    if (!context.AppendIdentityGuards({identity}))
    {
        sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                 "[PLAYER SAVE] identity guard assembly failed for guid %u",
                 GetGUIDLow());
        return;
    }

    bool const includeStats =
        GetSession()->IsLogingOut() ||
        !sWorld.getConfig(CONFIG_BOOL_STATS_SAVE_ONLY_ON_LOGOUT);

    if (!AppendSaveToDB(context, online, includeStats))
    {
        sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                 "[PLAYER SAVE] assembly failed for guid %u", GetGUIDLow());
        return;
    }

    SqlBatchResult const result = context.Execute();
    if (result.outcome != SqlWriteOutcome::Succeeded ||
        result.stage != SqlBatchStage::Complete)
    {
        if (result.outcome == SqlWriteOutcome::OutcomeUnknown)
        {
            BlockCharacterRecovery(CharacterRecoveryRecord{
                CharacterRecoveryKind::WholeSave, {GetGUIDLow()},
                {identity.accountId}, identity.generation,
                "ordinary character save outcome unknown"});
        }
        sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                 "[PLAYER SAVE] guid %u failed (outcome %u, stage %u, stmt %zu)",
                 GetGUIDLow(), uint32(result.outcome), uint32(result.stage),
                 result.statementIndex);
        return;
    }

    // save pet (hunter pet level and experience and all type pets health/mana).
    // Pet rows are a separate operation, as in the pre-adoption flow; it runs
    // only after the player save committed and published its acknowledgments.
    if (Pet* pet = GetPet())
        pet->SavePetToDB(PET_SAVE_AS_CURRENT);
}

void Player::SaveInventoryAndGoldToDB()
{
    if (IsSavingDisabled())
        return;
    if (IsCharacterRecoveryBlocked(GetGUIDLow()))
        return;
    if (m_identityGeneration == 0 || !GetSession() ||
        GetSession()->GetAccountId() == 0)
        return;

    CharacterIdentitySnapshot const identity = SaveIdentity(*this);
    CharacterPersistenceAdmission admission;
    if (!admission.Admit({identity.accountId}, AccountLifecycleKind::Mutation))
        return;
    CharacterPersistenceContext context(CharacterDatabase, GetGUIDLow());
    if (!context.AppendIdentityGuards({identity}))
        return;
    if (!AppendInventoryToDB(context) || !AppendGoldToDB(context))
        return;
    AppendIdentityGenerationPublication(context);

    SqlBatchResult const result = context.Execute();
    if (result.outcome != SqlWriteOutcome::Succeeded ||
        result.stage != SqlBatchStage::Complete)
    {
        if (result.outcome == SqlWriteOutcome::OutcomeUnknown)
            BlockCharacterRecovery(CharacterRecoveryRecord{
                CharacterRecoveryKind::WholeSave, {GetGUIDLow()},
                {identity.accountId}, identity.generation,
                "inventory and gold save outcome unknown"});
        sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                 "[PLAYER SAVE] inventory/gold guid %u failed (outcome %u, stage %u)",
                 GetGUIDLow(), uint32(result.outcome), uint32(result.stage));
    }
}

void Player::SaveGoldToDB()
{
    if (IsSavingDisabled())
        return;
    if (IsCharacterRecoveryBlocked(GetGUIDLow()))
        return;
    if (m_identityGeneration == 0 || !GetSession() ||
        GetSession()->GetAccountId() == 0)
        return;

    CharacterIdentitySnapshot const identity = SaveIdentity(*this);
    CharacterPersistenceAdmission admission;
    if (!admission.Admit({identity.accountId}, AccountLifecycleKind::Mutation))
        return;
    CharacterPersistenceContext context(CharacterDatabase, GetGUIDLow());
    if (!context.AppendIdentityGuards({identity}))
        return;
    if (!AppendGoldToDB(context))
        return;
    AppendIdentityGenerationPublication(context);

    SqlBatchResult const result = context.Execute();
    if (result.outcome != SqlWriteOutcome::Succeeded ||
        result.stage != SqlBatchStage::Complete)
    {
        if (result.outcome == SqlWriteOutcome::OutcomeUnknown)
            BlockCharacterRecovery(CharacterRecoveryRecord{
                CharacterRecoveryKind::WholeSave, {GetGUIDLow()},
                {identity.accountId}, identity.generation,
                "gold save outcome unknown"});
        sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                 "[PLAYER SAVE] gold guid %u failed (outcome %u, stage %u)",
                 GetGUIDLow(), uint32(result.outcome), uint32(result.stage));
    }
}
