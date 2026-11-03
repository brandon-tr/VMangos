#include "Database/CharacterPersistenceContext.h"
#include "Database/Database.h"
#include "Database/SqlPreparedStatement.h"
#include "Objects/Player.h"
#include "ReputationMgr.h"
#include "Chat/MasterPlayer.h"
#include "QuestDef.h"
#include "Database/CharacterDatabaseCache.h"
#include "WorldSession.h"
#include "SpellCaster.h"
#include <algorithm>
#include <limits>

#define FN05H_PLAYER_SKILL_INDEX(x) (PLAYER_SKILL_INFO_1_1 + ((x)*3))
#define FN05H_PLAYER_SKILL_VALUE_INDEX(x) (FN05H_PLAYER_SKILL_INDEX(x)+1)
#define FN05H_SKILL_VALUE(x) PAIR32_LOPART(x)
#define FN05H_SKILL_MAX(x) PAIR32_HIPART(x)
#include <vector>

namespace
{
CharacterIdentitySnapshot PlayerIdentity(Player const& player)
{
    CharacterIdentitySnapshot identity;
    identity.guid = player.GetGUIDLow();
    identity.accountId = player.GetSession() ? player.GetSession()->GetAccountId() : 0;
    identity.canonicalName = player.GetName();
    identity.generation = player.GetIdentityGeneration();
    return identity;
}

bool PlayerIdentityMatches(Player& player, CharacterIdentitySnapshot const& expected)
{
    CharacterIdentitySnapshot const current = PlayerIdentity(player);
    return current.guid == expected.guid &&
           current.accountId == expected.accountId &&
           current.canonicalName == expected.canonicalName &&
           current.generation == expected.generation;
}

CharacterIdentitySnapshot MasterIdentity(MasterPlayer& player)
{
    CharacterIdentitySnapshot identity;
    identity.guid = player.GetGUIDLow();
    identity.accountId = player.GetSession() ? player.GetSession()->GetAccountId() : 0;
    identity.canonicalName = player.name;
    identity.generation = player.identityGeneration;
    return identity;
}

bool MasterIdentityMatches(MasterPlayer& player, CharacterIdentitySnapshot const& expected)
{
    CharacterIdentitySnapshot const current = MasterIdentity(player);
    return current.guid == expected.guid &&
           current.accountId == expected.accountId &&
           current.canonicalName == expected.canonicalName &&
           current.generation == expected.generation;
}
}

