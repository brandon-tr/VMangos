#ifndef FN05H_TEST_PLAYER_H
#define FN05H_TEST_PLAYER_H

#include "Common.h"
#include "Progression.h"
#include "QuestDef.h"
#include "Database/CharacterDatabaseCache.h"
#include "SpellCaster.h"
#include "WorldSession.h"

#include <map>
#include <string>
#include <unordered_map>
#include <vector>

class CharacterPersistenceContext;

class WorldSession;

enum Team { TEAM_NONE = 0, TEAM_HORDE = 1, TEAM_ALLIANCE = 2 };
enum BattleGroundTypeId { BATTLEGROUND_TYPE_NONE = 0, BATTLEGROUND_AV = 1 };

struct WorldLocation
{
    float x = 0.0f;
    float y = 0.0f;
    float z = 0.0f;
    float o = 0.0f;
    uint32 mapId = 0;
};

struct BGData
{
    uint32 bgInstanceID = 0;
    BattleGroundTypeId bgTypeID = BATTLEGROUND_TYPE_NONE;
    Team bgTeam = TEAM_NONE;
    WorldLocation joinPos;
    bool m_needSave = false;
};

class SpellAuraHolder
{
    public:
        bool admitted = false;
        AuraSaveStruct saved;
};

enum PlayerSpellState
{
    PLAYERSPELL_UNCHANGED = 0,
    PLAYERSPELL_CHANGED = 1,
    PLAYERSPELL_NEW = 2,
    PLAYERSPELL_REMOVED = 3
};

struct PlayerSpell
{
    PlayerSpellState state : 8;
    bool active : 1;
    bool dependent : 1;
    bool disabled : 1;
    PlayerSpell() : state(PLAYERSPELL_NEW), active(false),
                    dependent(false), disabled(false) {}
};

typedef std::unordered_map<uint32, PlayerSpell> PlayerSpellMap;

enum SkillUpdateState
{
    SKILL_UNCHANGED = 0,
    SKILL_CHANGED = 1,
    SKILL_NEW = 2,
    SKILL_DELETED = 3
};

struct SkillStatusData
{
    SkillStatusData(uint8 _pos, SkillUpdateState _uState)
        : pos(_pos), uState(_uState) {}
    uint8 pos;
    SkillUpdateState uState;
};

typedef std::unordered_map<uint32, SkillStatusData> SkillStatusMap;

#define PLAYER_SKILL_INFO_1_1 0
#define MAX_POWERS 5
#define MAX_STATS 5
#define MAX_SPELL_SCHOOL 7
#define PLAYER_BLOCK_PERCENTAGE 100
#define PLAYER_DODGE_PERCENTAGE 101
#define PLAYER_PARRY_PERCENTAGE 102
#define PLAYER_CRIT_PERCENTAGE 103
#define PLAYER_RANGED_CRIT_PERCENTAGE 104
#define UNIT_FIELD_ATTACK_POWER 105
#define UNIT_FIELD_RANGED_ATTACK_POWER 106
#define PLAYER_FIELD_MOD_DAMAGE_DONE_POS 107
#define SPELL_AURA_MOD_HEALING_DONE 108

enum Powers
{
    POWER_MANA = 0, POWER_RAGE = 1, POWER_FOCUS = 2,
    POWER_ENERGY = 3, POWER_HAPPINESS = 4
};
enum Stats
{
    STAT_STRENGTH = 0, STAT_AGILITY = 1, STAT_STAMINA = 2,
    STAT_INTELLECT = 3, STAT_SPIRIT = 4
};
enum SpellSchools
{
    SPELL_SCHOOL_NORMAL = 0, SPELL_SCHOOL_HOLY = 1,
    SPELL_SCHOOL_FIRE = 2, SPELL_SCHOOL_NATURE = 3,
    SPELL_SCHOOL_FROST = 4, SPELL_SCHOOL_SHADOW = 5,
    SPELL_SCHOOL_ARCANE = 6
};

class Player
{
    public:
        uint32 GetGUIDLow() const { return guid; }
        WorldSession* GetSession() const { return session; }
        char const* GetName() const { return name.c_str(); }
        uint64 GetIdentityGeneration() const { return generation; }
        bool InBattleGroundQueue() const { return inQueue; }
        bool AppendBGData(CharacterPersistenceContext& context);
        bool AppendQuestStatus(CharacterPersistenceContext& context,
                               uint64 gameTimeSeconds);
        bool AppendSkills(CharacterPersistenceContext& context);
        bool AppendSpells(CharacterPersistenceContext& context);
        bool AppendSpellCooldowns(CharacterPersistenceContext& context) const;
        bool AppendAuras(CharacterPersistenceContext& context);
        bool AppendStats(CharacterPersistenceContext& context,
                         uint32 minimumLevel);

        uint32 GetUInt32Value(uint32 index) const { return values.at(index); }
        uint32 GetLevel() const { return level; }
        uint32 GetMaxHealth() const { return maxHealth; }
        uint32 GetMaxPower(Powers power) const { return maxPower[uint32(power)]; }
        float GetStat(Stats stat) const { return stats[uint32(stat)]; }
        int32 GetResistance(SpellSchools school) const
        {
            return resistances[uint32(school)];
        }
        float GetFloatValue(uint32 index) const { return floats.at(index); }
        float GetSpellCritPercent(uint32) const { return spellCrit; }
        uint32 GetTotalAuraModifier(uint32) const { return healing; }
        std::map<uint32, SpellAuraHolder*> const& GetSpellAuraHolderMap() const
        {
            return auraHolders;
        }
        bool SaveAura(SpellAuraHolder const* holder, AuraSaveStruct& out);

        uint32 guid = 0;
        WorldSession* session = nullptr;
        std::string name;
        uint64 generation = 0;
        bool inQueue = false;
        BGData m_bgData;

        QuestStatusMap mQuestStatus;
        SkillStatusMap m_skillStatusMap;
        std::unordered_map<uint16, uint16> m_forgottenSkills;
        PlayerSpellMap m_spells;
        CooldownContainer m_cooldownMap;
        std::map<uint32, SpellAuraHolder*> auraHolders;

        std::map<uint32, uint32> values;
        std::map<uint32, float> floats;
        uint32 level = 60;
        uint32 maxHealth = 1000;
        uint32 maxPower[MAX_POWERS] = {0};
        float stats[MAX_STATS] = {0};
        int32 resistances[MAX_SPELL_SCHOOL] = {0};
        float spellCrit = 0.0f;
        uint32 healing = 0;
};

#endif
