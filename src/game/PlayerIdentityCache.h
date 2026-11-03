#ifndef PLAYER_IDENTITY_CACHE_H
#define PLAYER_IDENTITY_CACHE_H

#include "Common.h"

#include <cstddef>
#include <cstdint>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

class Database;

struct PlayerCacheData
{
    uint32 uiGuid = 0;
    uint32 uiLevel = 0;
    uint32 uiAccount = 0;
    uint32 uiRace = 0;
    uint32 uiClass = 0;
    uint32 uiGender = 0;
    uint32 uiZoneId = 0;
    uint32 uiMapId = 0;
    std::string sName;
    float fPosX = 0.0f;
    float fPosY = 0.0f;
    float fPosZ = 0.0f;
    float fOrientation = 0.0f;
    bool bInFlight = false;
};

using PlayerCacheSnapshot = std::shared_ptr<PlayerCacheData const>;

class PlayerIdentityCache
{
    public:
        PlayerCacheSnapshot Find(uint32 guid) const;
        PlayerCacheSnapshot FindName(std::string const& exactName) const;
        std::vector<PlayerCacheSnapshot> FindAccount(uint32 accountId) const;

        PlayerCacheSnapshot Upsert(PlayerCacheData value);
        bool Erase(uint32 guid);
        bool Rename(uint32 guid, std::string const& expectedOldName,
                    std::string const& newName);
        bool UpdateIfIdentityMatches(PlayerCacheData value);
        bool UpdatePosition(uint32 guid, uint32 mapId, float x, float y,
                            float z, float orientation, bool inFlight);
        bool UpdateLevelAndZone(uint32 guid, uint32 level, uint32 zone);
        bool ReplaceAll(std::vector<PlayerCacheData> values);
        std::size_t Size() const;

        bool Reload(Database& database, uint32 guid, std::string& reason);

    private:
        mutable std::mutex m_mutex;
        std::map<uint32, PlayerCacheSnapshot> m_byGuid;
        std::map<std::string, uint32> m_byName;
};

#endif