bool Player::AppendQuestStatus(CharacterPersistenceContext& context,
                               uint64 gameTimeSeconds)
{
    if (!GetSession() || !context.HasIdentityGuard(PlayerIdentity(*this)))
    {
        context.Invalidate();
        return false;
    }

    struct QuestAck
    {
        uint32 key;
        QuestStatusData data;
    };
    std::vector<QuestAck> acknowledgments;
    Database* database = context.Batch().GetDatabase();
    if (!database)
    {
        context.Invalidate();
        return false;
    }

    for (auto const& entry : mQuestStatus)
    {
        QuestStatusData const& quest = entry.second;
        if (quest.uState == QUEST_UNCHANGED)
            continue;

        if (quest.uState == QUEST_DELETED)
        {
            static SqlStatementID deleteQuest;
            SqlStatement remove = database->CreateStatement(
                deleteQuest,
                "DELETE FROM `character_queststatus` WHERE `guid` = ? AND `quest` = ?");
            remove.addUInt32(GetGUIDLow());
            remove.addUInt32(entry.first);
            if (!remove.AppendToCheckedBatch(
                    context.Batch(), SqlAffectedRowsPolicy::Any))
            {
                context.Invalidate();
                return false;
            }
            acknowledgments.push_back(QuestAck{entry.first, quest});
            continue;
        }

        uint64 const seconds = uint64(quest.m_timer) / IN_MILLISECONDS;
        if (seconds > std::numeric_limits<uint64>::max() - gameTimeSeconds)
        {
            context.Invalidate();
            return false;
        }
        uint64 const expiry = seconds + gameTimeSeconds;

        if (quest.uState == QUEST_NEW)
        {
            static SqlStatementID insertQuest;
            SqlStatement insert = database->CreateStatement(
                insertQuest,
                "INSERT INTO `character_queststatus` (`guid`, `quest`, `status`, `rewarded`, `explored`, `timer`, `mob_count1`, `mob_count2`, `mob_count3`, `mob_count4`, `item_count1`, `item_count2`, `item_count3`, `item_count4`, `reward_choice`) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)");
            insert.addUInt32(GetGUIDLow());
            insert.addUInt32(entry.first);
            insert.addUInt8(quest.m_status);
            insert.addUInt8(quest.m_rewarded);
            insert.addUInt8(quest.m_explored);
            insert.addUInt64(expiry);
            for (uint32 value : quest.m_creatureOrGOcount)
                insert.addUInt32(value);
            for (uint32 value : quest.m_itemcount)
                insert.addUInt32(value);
            insert.addUInt32(quest.m_reward_choice);
            if (!insert.AppendToCheckedBatch(
                    context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
            {
                context.Invalidate();
                return false;
            }
        }
        else
        {
            static SqlStatementID updateQuest;
            SqlStatement update = database->CreateStatement(
                updateQuest,
                "UPDATE `character_queststatus` SET `status` = ?, `rewarded` = ?, `reward_choice` = ?, `explored` = ?, `timer` = ?, `mob_count1` = ?, `mob_count2` = ?, `mob_count3` = ?, `mob_count4` = ?, `item_count1` = ?, `item_count2` = ?, `item_count3` = ?, `item_count4` = ? WHERE `guid` = ? AND `quest` = ?");
            update.addUInt8(quest.m_status);
            update.addUInt8(quest.m_rewarded);
            update.addUInt32(quest.m_reward_choice);
            update.addUInt8(quest.m_explored);
            update.addUInt64(expiry);
            for (uint32 value : quest.m_creatureOrGOcount)
                update.addUInt32(value);
            for (uint32 value : quest.m_itemcount)
                update.addUInt32(value);
            update.addUInt32(GetGUIDLow());
            update.addUInt32(entry.first);
            if (!update.AppendToCheckedBatch(
                    context.Batch(), SqlAffectedRowsPolicy::Any))
            {
                context.Invalidate();
                return false;
            }
        }
        acknowledgments.push_back(QuestAck{entry.first, quest});
    }

    CharacterIdentitySnapshot const identity = PlayerIdentity(*this);
    context.OnCommit([this, identity, acknowledgments]() {
        if (!PlayerIdentityMatches(*this, identity))
            return;
        for (QuestAck const& ack : acknowledgments)
        {
            auto itr = mQuestStatus.find(ack.key);
            bool const present = ack.data.uState != QUEST_DELETED;
            if (present)
            {
                if (itr == mQuestStatus.end())
                {
                    QuestStatusData tombstone = ack.data;
                    tombstone.uState = QUEST_DELETED;
                    mQuestStatus[ack.key] = tombstone;
                    continue;
                }
                QuestStatusData& current = itr->second;
                if (current.uState == QUEST_DELETED)
                    continue;
                if (current.m_status == ack.data.m_status &&
                    current.m_rewarded == ack.data.m_rewarded &&
                    current.m_explored == ack.data.m_explored &&
                    current.m_timer == ack.data.m_timer &&
                    current.m_reward_choice == ack.data.m_reward_choice &&
                    std::equal(std::begin(current.m_itemcount), std::end(current.m_itemcount),
                               std::begin(ack.data.m_itemcount)) &&
                    std::equal(std::begin(current.m_creatureOrGOcount),
                               std::end(current.m_creatureOrGOcount),
                               std::begin(ack.data.m_creatureOrGOcount)))
                    current.uState = QUEST_UNCHANGED;
                else
                    current.uState = QUEST_CHANGED;
            }
            else if (itr != mQuestStatus.end())
            {
                if (itr->second.uState == QUEST_DELETED)
                    mQuestStatus.erase(itr);
                else
                    itr->second.uState = QUEST_NEW;
            }
        }
    });
    return true;
}

bool Player::AppendSkills(CharacterPersistenceContext& context)
{
    if (!GetSession() || !context.HasIdentityGuard(PlayerIdentity(*this)))
    {
        context.Invalidate();
        return false;
    }

    struct SkillAck
    {
        uint32 key;
        uint8 pos;
        uint16 value;
        uint16 maximum;
        SkillUpdateState state;
    };
    std::vector<SkillAck> acknowledgments;
    Database* database = context.Batch().GetDatabase();
    if (!database)
    {
        context.Invalidate();
        return false;
    }

    for (auto const& entry : m_skillStatusMap)
    {
        SkillStatusData const& skill = entry.second;
        if (skill.uState == SKILL_UNCHANGED)
            continue;

        if (skill.uState == SKILL_DELETED)
        {
            static SqlStatementID deleteSkill;
            SqlStatement remove = database->CreateStatement(
                deleteSkill,
                "DELETE FROM `character_skills` WHERE `guid` = ? AND `skill` = ?");
            remove.addUInt32(GetGUIDLow());
            remove.addUInt32(entry.first);
            if (!remove.AppendToCheckedBatch(
                    context.Batch(), SqlAffectedRowsPolicy::Any))
            {
                context.Invalidate();
                return false;
            }
            acknowledgments.push_back(
                SkillAck{entry.first, 0, 0, 0, SKILL_DELETED});
            continue;
        }

        uint32 const valueData = GetUInt32Value(FN05H_PLAYER_SKILL_VALUE_INDEX(skill.pos));
        uint16 const value = FN05H_SKILL_VALUE(valueData);
        uint16 const maximum = FN05H_SKILL_MAX(valueData);
        if (skill.uState == SKILL_NEW)
        {
            static SqlStatementID insertSkill;
            SqlStatement insert = database->CreateStatement(
                insertSkill,
                "INSERT INTO `character_skills` (`guid`, `skill`, `value`, `max`) VALUES (?, ?, ?, ?)");
            insert.addUInt32(GetGUIDLow());
            insert.addUInt32(entry.first);
            insert.addUInt16(value);
            insert.addUInt16(maximum);
            if (!insert.AppendToCheckedBatch(
                    context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
            {
                context.Invalidate();
                return false;
            }
        }
        else
        {
            static SqlStatementID updateSkill;
            SqlStatement update = database->CreateStatement(
                updateSkill,
                "UPDATE `character_skills` SET `value` = ?, `max` = ? WHERE `guid` = ? AND `skill` = ?");
            update.addUInt16(value);
            update.addUInt16(maximum);
            update.addUInt32(GetGUIDLow());
            update.addUInt32(entry.first);
            if (!update.AppendToCheckedBatch(
                    context.Batch(), SqlAffectedRowsPolicy::Any))
            {
                context.Invalidate();
                return false;
            }
        }
        acknowledgments.push_back(
            SkillAck{entry.first, skill.pos, value, maximum, skill.uState});
    }

#if SUPPORTED_CLIENT_BUILD > CLIENT_BUILD_1_10_2
    for (auto const& entry : m_forgottenSkills)
    {
        if (entry.second <= 1)
            continue;
        static SqlStatementID replaceForgottenSkill;
        SqlStatement replace = database->CreateStatement(
            replaceForgottenSkill,
            "REPLACE INTO `character_forgotten_skills` (`guid`, `skill`, `value`) VALUES (?, ?, ?)");
        replace.addUInt32(GetGUIDLow());
        replace.addUInt16(entry.first);
        replace.addUInt16(entry.second);
        if (!replace.AppendToCheckedBatch(
                context.Batch(), SqlAffectedRowsPolicy::Any))
        {
            context.Invalidate();
            return false;
        }
    }
#endif
    CharacterIdentitySnapshot const identity = PlayerIdentity(*this);
    context.OnCommit([this, identity, acknowledgments]() {
        if (!PlayerIdentityMatches(*this, identity))
            return;
        for (SkillAck const& ack : acknowledgments)
        {
            auto itr = m_skillStatusMap.find(ack.key);
            if (ack.state == SKILL_DELETED)
            {
                if (itr == m_skillStatusMap.end())
                    continue;
                if (itr->second.uState == SKILL_DELETED)
                    m_skillStatusMap.erase(itr);
                else
                    itr->second.uState = SKILL_NEW;
                continue;
            }
            if (itr == m_skillStatusMap.end())
            {
                m_skillStatusMap.emplace(
                    ack.key, SkillStatusData{ack.pos, SKILL_DELETED});
                continue;
            }
            SkillStatusData& current = itr->second;
            if (current.uState == SKILL_DELETED)
                continue;
            uint32 const valueData =
                GetUInt32Value(FN05H_PLAYER_SKILL_VALUE_INDEX(current.pos));
            if (current.pos == ack.pos &&
                FN05H_SKILL_VALUE(valueData) == ack.value &&
                FN05H_SKILL_MAX(valueData) == ack.maximum)
                current.uState = SKILL_UNCHANGED;
            else
                current.uState = SKILL_CHANGED;
        }
    });
    return true;
}

bool Player::AppendSpells(CharacterPersistenceContext& context)
{
    if (!GetSession() || !context.HasIdentityGuard(PlayerIdentity(*this)))
    {
        context.Invalidate();
        return false;
    }

    struct SpellAck
    {
        uint32 key;
        bool rowPresent;
        bool active;
        bool disabled;
        bool dependent;
    };
    std::vector<SpellAck> acknowledgments;
    Database* database = context.Batch().GetDatabase();
    if (!database)
    {
        context.Invalidate();
        return false;
    }

    static SqlStatementID deleteSpell;
    static SqlStatementID insertSpell;
    for (auto const& entry : m_spells)
    {
        PlayerSpell const& spell = entry.second;
        if (spell.state == PLAYERSPELL_UNCHANGED)
            continue;

        if (spell.state == PLAYERSPELL_CHANGED ||
            spell.state == PLAYERSPELL_REMOVED)
        {
            SqlStatement remove = database->CreateStatement(
                deleteSpell,
                "DELETE FROM `character_spell` WHERE `guid` = ? and `spell` = ?");
            remove.addUInt32(GetGUIDLow());
            remove.addUInt32(entry.first);
            if (!remove.AppendToCheckedBatch(
                    context.Batch(), SqlAffectedRowsPolicy::Any))
            {
                context.Invalidate();
                return false;
            }
        }

        bool const rowPresent =
            !spell.dependent &&
            (spell.state == PLAYERSPELL_NEW ||
             spell.state == PLAYERSPELL_CHANGED);
        if (rowPresent)
        {
            SqlStatement insert = database->CreateStatement(
                insertSpell,
                "INSERT INTO `character_spell` (`guid`, `spell`, `active`, `disabled`) VALUES (?, ?, ?, ?)");
            insert.addUInt32(GetGUIDLow());
            insert.addUInt32(entry.first);
            insert.addUInt8(spell.active ? 1 : 0);
            insert.addUInt8(spell.disabled ? 1 : 0);
            if (!insert.AppendToCheckedBatch(
                    context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
            {
                context.Invalidate();
                return false;
            }
        }
        acknowledgments.push_back(
            SpellAck{entry.first, rowPresent, spell.active, spell.disabled,
                     spell.dependent});
    }

    CharacterIdentitySnapshot const identity = PlayerIdentity(*this);
    context.OnCommit([this, identity, acknowledgments]() {
        if (!PlayerIdentityMatches(*this, identity))
            return;
        for (SpellAck const& ack : acknowledgments)
        {
            auto itr = m_spells.find(ack.key);
            if (ack.rowPresent)
            {
                if (itr == m_spells.end())
                {
                    PlayerSpell tombstone;
                    tombstone.state = PLAYERSPELL_REMOVED;
                    tombstone.active = ack.active;
                    tombstone.dependent = ack.dependent;
                    tombstone.disabled = ack.disabled;
                    m_spells[ack.key] = tombstone;
                    continue;
                }
                PlayerSpell& current = itr->second;
                if (current.state == PLAYERSPELL_REMOVED)
                    continue;
                if (current.dependent)
                    current.state = PLAYERSPELL_CHANGED;
                else if (current.active == ack.active &&
                         current.disabled == ack.disabled)
                    current.state = PLAYERSPELL_UNCHANGED;
                else
                    current.state = PLAYERSPELL_CHANGED;
            }
            else if (itr != m_spells.end())
            {
                if (itr->second.state == PLAYERSPELL_REMOVED)
                    m_spells.erase(itr);
                else if (itr->second.dependent)
                    itr->second.state = PLAYERSPELL_UNCHANGED;
                else
                    itr->second.state = PLAYERSPELL_NEW;
            }
        }
    });
    return true;
}

bool Player::AppendBGData(CharacterPersistenceContext& context)
{
    if (!GetSession() || !context.HasIdentityGuard(PlayerIdentity(*this)))
    {
        context.Invalidate();
        return false;
    }

    if (!m_bgData.m_needSave)
        return true;

    struct CapturedBG
    {
        CharacterIdentitySnapshot identity;
        uint32 instanceId;
        uint32 team;
        float x;
        float y;
        float z;
        float orientation;
        uint32 mapId;
        bool inQueue;
    } captured = {PlayerIdentity(*this), m_bgData.bgInstanceID,
                  uint32(m_bgData.bgTeam), m_bgData.joinPos.x,
                  m_bgData.joinPos.y, m_bgData.joinPos.z,
                  m_bgData.joinPos.o, m_bgData.joinPos.mapId,
                  InBattleGroundQueue()};

    Database* database = context.Batch().GetDatabase();
    if (!database)
    {
        context.Invalidate();
        return false;
    }

    static SqlStatementID deleteBG;
    SqlStatement remove = database->CreateStatement(
        deleteBG, "DELETE FROM `character_battleground_data` WHERE `guid` = ?");
    remove.addUInt32(GetGUIDLow());
    if (!remove.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::Any))
    {
        context.Invalidate();
        return false;
    }

    if (m_bgData.bgInstanceID || InBattleGroundQueue())
    {
        static SqlStatementID insertBG;
        SqlStatement insert = database->CreateStatement(
            insertBG, "INSERT INTO `character_battleground_data` VALUES "
                      "(?, ?, ?, ?, ?, ?, ?, ?)");
        insert.addUInt32(GetGUIDLow());
        insert.addUInt32(m_bgData.bgInstanceID);
        insert.addUInt32(uint32(m_bgData.bgTeam));
        insert.addFloat(m_bgData.joinPos.x);
        insert.addFloat(m_bgData.joinPos.y);
        insert.addFloat(m_bgData.joinPos.z);
        insert.addFloat(m_bgData.joinPos.o);
        insert.addUInt32(m_bgData.joinPos.mapId);
        if (!insert.AppendToCheckedBatch(
                context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
        {
            context.Invalidate();
            return false;
        }
    }

    context.OnCommit([this, captured]() {
        if (!PlayerIdentityMatches(*this, captured.identity) ||
            !m_bgData.m_needSave ||
            m_bgData.bgInstanceID != captured.instanceId ||
            uint32(m_bgData.bgTeam) != captured.team ||
            m_bgData.joinPos.x != captured.x ||
            m_bgData.joinPos.y != captured.y ||
            m_bgData.joinPos.z != captured.z ||
            m_bgData.joinPos.o != captured.orientation ||
            m_bgData.joinPos.mapId != captured.mapId ||
            InBattleGroundQueue() != captured.inQueue)
            return;
        m_bgData.m_needSave = false;
    });
    return true;
}

bool Player::AppendSpellCooldowns(CharacterPersistenceContext& context) const
{
    if (!GetSession() || !context.HasIdentityGuard(PlayerIdentity(*this)))
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

    static SqlStatementID deleteCooldowns;
    SqlStatement remove = database->CreateStatement(
        deleteCooldowns,
        "DELETE FROM `character_spell_cooldown` WHERE `guid` = ?");
    remove.addUInt32(GetGUIDLow());
    if (!remove.AppendToCheckedBatch(context.Batch(), SqlAffectedRowsPolicy::Any))
    {
        context.Invalidate();
        return false;
    }

    for (auto const& entry : m_cooldownMap)
    {
        CooldownData const* cooldown = entry.second.get();
        if (!cooldown)
        {
            context.Invalidate();
            return false;
        }
        if (cooldown->IsPermanent())
            continue;
        if (!cooldown->GetSpellEntry())
        {
            context.Invalidate();
            return false;
        }

        TimePoint spellTime = TimePoint::min();
        TimePoint categoryTime = TimePoint::min();
        cooldown->GetSpellCDExpireTime(spellTime);
        cooldown->GetCatCDExpireTime(categoryTime);
        uint64 const spellExpiry = uint64(Clock::to_time_t(spellTime));
        uint64 const categoryExpiry = uint64(Clock::to_time_t(categoryTime));

        static SqlStatementID insertCooldown;
        SqlStatement insert = database->CreateStatement(
            insertCooldown,
            "INSERT INTO `character_spell_cooldown` (`guid`, `spell`, `spell_expire_time`, `category`, `category_expire_time`, `item_id`) VALUES( ?, ?, ?, ?, ?, ?)");
        insert.addUInt32(GetGUIDLow());
        insert.addUInt32(cooldown->GetSpellEntry()->Id);
        insert.addUInt64(spellExpiry);
        insert.addUInt32(cooldown->GetCategory());
        insert.addUInt64(categoryExpiry);
        insert.addUInt32(cooldown->GetItemId());
        if (!insert.AppendToCheckedBatch(
                context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
        {
            context.Invalidate();
            return false;
        }
    }
    return true;
}

bool Player::AppendAuras(CharacterPersistenceContext& context)
{
    if (!GetSession() || !context.HasIdentityGuard(PlayerIdentity(*this)))
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

    static SqlStatementID deleteAuras;
    SqlStatement remove = database->CreateStatement(
        deleteAuras, "DELETE FROM `character_aura` WHERE `guid` = ?");
    remove.addUInt32(GetGUIDLow());
    if (!remove.AppendToCheckedBatch(context.Batch(), SqlAffectedRowsPolicy::Any))
    {
        context.Invalidate();
        return false;
    }

    static SqlStatementID insertAuras;
    for (auto const& entry : GetSpellAuraHolderMap())
    {
        AuraSaveStruct aura;
        if (!SaveAura(entry.second, aura))
            continue;
        SqlStatement insert = database->CreateStatement(
            insertAuras,
            "INSERT INTO `character_aura` (`guid`, `caster_guid`, `item_guid`, `spell`, `stacks`, `charges`, `base_points0`, `base_points1`, `base_points2`, `periodic_time0`, `periodic_time1`, `periodic_time2`, `max_duration`, `duration`, `effect_index_mask`) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)");
        insert.addUInt32(GetGUIDLow());
        insert.addUInt64(aura.casterGuid.GetRawValue());
        insert.addUInt32(aura.itemLowGuid);
        insert.addUInt32(aura.spellId);
        insert.addUInt32(aura.stacks);
        insert.addUInt8(aura.charges);
        for (float value : aura.damage)
            insert.addFloat(value);
        for (uint32 value : aura.periodicTime)
            insert.addUInt32(value);
        insert.addInt32(aura.maxDuration);
        insert.addInt32(aura.duration);
        insert.addInt8(aura.effIndexMask);
        if (!insert.AppendToCheckedBatch(
                context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
        {
            context.Invalidate();
            return false;
        }
    }
    return true;
}

bool Player::AppendStats(CharacterPersistenceContext& context,
                         uint32 minimumLevel)
{
    if (!GetSession() || !context.HasIdentityGuard(PlayerIdentity(*this)))
    {
        context.Invalidate();
        return false;
    }
    if (minimumLevel == 0 || GetLevel() < minimumLevel)
        return true;

    Database* database = context.Batch().GetDatabase();
    if (!database)
    {
        context.Invalidate();
        return false;
    }

    static SqlStatementID deleteStats;
    SqlStatement remove = database->CreateStatement(
        deleteStats, "DELETE FROM `character_stats` WHERE `guid` = ?");
    remove.addUInt32(GetGUIDLow());
    if (!remove.AppendToCheckedBatch(context.Batch(), SqlAffectedRowsPolicy::Any))
    {
        context.Invalidate();
        return false;
    }

    static SqlStatementID insertStats;
    SqlStatement insert = database->CreateStatement(
        insertStats,
        "INSERT INTO `character_stats` (`guid`, `max_health`, `max_power1`, `max_power2`, `max_power3`, `max_power4`, `max_power5`, `strength`, `agility`, `stamina`, `intellect`, `spirit`, `armor`, `holy_res`, `fire_res`, `nature_res`, `frost_res`, `shadow_res`, `arcane_res`, `block_chance`, `dodge_chance`, `parry_chance`, `crit_chance`, `ranged_crit_chance`, `spell_crit_chance`, `attack_power`, `ranged_attack_power`, `spell_damage`, `spell_healing`) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)");
    insert.addUInt32(GetGUIDLow());
    insert.addUInt32(GetMaxHealth());
    for (int i = 0; i < MAX_POWERS; ++i)
        insert.addUInt32(GetMaxPower(Powers(i)));
    for (int i = 0; i < MAX_STATS; ++i)
        insert.addFloat(GetStat(Stats(i)));
    for (int i = 0; i < MAX_SPELL_SCHOOL; ++i)
        insert.addInt32(GetResistance(SpellSchools(i)));
    insert.addFloat(GetFloatValue(PLAYER_BLOCK_PERCENTAGE));
    insert.addFloat(GetFloatValue(PLAYER_DODGE_PERCENTAGE));
    insert.addFloat(GetFloatValue(PLAYER_PARRY_PERCENTAGE));
    insert.addFloat(GetFloatValue(PLAYER_CRIT_PERCENTAGE));
    insert.addFloat(GetFloatValue(PLAYER_RANGED_CRIT_PERCENTAGE));
    insert.addFloat(GetSpellCritPercent(SPELL_SCHOOL_HOLY));
    insert.addUInt32(GetUInt32Value(UNIT_FIELD_ATTACK_POWER));
    insert.addUInt32(GetUInt32Value(UNIT_FIELD_RANGED_ATTACK_POWER));
    insert.addUInt32(GetUInt32Value(PLAYER_FIELD_MOD_DAMAGE_DONE_POS + 1));
    insert.addUInt32(GetTotalAuraModifier(SPELL_AURA_MOD_HEALING_DONE));
    if (!insert.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
    {
        context.Invalidate();
        return false;
    }
    return true;
}

bool ReputationMgr::AppendSaveToDB(CharacterPersistenceContext& context)
{
    if (!m_player || !context.HasIdentityGuard(PlayerIdentity(*m_player)))
    {
        context.Invalidate();
        return false;
    }

    struct FactionAck
    {
        uint32 key;
        uint32 id;
        uint32 reputationListId;
        int32 standing;
        uint32 flags;
    };
    std::vector<FactionAck> acknowledgments;

    Database* database = context.Batch().GetDatabase();
    if (!database)
    {
        context.Invalidate();
        return false;
    }

    for (auto const& entry : m_factions)
    {
        FactionState const& faction = entry.second;
        if (!faction.needSave)
            continue;

        static SqlStatementID deleteReputation;
        SqlStatement remove = database->CreateStatement(
            deleteReputation,
            "DELETE FROM `character_reputation` WHERE `guid` = ? AND `faction` = ?");
        remove.addUInt32(m_player->GetGUIDLow());
        remove.addUInt32(faction.ID);
        if (!remove.AppendToCheckedBatch(
                context.Batch(), SqlAffectedRowsPolicy::Any))
        {
            context.Invalidate();
            return false;
        }

        static SqlStatementID insertReputation;
        SqlStatement insert = database->CreateStatement(
            insertReputation,
            "INSERT INTO `character_reputation` (`guid`, `faction`, `standing`, `flags`) "
            "VALUES (?, ?, ?, ?)");
        insert.addUInt32(m_player->GetGUIDLow());
        insert.addUInt32(faction.ID);
        insert.addInt32(faction.Standing);
        insert.addUInt32(faction.Flags);
        if (!insert.AppendToCheckedBatch(
                context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
        {
            context.Invalidate();
            return false;
        }

        acknowledgments.push_back(
            FactionAck{uint32(entry.first), faction.ID,
                       faction.ReputationListID, faction.Standing,
                       faction.Flags});
    }

    CharacterIdentitySnapshot const identity = PlayerIdentity(*m_player);
    context.OnCommit([this, identity, acknowledgments]() {
        Player* owner = m_player;
        if (!owner || !PlayerIdentityMatches(*owner, identity))
            return;
        for (FactionAck const& ack : acknowledgments)
        {
            auto itr = m_factions.find(ack.key);
            if (itr == m_factions.end())
                continue;
            FactionState& faction = itr->second;
            if (faction.ID == ack.id &&
                faction.ReputationListID == ack.reputationListId &&
                faction.Standing == ack.standing &&
                faction.Flags == ack.flags)
                faction.needSave = false;
        }
    });
    return true;
}

bool WorldSession::AppendTutorialsDataToContext(
    CharacterPersistenceContext& context)
{
    Player* player = GetPlayer();
    if (!player || player->GetSession() != this ||
        !context.HasIdentityGuard(PlayerIdentity(*player)))
    {
        context.Invalidate();
        return false;
    }
    if (m_tutorialState == TUTORIALDATA_UNCHANGED)
        return true;

    Database* database = context.Batch().GetDatabase();
    if (!database)
    {
        context.Invalidate();
        return false;
    }

    uint32 captured[ACCOUNT_TUTORIALS_COUNT];
    for (size_t i = 0; i < ACCOUNT_TUTORIALS_COUNT; ++i)
        captured[i] = m_tutorials[i];
    CharacterIdentitySnapshot const identity = PlayerIdentity(*player);
    TutorialDataState const state = m_tutorialState;

    if (state == TUTORIALDATA_NEW)
    {
        static SqlStatementID insertTutorial;
        SqlStatement insert = database->CreateStatement(
            insertTutorial,
            "INSERT INTO `character_tutorial` (`account`, `tut0`, `tut1`, `tut2`, `tut3`, `tut4`, `tut5`, `tut6`, `tut7`) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)");
        insert.addUInt32(GetAccountId());
        for (uint32 value : captured)
            insert.addUInt32(value);
        if (!insert.AppendToCheckedBatch(
                context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
        {
            context.Invalidate();
            return false;
        }
    }
    else
    {
        static SqlStatementID updateTutorial;
        SqlStatement update = database->CreateStatement(
            updateTutorial,
            "UPDATE `character_tutorial` SET `tut0`=?, `tut1`=?, `tut2`=?, `tut3`=?, `tut4`=?, `tut5`=?, `tut6`=?, `tut7`=? WHERE `account` = ?");
        for (uint32 value : captured)
            update.addUInt32(value);
        update.addUInt32(GetAccountId());
        if (!update.AppendToCheckedBatch(
                context.Batch(), SqlAffectedRowsPolicy::Any))
        {
            context.Invalidate();
            return false;
        }
    }

    context.OnCommit([this, player, identity, captured, state]() {
        if (GetPlayer() != player || player->GetSession() != this ||
            !PlayerIdentityMatches(*player, identity))
            return;
        for (size_t i = 0; i < ACCOUNT_TUTORIALS_COUNT; ++i)
            if (m_tutorials[i] != captured[i])
            {
                m_tutorialState = TUTORIALDATA_CHANGED;
                return;
            }
        m_tutorialState = TUTORIALDATA_UNCHANGED;
        (void)state;
    });
    return true;
}
bool MasterPlayer::AppendActionsToDB(CharacterPersistenceContext& context)
{
    if (!GetSession() || !context.HasIdentityGuard(MasterIdentity(*this)))
    {
        context.Invalidate();
        return false;
    }

    struct ActionAck
    {
        uint8 button;
        uint32 packed;
        ActionButtonUpdateState state;
    };
    std::vector<ActionAck> acknowledgments;
    Database* database = context.Batch().GetDatabase();
    if (!database)
    {
        context.Invalidate();
        return false;
    }

    for (auto const& entry : m_actionButtons)
    {
        ActionButton const& action = entry.second;
        if (action.uState == ACTIONBUTTON_UNCHANGED)
            continue;

        static SqlStatementID insertAction;
        static SqlStatementID updateAction;
        static SqlStatementID deleteAction;

        if (action.uState == ACTIONBUTTON_NEW)
        {
            SqlStatement statement = database->CreateStatement(
                insertAction,
                "INSERT INTO `character_action` (`guid`, `button`, `action`, `type`) "
                "VALUES (?, ?, ?, ?)");
            statement.addUInt32(GetGUIDLow());
            statement.addUInt32(uint32(entry.first));
            statement.addUInt32(action.GetAction());
            statement.addUInt32(uint32(action.GetType()));
            if (!statement.AppendToCheckedBatch(
                    context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
            {
                context.Invalidate();
                return false;
            }
        }
        else if (action.uState == ACTIONBUTTON_CHANGED)
        {
            SqlStatement statement = database->CreateStatement(
                updateAction,
                "UPDATE `character_action` SET `action` = ?, `type` = ? "
                "WHERE `guid` = ? AND `button` = ?");
            statement.addUInt32(action.GetAction());
            statement.addUInt32(uint32(action.GetType()));
            statement.addUInt32(GetGUIDLow());
            statement.addUInt32(uint32(entry.first));
            if (!statement.AppendToCheckedBatch(
                    context.Batch(), SqlAffectedRowsPolicy::Any))
            {
                context.Invalidate();
                return false;
            }
        }
        else if (action.uState == ACTIONBUTTON_DELETED)
        {
            SqlStatement statement = database->CreateStatement(
                deleteAction,
                "DELETE FROM `character_action` WHERE `guid` = ? AND `button` = ?");
            statement.addUInt32(GetGUIDLow());
            statement.addUInt32(uint32(entry.first));
            if (!statement.AppendToCheckedBatch(
                    context.Batch(), SqlAffectedRowsPolicy::Any))
            {
                context.Invalidate();
                return false;
            }
        }
        else
            continue;

        acknowledgments.push_back(
            ActionAck{entry.first, action.packedData, action.uState});
    }

    CharacterIdentitySnapshot const identity = MasterIdentity(*this);
    context.OnCommit([this, identity, acknowledgments]() {
        if (!MasterIdentityMatches(*this, identity))
            return;
        for (ActionAck const& ack : acknowledgments)
        {
            auto itr = m_actionButtons.find(ack.button);
            if (ack.state == ACTIONBUTTON_DELETED)
            {
                if (itr == m_actionButtons.end())
                    continue;
                if (itr->second.uState == ACTIONBUTTON_DELETED)
                    m_actionButtons.erase(itr);
                else
                    itr->second.uState = ACTIONBUTTON_NEW;
                continue;
            }

            if (itr == m_actionButtons.end())
            {
                ActionButton tombstone;
                tombstone.packedData = ack.packed;
                tombstone.uState = ACTIONBUTTON_DELETED;
                m_actionButtons[ack.button] = tombstone;
                continue;
            }

            ActionButton& action = itr->second;
            if (action.uState == ACTIONBUTTON_DELETED)
                continue;
            if (action.packedData == ack.packed)
                action.uState = ACTIONBUTTON_UNCHANGED;
            else
                action.uState = ACTIONBUTTON_CHANGED;
        }
    });
    return true;
}
