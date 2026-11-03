#include "PlayerIdentityCache.h"

#include "Database/Database.h"
#include "Database/Field.h"
#include "Database/QueryResult.h"
#include "PlayerName.h"

#include <limits>
#include <memory>
#include <string>
#include <utility>

namespace
{
bool ValidValue(PlayerCacheData const& value)
{
    return value.uiGuid != 0 && !value.sName.empty() &&
           value.sName.find('\0') == std::string::npos;
}

bool ValidName(std::string const& name)
{
    return !name.empty() && name.find('\0') == std::string::npos;
}

PlayerCacheSnapshot MakeSnapshot(PlayerCacheData value)
{
    return std::make_shared<PlayerCacheData const>(std::move(value));
}

bool ReadCount(std::unique_ptr<QueryResult> const& result, uint64& count)
{
    if (!result || result->GetRowCount() != 1 ||
        result->GetFieldCount() != 1)
        return false;
    Field* field = result->Fetch();
    if (!field || field->IsNULL())
        return false;

    char const* text = field->GetString();
    if (!text || !*text)
        return false;

    uint64 parsed = 0;
    for (char const* current = text; *current; ++current)
    {
        unsigned char const byte = static_cast<unsigned char>(*current);
        if (byte < '0' || byte > '9')
            return false;

        uint64 const digit = byte - '0';
        if (parsed > (std::numeric_limits<uint64>::max() - digit) / 10)
            return false;
        parsed = parsed * 10 + digit;
    }

    count = parsed;
    return true;
}
}

PlayerCacheSnapshot PlayerIdentityCache::Find(uint32 guid) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    auto itr = m_byGuid.find(guid);
    return itr == m_byGuid.end() ? PlayerCacheSnapshot() : itr->second;
}

PlayerCacheSnapshot PlayerIdentityCache::FindName(
    std::string const& exactName) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    auto nameItr = m_byName.find(exactName);
    if (nameItr == m_byName.end())
        return PlayerCacheSnapshot();
    auto guidItr = m_byGuid.find(nameItr->second);
    return guidItr == m_byGuid.end() ? PlayerCacheSnapshot() : guidItr->second;
}

std::vector<PlayerCacheSnapshot> PlayerIdentityCache::FindAccount(
    uint32 accountId) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    std::vector<PlayerCacheSnapshot> result;
    for (auto const& entry : m_byGuid)
        if (entry.second->uiAccount == accountId)
            result.push_back(entry.second);
    return result;
}

PlayerCacheSnapshot PlayerIdentityCache::Upsert(PlayerCacheData value)
{
    if (!ValidValue(value))
        return PlayerCacheSnapshot();

    std::lock_guard<std::mutex> lock(m_mutex);
    auto nameItr = m_byName.find(value.sName);
    if (nameItr != m_byName.end() && nameItr->second != value.uiGuid)
        return PlayerCacheSnapshot();

    PlayerCacheSnapshot snapshot = MakeSnapshot(std::move(value));
    auto old = m_byGuid.find(snapshot->uiGuid);
    if (old != m_byGuid.end() && old->second->sName != snapshot->sName)
    {
        auto oldName = m_byName.find(old->second->sName);
        if (oldName != m_byName.end() && oldName->second == snapshot->uiGuid)
            m_byName.erase(oldName);
    }

    m_byGuid[snapshot->uiGuid] = snapshot;
    m_byName[snapshot->sName] = snapshot->uiGuid;
    return snapshot;
}

bool PlayerIdentityCache::Erase(uint32 guid)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    auto itr = m_byGuid.find(guid);
    if (itr == m_byGuid.end())
        return false;

    auto nameItr = m_byName.find(itr->second->sName);
    if (nameItr != m_byName.end() && nameItr->second == guid)
        m_byName.erase(nameItr);
    m_byGuid.erase(itr);
    return true;
}

bool PlayerIdentityCache::Rename(uint32 guid,
                                 std::string const& expectedOldName,
                                 std::string const& newName)
{
    if (guid == 0 || !ValidName(expectedOldName) || !ValidName(newName))
        return false;

    std::lock_guard<std::mutex> lock(m_mutex);
    auto itr = m_byGuid.find(guid);
    if (itr == m_byGuid.end() || itr->second->sName != expectedOldName)
        return false;

    if (newName == expectedOldName)
    {
        m_byName[newName] = guid;
        return true;
    }

    auto destination = m_byName.find(newName);
    if (destination != m_byName.end() && destination->second != guid)
        return false;

    PlayerCacheData value = *itr->second;
    value.sName = newName;
    PlayerCacheSnapshot snapshot = MakeSnapshot(std::move(value));
    auto oldName = m_byName.find(expectedOldName);
    if (oldName != m_byName.end() && oldName->second == guid)
        m_byName.erase(oldName);
    m_byGuid[guid] = snapshot;
    m_byName[newName] = guid;
    return true;
}

bool PlayerIdentityCache::UpdateIfIdentityMatches(PlayerCacheData value)
{
    if (!ValidValue(value))
        return false;

    std::lock_guard<std::mutex> lock(m_mutex);
    auto itr = m_byGuid.find(value.uiGuid);
    if (itr == m_byGuid.end() ||
        itr->second->uiAccount != value.uiAccount ||
        itr->second->sName != value.sName)
        return false;

    m_byGuid[value.uiGuid] = MakeSnapshot(std::move(value));
    return true;
}

bool PlayerIdentityCache::UpdatePosition(uint32 guid, uint32 mapId, float x,
                                         float y, float z, float orientation,
                                         bool inFlight)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    auto itr = m_byGuid.find(guid);
    if (itr == m_byGuid.end())
        return false;

    PlayerCacheData value = *itr->second;
    value.uiMapId = mapId;
    value.fPosX = x;
    value.fPosY = y;
    value.fPosZ = z;
    value.fOrientation = orientation;
    value.bInFlight = inFlight;
    m_byGuid[guid] = MakeSnapshot(std::move(value));
    return true;
}

bool PlayerIdentityCache::UpdateLevelAndZone(uint32 guid, uint32 level,
                                             uint32 zone)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    auto itr = m_byGuid.find(guid);
    if (itr == m_byGuid.end())
        return false;

    PlayerCacheData value = *itr->second;
    value.uiLevel = level;
    value.uiZoneId = zone;
    m_byGuid[guid] = MakeSnapshot(std::move(value));
    return true;
}

bool PlayerIdentityCache::ReplaceAll(std::vector<PlayerCacheData> values)
{
    std::map<uint32, PlayerCacheSnapshot> byGuid;
    std::map<std::string, uint32> byName;

    for (PlayerCacheData const& value : values)
    {
        if (!ValidValue(value))
            return false;
        if (byGuid.find(value.uiGuid) != byGuid.end() ||
            byName.find(value.sName) != byName.end())
            return false;

        PlayerCacheSnapshot snapshot = MakeSnapshot(value);
        byGuid[value.uiGuid] = snapshot;
        byName[value.sName] = value.uiGuid;
    }

    std::lock_guard<std::mutex> lock(m_mutex);
    m_byGuid.swap(byGuid);
    m_byName.swap(byName);
    return true;
}

std::size_t PlayerIdentityCache::Size() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_byGuid.size();
}

bool PlayerIdentityCache::Reload(Database& database, uint32 guid,
                                 std::string& reason)
{
    reason.clear();

    std::string where =
        " WHERE `deleted_time` IS NULL AND `account` <> 0";
    if (guid != 0)
        where += " AND `guid`=" + std::to_string(guid);

    std::unique_ptr<QueryResult> countResult = database.Query(
        "SELECT COUNT(*) FROM `characters`" + where);
    uint64 expected = 0;
    if (!ReadCount(countResult, expected))
    {
        reason = "character cache count query failed";
        return false;
    }

    if (expected == 0)
    {
        if (guid != 0)
            Erase(guid);
        else if (!ReplaceAll({}))
        {
            reason = "character cache empty replacement failed";
            return false;
        }
        reason.clear();
        return true;
    }

    std::unique_ptr<QueryResult> result = database.Query(
        "SELECT `guid`, `race`, `class`, `gender`, `account`, `name`, "
        "`level`, `zone`, `map`, `position_x`, `position_y`, `position_z`, "
        "`orientation`, `current_taxi_path` FROM `characters`" + where);
    if (!result || result->GetFieldCount() != 14 ||
        result->GetRowCount() != expected)
    {
        reason = "character cache data query failed";
        return false;
    }

    std::vector<PlayerCacheData> values;
    std::map<uint32, bool> seenGuids;
    std::map<std::string, bool> seenNames;
    for (uint64 i = 0; i < expected; ++i)
    {
        if (i != 0 && !result->NextRow())
        {
            reason = "character cache data row missing";
            return false;
        }

        Field* fields = result->Fetch();
        if (!fields)
        {
            reason = "character cache data row invalid";
            return false;
        }

        PlayerCacheData value;
        value.uiGuid = fields[0].GetUInt32();
        value.uiRace = fields[1].GetUInt32();
        value.uiClass = fields[2].GetUInt32();
        value.uiGender = fields[3].GetUInt32();
        value.uiAccount = fields[4].GetUInt32();
        value.sName = fields[5].GetCppString();
        value.uiLevel = fields[6].GetUInt32();
        value.uiZoneId = fields[7].GetUInt32();
        value.uiMapId = fields[8].GetUInt32();
        value.fPosX = fields[9].GetFloat();
        value.fPosY = fields[10].GetFloat();
        value.fPosZ = fields[11].GetFloat();
        value.fOrientation = fields[12].GetFloat();
        value.bInFlight = !fields[13].GetCppString().empty();

        if (value.uiGuid == 0 || !isCanonicalFullPlayerName(value.sName))
        {
            reason = "character cache active identity is malformed";
            return false;
        }
        if (guid != 0 && value.uiGuid != guid)
        {
            reason = "character cache single reload returned another guid";
            return false;
        }
        if (seenGuids.find(value.uiGuid) != seenGuids.end() ||
            seenNames.find(value.sName) != seenNames.end())
        {
            reason = "character cache contains duplicate identities";
            return false;
        }
        seenGuids[value.uiGuid] = true;
        seenNames[value.sName] = true;
        values.push_back(std::move(value));
    }

    if (guid == 0)
    {
        if (!ReplaceAll(std::move(values)))
        {
            reason = "character cache replacement was rejected";
            return false;
        }
    }
    else if (!Upsert(std::move(values.front())))
    {
        reason = "character cache single replacement was rejected";
        return false;
    }

    reason.clear();
    return true;
}
