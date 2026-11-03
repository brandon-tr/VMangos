/*
 * Copyright (C) 2005-2011 MaNGOS <http://getmangos.com/>
 * Copyright (C) 2009-2011 MaNGOSZero <https://github.com/mangos/zero>
 * Copyright (C) 2011-2016 Nostalrius <https://nostalrius.org>
 * Copyright (C) 2016-2017 Elysium Project <https://github.com/elysium-project>
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
 */

#include "Common.h"
#include "Database/DatabaseEnv.h"
#include "Database/CharacterPersistenceContext.h"
#include "Database/CharacterIdentityPersistence.h"
#include "Database/CharacterPersistenceRecovery.h"
#include "Database/SqlCheckedBatch.h"
#include "Database/SqlPreparedStatement.h"
#include "WorldPacket.h"
#include "SharedDefines.h"
#include "WorldSession.h"
#include "Opcodes.h"
#include "Log.h"
#include "World.h"
#include "ObjectMgr.h"
#include "PlayerName.h"
#include "DBCStores.h"
#include "Player.h"
#include "Guild.h"
#include "GuildMgr.h"
#include "ObjectAccessor.h"
#include "Group.h"
#include "Database/DatabaseImpl.h"
#include "SocialMgr.h"
#include "Util.h"
#include "Language.h"
#include "Anticheat.h"

#include <limits>
#include "MasterPlayer.h"
#include "PlayerBroadcaster.h"
#include "PlayerBotMgr.h"
#include "MapManager.h"
#include "AccountMgr.h"

class LoginQueryHolder : public SqlQueryHolder
{
private:
    uint32 m_accountId;
    ObjectGuid m_guid;
public:
    LoginQueryHolder(uint32 accountId, ObjectGuid guid)
        : SqlQueryHolder(guid.GetCounter()), m_accountId(accountId), m_guid(guid) { }
    ~LoginQueryHolder()
    {
        // Queries should NOT be deleted by user
        DeleteAllResults();
    }
    ObjectGuid GetGuid() const
    {
        return m_guid;
    }
    uint32 GetAccountId() const
    {
        return m_accountId;
    }
    bool Initialize();
};

bool LoginQueryHolder::Initialize()
{
    SetSize(MAX_PLAYER_LOGIN_QUERY);

    bool res = true;

    // NOTE: all fields in `characters` must be read to prevent lost character data at next save in case wrong DB structure.
    // !!! NOTE: including unused `zone`,`online`
    res &= SetPQuery(PLAYER_LOGIN_QUERY_LOADFROM,            "SELECT `guid`, `account`, `name`, `race`, `class`, `gender`, `level`, `xp`, `money`, `skin`, `face`, `hair_style`, `hair_color`, `facial_hair`, `bank_bag_slots`, `character_flags`, "
                     "`position_x`, `position_y`, `position_z`, `map`, `orientation`, `known_taxi_mask`, `played_time_total`, `played_time_level`, `rest_bonus`, `logout_time`, `reset_talents_multiplier`, "
                     "`reset_talents_time`, `transport_guid`, `transport_x`, `transport_y`, `transport_z`, `transport_o`, `extra_flags`, `stable_slots`, `death_expire_time`, `current_taxi_path`, "
                     "`honor_rank_points`, `honor_highest_rank`, `honor_standing`, `honor_last_week_hk`, `honor_last_week_cp`, `honor_stored_hk`, `honor_stored_dk`, "
                     "`watched_faction`, `drunk`, `health`, `power1`, `power2`, `power3`, `power4`, `power5`, `explored_zones`, `ammo_id`, `action_bars`, "
                     "`world_phase_mask`, `create_time`, `instance`, `identity_generation` FROM `characters` WHERE `guid` = '%u'", m_guid.GetCounter());
    res &= SetPQuery(PLAYER_LOGIN_QUERY_LOADGROUP,           "SELECT `group_id` FROM `group_member` WHERE `member_guid` ='%u'", m_guid.GetCounter());
    res &= SetPQuery(PLAYER_LOGIN_QUERY_LOADBOUNDINSTANCES,  "SELECT `id`, `permanent`, `map`, `reset_time` FROM `character_instance` LEFT JOIN `instance` ON `instance` = `id` WHERE `guid` = '%u'", m_guid.GetCounter());
    res &= SetPQuery(PLAYER_LOGIN_QUERY_LOADAURAS,           "SELECT `caster_guid`, `item_guid`, `spell`, `stacks`, `charges`, `base_points0`, `base_points1`, `base_points2`, `periodic_time0`, `periodic_time1`, `periodic_time2`, `max_duration`, `duration`, `effect_index_mask` FROM `character_aura` WHERE `guid` = '%u'", m_guid.GetCounter());
    res &= SetPQuery(PLAYER_LOGIN_QUERY_LOADSPELLS,          "SELECT `spell`, `active`, `disabled` FROM `character_spell` WHERE `guid` = '%u'", m_guid.GetCounter());
    res &= SetPQuery(PLAYER_LOGIN_QUERY_LOADQUESTSTATUS,     "SELECT `quest`, `status`, `rewarded`, `explored`, `timer`, `mob_count1`, `mob_count2`, `mob_count3`, `mob_count4`, `item_count1`, `item_count2`, `item_count3`, `item_count4`, `reward_choice` FROM `character_queststatus` WHERE `guid` = '%u'", m_guid.GetCounter());
    res &= SetPQuery(PLAYER_LOGIN_QUERY_LOADHONORCP,         "SELECT `victim_type`, `victim_id`, `cp`, `date`, `type` FROM `character_honor_cp` WHERE `guid` = '%u'", m_guid.GetCounter());
    res &= SetPQuery(PLAYER_LOGIN_QUERY_LOADREPUTATION,      "SELECT `faction`, `standing`, `flags` FROM `character_reputation` WHERE `guid` = '%u'", m_guid.GetCounter());
    res &= SetPQuery(PLAYER_LOGIN_QUERY_LOADINVENTORY,       "SELECT * FROM (SELECT `creator_guid`, `gift_creator_guid`, `count`, `duration`, `charges`, `flags`, `enchantments`, `random_property_id`, `durability`, `text`, `bag`, `slot`, `item_guid`, `item_instance`.`item_id`, `generated_loot` FROM `character_inventory` JOIN `item_instance` ON `character_inventory`.`item_guid` = `item_instance`.`guid` WHERE `character_inventory`.`guid` = '%u') as t ORDER BY `bag`, `slot`", m_guid.GetCounter());
    res &= SetPQuery(PLAYER_LOGIN_QUERY_LOADITEMLOOT,        "SELECT `guid`, `item_id`, `amount`, `property` FROM `item_loot` WHERE `owner_guid` = '%u'", m_guid.GetCounter());
    res &= SetPQuery(PLAYER_LOGIN_QUERY_LOADACTIONS,         "SELECT `button`, `action`, `type` FROM `character_action` WHERE `guid` = '%u' ORDER BY `button`", m_guid.GetCounter());
    res &= SetPQuery(PLAYER_LOGIN_QUERY_LOADSOCIALLIST,      "SELECT `friend`, `flags` FROM `character_social` WHERE `guid` = '%u' LIMIT 255", m_guid.GetCounter());
    res &= SetPQuery(PLAYER_LOGIN_QUERY_LOADHOMEBIND,        "SELECT `map`, `zone`, `position_x`, `position_y`, `position_z` FROM `character_homebind` WHERE `guid` = '%u'", m_guid.GetCounter());
    res &= SetPQuery(PLAYER_LOGIN_QUERY_LOADSPELLCOOLDOWNS,  "SELECT `spell`, `spell_expire_time`, `category`, `category_expire_time`, `item_id` FROM `character_spell_cooldown` WHERE `guid` = '%u'", m_guid.GetCounter());
    res &= SetPQuery(PLAYER_LOGIN_QUERY_LOADGUILD,           "SELECT `guild_id`, `rank` FROM `guild_member` WHERE `guid` = '%u'", m_guid.GetCounter());
    res &= SetPQuery(PLAYER_LOGIN_QUERY_LOADBGDATA,          "SELECT `instance_id`, `team`, `join_x`, `join_y`, `join_z`, `join_o`, `join_map` FROM `character_battleground_data` WHERE `guid` = '%u'", m_guid.GetCounter());
    res &= SetPQuery(PLAYER_LOGIN_QUERY_LOADACCOUNTDATA,     "SELECT `type`, `time`, `data` FROM `character_account_data` WHERE `guid`='%u'", m_guid.GetCounter());
    res &= SetPQuery(PLAYER_LOGIN_QUERY_LOADSKILLS,          "SELECT `skill`, `value`, `max` FROM `character_skills` WHERE `guid` = '%u'", m_guid.GetCounter());
    res &= SetPQuery(PLAYER_LOGIN_QUERY_LOADMAILS,           "SELECT `id`, `message_type`, `sender_guid`, `receiver_guid`, `subject`, `item_text_id`, `expire_time`, `deliver_time`, `money`, `cod`, `checked`, `stationery`, `mail_template_id`, `has_items` FROM `mail` WHERE `receiver_guid` = '%u' ORDER BY `id` DESC", m_guid.GetCounter());
    res &= SetPQuery(PLAYER_LOGIN_QUERY_LOADMAILEDITEMS,     "SELECT `creator_guid`, `gift_creator_guid`, `count`, `duration`, `charges`, `flags`, `enchantments`, `random_property_id`, `durability`, `text`, `mail_id`, `item_guid`, `item_instance`.`item_id`, `generated_loot` FROM `mail_items` JOIN `item_instance` ON `item_guid` = `guid` WHERE `receiver_guid` = '%u'", m_guid.GetCounter());
    res &= SetPQuery(PLAYER_LOGIN_QUERY_FORGOTTEN_SKILLS,    "SELECT `skill`, `value` FROM `character_forgotten_skills` WHERE `guid` = '%u'", m_guid.GetCounter());

    return res;
}

// don't call WorldSession directly
// it may get deleted before the query callbacks get executed
// instead pass an account id to this handler
class CharacterHandler
{
public:
    void HandleCharEnumCallback(std::unique_ptr<QueryResult> result, uint32 account)
    {
        WorldSession* session = sWorld.FindSession(account);
        if (!session)
        {
            return;
        }
        session->HandleCharEnum(std::move(result));
    }
    static void HandlePlayerLoginCallback(std::unique_ptr<QueryResult> /*dummy*/, SqlQueryHolder* holder, WorldSession::LoginAdmissionRequest request)
    {
        if (!holder || !request.holder || holder != request.holder.get())
            return;

        WorldSession* session = sWorld.FindSession(
            static_cast<LoginQueryHolder*>(holder)->GetAccountId());
        if (!session || session->GetGUID() != request.sessionGuid ||
            !request.lease || !request.lease->IsActive() ||
            !GetAccountLifecycleRegistry().Allows(
                *request.lease, session->GetAccountId(),
                AccountLifecycleKind::Mutation))
            return;
        session->HandlePlayerLogin(std::move(request.holder),
                                 std::move(request.lease));
    }
} chrHandler;

void WorldSession::HandleCharEnum(std::unique_ptr<QueryResult> result)
{
    WorldPacket data(SMSG_CHAR_ENUM, 100);                  // we guess size

    uint8 num = 0;
    data << num;

    if (result)
    {
        do
        {
            uint32 guidlow = (*result)[0].GetUInt32();
            uint32 level   = (*result)[10].GetUInt32();
            if (m_characterMaxLevel < level)
                m_characterMaxLevel = level;

            sLog.Out(LOG_BASIC, LOG_LVL_DETAIL, "Build enum data for char guid %u from account %u.", guidlow, GetAccountId());
            if (Player::BuildEnumData(result, &data))
                ++num;
        }
        while (result->NextRow());
    }

    data.put<uint8>(0, num);
    m_charactersCount = num;

    SendPacket(&data);
}

void WorldSession::HandleCharEnumOpcode(NullClientPacket const& /*packet*/)
{
    // get all the data necessary for loading all characters (along with their pets) on the account
    CharacterDatabase.AsyncPQuery(&chrHandler, &CharacterHandler::HandleCharEnumCallback, GetAccountId(),
                                  //           0                    1                    2                    3                     4                      5                    6                    7                          8                          9                           10
                                  "SELECT `characters`.`guid`, `characters`.`name`, `characters`.`race`, `characters`.`class`, `characters`.`gender`, `characters`.`skin`, `characters`.`face`, `characters`.`hair_style`, `characters`.`hair_color`, `characters`.`facial_hair`, `characters`.`level`, "
                                  //    11                   12                  13                         14                         15                         16                         17
                                  "`characters`.`zone`, `characters`.`map`, `characters`.`position_x`, `characters`.`position_y`, `characters`.`position_z`, `guild_member`.`guild_id`, `characters`.`character_flags`, "
                                  //    18                                19                       20                            21                       22
                                  "`characters`.`played_time_total`, `character_pet`.`entry`, `character_pet`.`display_id`, `character_pet`.`level`, `characters`.`equipment_cache` "
                                  "FROM `characters` LEFT JOIN `character_pet` ON `characters`.`guid`=`character_pet`.`owner_guid` AND `character_pet`.`slot`='%u' "
                                  "LEFT JOIN `guild_member` ON `characters`.`guid` = `guild_member`.`guid` "
                                  "WHERE `characters`.`account` = '%u' ORDER BY `characters`.`create_time`, `characters`.`guid` "
                                  "LIMIT 0,10",
                                  PET_SAVE_AS_CURRENT, GetAccountId());
}

void WorldSession::HandleCharCreateOpcode(WorldPackets::Character::CharCreate const& packet)
{
    auto sendResponse = [this](uint8 result) {
        auto response = std::make_unique<WorldPackets::Character::CharCreateResponse>();
        response->result = result;
        SendPacket(std::move(response));
    };

    if (GetSecurity() == SEC_PLAYER)
    {
        if (uint32 mask = sWorld.getConfig(CONFIG_UINT32_CHARACTERS_CREATING_DISABLED))
        {
            bool disabled = false;

            Team team = Player::TeamForRace(packet.race);
            switch (team)
            {
                case ALLIANCE:
                    disabled = mask & (1 << 0);
                    break;
                case HORDE:
                    disabled = mask & (1 << 1);
                    break;
            }

            if (disabled)
            {
                sendResponse(CHAR_CREATE_DISABLED);
                return;
            }
        }
    }

    ChrClassesEntry const* classEntry = sChrClassesStore.LookupEntry(packet.class_);
    ChrRacesEntry const* raceEntry = sChrRacesStore.LookupEntry(packet.race);

    if (!classEntry || !raceEntry)
    {
        sendResponse(CHAR_CREATE_FAILED);
        std::stringstream oss;
        oss << "Attempt to create character of invalid Class (" << int(packet.class_) << ") or Race (" << int(packet.race) << ")";
        ProcessAnticheatAction("PassiveAnticheat", oss.str().c_str(), CHEAT_ACTION_LOG);
        return;
    }

    if (raceEntry->HasFlag(CHRRACES_FLAGS_NOT_PLAYABLE))
    {
        sendResponse(CHAR_CREATE_DISABLED);
        std::stringstream oss;
        oss << "Attempt to create character of non-playable Race (" << int(packet.race) << ")";
        ProcessAnticheatAction("PassiveAnticheat", oss.str().c_str(), CHEAT_ACTION_LOG);
        return;
    }

    if (!Player::ValidateAppearance(packet.race, packet.gender, packet.hairStyle, packet.hairColor, packet.face, packet.facialHair, packet.skin))
    {
        sendResponse(CHAR_CREATE_FAILED);
        ProcessAnticheatAction("PassiveAnticheat", "Attempt to create character with invalid appearance attributes", CHEAT_ACTION_LOG);
        return;
    }

    std::string safeName = packet.name;

    PlayerNameCharsets const charsets = PlayerNameCharsets::ForClient(
        GetClientRegion(), static_cast<std::size_t>(GetClientLocale()));
    uint8 res = prepareFullPlayerName(safeName, GetPlayerNameLists(), charsets);
    if (res != CHAR_NAME_SUCCESS)
    {
        if (safeName.empty() || safeName.find(' ') == std::string::npos)
            ProcessAnticheatAction("PassiveAnticheat", "Attempt to create character with invalid name", CHEAT_ACTION_LOG);
        sendResponse(res);
        return;
    }

    AccountLifecycleLeasePtr admission =
        GetAccountLifecycleRegistry().TryAcquire(
            {GetAccountId()}, AccountLifecycleKind::Mutation);
    if (!admission)
    {
        sendResponse(CHAR_CREATE_ERROR);
        return;
    }

    if (GetSecurity() == SEC_PLAYER && sObjectMgr.IsReservedName(safeName))
    {
        sendResponse(CHAR_NAME_RESERVED);
        return;
    }

    if (sObjectMgr.GetPlayerGuidByName(safeName))
    {
        sendResponse(CHAR_CREATE_NAME_IN_USE);
        return;
    }

    uint32 authoritativeCount = 0;
    if (!sAccountMgr.TryGetCharactersCount(GetAccountId(), authoritativeCount))
    {
        sendResponse(CHAR_CREATE_ERROR);
        return;
    }
    if (authoritativeCount >= sWorld.getConfig(CONFIG_UINT32_CHARACTERS_PER_REALM))
    {
        sendResponse(CHAR_CREATE_SERVER_LIMIT);
        return;
    }

    bool AllowTwoSideAccounts = !sWorld.IsPvPRealm() || sWorld.getConfig(CONFIG_BOOL_ALLOW_TWO_SIDE_ACCOUNTS) || GetSecurity() > SEC_PLAYER;

    if (!AllowTwoSideAccounts)
    {
        std::vector<PlayerCacheSnapshot> characters;
        sObjectMgr.GetPlayerDataForAccount(GetAccountId(), characters);

        if (!characters.empty())
        {
            PlayerCacheSnapshot cData = characters.front();
            Team team_ = Player::TeamForRace(packet.race);

            uint8 acc_race = cData->uiRace;

            // need to check team only for first character
            // TODO: what to if account already has characters of both races?
            if (!AllowTwoSideAccounts)
            {
                if (acc_race == 0 || Player::TeamForRace(acc_race) != team_)
                {
                    sendResponse(CHAR_CREATE_PVP_TEAMS_VIOLATION);
                    return;
                }
            }
        }
    }

    uint32 const guidLow = sObjectMgr.GeneratePlayerLowGuid();
    uint8 const createResult = Player::SaveNewPlayer(this, guidLow, safeName, packet.race, packet.class_, packet.gender, packet.skin, packet.face, packet.hairStyle, packet.hairColor, packet.facialHair, admission.get());
    if (createResult == CHAR_CREATE_SUCCESS)
    {
        uint32 publishedCount = 0;
        if (!sWorld.UpdateRealmCharCount(
                GetAccountId(), admission.get(), &publishedCount))
        {
            sendResponse(CHAR_CREATE_ERROR);
            return;
        }
        m_charactersCount = publishedCount;
        sendResponse(CHAR_CREATE_SUCCESS);
    }
    else
    {
        sendResponse(createResult);
    }
}

void WorldSession::HandleCharDeleteOpcode(WorldPackets::Character::CharDelete const& packet)
{
    auto sendResponse = [this](uint8 result) {
        auto response = std::make_unique<WorldPackets::Character::CharDeleteResponse>();
        response->result = result;
        SendPacket(std::move(response));
    };

    // can't delete loaded character
    if (ObjectAccessor::FindPlayerNotInWorld(packet.guid))
        return;

    uint32 accountId = 0;
    std::string name;

    // is guild leader
    if (sGuildMgr.GetGuildByLeader(packet.guid))
    {
        sendResponse(CHAR_DELETE_FAILED);
        return;
    }

    uint32 lowguid = packet.guid.GetCounter();

    PlayerCacheSnapshot cacheData = sObjectMgr.GetPlayerDataByGUID(lowguid);
    if (!cacheData)
        return; // Character not found

    accountId = cacheData->uiAccount;
    name = cacheData->sName;

    // prevent deleting other players' characters using cheating tools
    if (accountId != GetAccountId())
        return;

    sLog.Player(this, LOG_CHAR, "Delete", LOG_LVL_BASIC, "Character %s guid %u", name.c_str(), packet.guid);

    // If the character is online (ALT-F4 logout for example)
    if (Player* onlinePlayer = sObjectAccessor.FindPlayer(packet.guid))
        onlinePlayer->GetSession()->LogoutPlayer(true);

    if (!Player::DeleteFromDB(packet.guid, GetAccountId()))
    {
        sendResponse(CHAR_DELETE_FAILED);
        return;
    }
    sendResponse(CHAR_DELETE_SUCCESS);
}

void WorldSession::HandlePlayerLoginOpcode(WorldPackets::Character::PlayerLogin const& packet)
{
    if ((!sWorld.getConfig(CONFIG_BOOL_WORLD_AVAILABLE) && GetSecurity() == SEC_PLAYER) ||
        PlayerLoading() || GetPlayer() != nullptr || !packet.guid.IsPlayer())
    {
        auto loginFailedPacket = std::make_unique<WorldPackets::Character::CharacterLoginFailed>();
        loginFailedPacket->result = 1;
        SendPacket(std::move(loginFailedPacket));
        return;
    }

    if (IsCharacterRecoveryBlocked(packet.guid.GetCounter()))
    {
        auto loginFailedPacket = std::make_unique<WorldPackets::Character::CharacterLoginFailed>();
        loginFailedPacket->result = 1;
        SendPacket(std::move(loginFailedPacket));
        return;
    }

    AccountLifecycleLeasePtr admission =
        GetAccountLifecycleRegistry().TryAcquire(
            {GetAccountId()}, AccountLifecycleKind::Mutation);
    if (!admission)
        return;
    auto holder = std::make_shared<LoginQueryHolder>(GetAccountId(), packet.guid);
    if (!holder->Initialize())
        return;
    m_playerLoading = true;
    if (!CharacterDatabase.DelayQueryHolderUnsafe(
            &CharacterHandler::HandlePlayerLoginCallback, holder.get(),
            LoginAdmissionRequest{GetGUID(), admission, holder}))
    {
        m_playerLoading = false;
    }
}

void WorldSession::LoginPlayer(ObjectGuid loginPlayerGuid)
{
    ASSERT(loginPlayerGuid.IsPlayer());
    if (IsCharacterRecoveryBlocked(loginPlayerGuid.GetCounter()))
        return;
    AccountLifecycleLeasePtr admission =
        GetAccountLifecycleRegistry().TryAcquire(
            {GetAccountId()}, AccountLifecycleKind::Mutation);
    if (!admission)
        return;
    auto holder = std::make_shared<LoginQueryHolder>(GetAccountId(), loginPlayerGuid);
    if (!holder->Initialize())
        return;
    m_playerLoading = true;
    if (!CharacterDatabase.DelayQueryHolderUnsafe(
            &CharacterHandler::HandlePlayerLoginCallback, holder.get(),
            LoginAdmissionRequest{GetGUID(), admission, holder}))
    {
        m_playerLoading = false;
    }
}

void WorldSession::HandlePlayerLogin(std::shared_ptr<LoginQueryHolder> holder, AccountLifecycleLeasePtr lease)
{
    if (!holder)
    {
        m_playerLoading = false;
        return;
    }
    if (!lease || !lease->IsActive() ||
        !GetAccountLifecycleRegistry().Allows(
            *lease, GetAccountId(), AccountLifecycleKind::Mutation))
    {
        m_playerLoading = false;
        return;
    }

    // The following fixes a crash. Use case:
    // Session1 created, requests login, kicked.
    // Session2 created, requests login, and receives 2 login callback.
    if (GetPlayer() || !m_playerLoading)
    {
        sLog.Out(LOG_BASIC, LOG_LVL_MINIMAL, "[CRASH] HandlePlayerLogin on session %u with player %s [loading=%u]", GetAccountId(), GetPlayerName(), m_playerLoading);
        m_playerLoading = false;
        return;
    }

    ObjectGuid playerGuid = holder->GetGuid();
    ASSERT(playerGuid.IsPlayer());
    m_currentPlayerGuid = playerGuid;

    // If the character is online (ALT-F4 logout for example)
    Player* pCurrChar = sObjectAccessor.FindPlayer(playerGuid);
    MasterPlayer* pCurrMasterPlayer = sObjectAccessor.FindMasterPlayer(playerGuid);
    bool alreadyOnline = false;
    if (pCurrChar)
    {
        // Hacking attempt
        if (pCurrChar->GetSession()->GetAccountId() != GetAccountId())
        {
            ProcessAnticheatAction("PassiveAnticheat", "Attempt to login to character on different account", CHEAT_ACTION_LOG);
            KickPlayer();
            m_playerLoading = false;
            return;
        }

        if (pCurrChar->FindMap() != sMapMgr.FindMap(pCurrChar->GetMapId(), pCurrChar->GetInstanceId()))
        {
            sLog.Out(LOG_BASIC, LOG_LVL_MINIMAL, "[CRASH] Dangling map pointer during login on character guid %u", playerGuid.GetCounter());
            KickPlayer();
            m_playerLoading = false;
            return;
        }

        alreadyOnline = true;
        pCurrChar->GetSession()->SetPlayer(nullptr);
        pCurrChar->SetSession(this);

        // Need to attach packet bcaster to the new socket
        if (pCurrChar->m_broadcaster)
            pCurrChar->m_broadcaster->ChangeSocket(GetSocket());
        else
            pCurrChar->CreatePacketBroadcaster();

        // If the character had a logout request, then he is articifially stunned (in CMSG_LOGOUT_REQUEST handler). Fix it here.
        if (pCurrChar->CanFreeMove())
        {
            pCurrChar->SetRootedReal(false);
            pCurrChar->SetStandState(UNIT_STAND_STATE_STAND);
            pCurrChar->RemoveFlag(UNIT_FIELD_FLAGS, UNIT_FLAG_STUNNED);
        }
    }
    else
    {
        // Character found online but not in world ?
        if (HashMapHolder<Player>::Find(playerGuid))
        {
            sLog.Out(LOG_BASIC, LOG_LVL_MINIMAL, "[CRASH] Trying to login already ingame character guid %u", playerGuid.GetCounter());
            KickPlayer();
            m_playerLoading = false;
            return;
        }
        pCurrChar = new Player(this);
        pCurrChar->GetMotionMaster()->Initialize();

        if (GetBot() && !sPlayerBotMgr.IsSavingAllowed())
            pCurrChar->m_saveDisabled = true;
    }

    // "GetAccountId()==db stored account id" checked in LoadFromDB (prevent login not own character using cheating tools)
    if (alreadyOnline)
        pCurrChar->SendPacketsAtRelogin();
    else if (!pCurrChar->LoadFromDB(playerGuid, holder.get()))
    {
        KickPlayer();                                       // disconnect client, player no set to session and it will not deleted or saved at kick
        delete pCurrChar;                                   // delete it manually
        m_playerLoading = false;
        return;
    }

    ASSERT(pCurrChar->GetSession() == this);
    SetPlayer(pCurrChar);
    if (pCurrMasterPlayer)
    {
        pCurrMasterPlayer->GetSession()->SetMasterPlayer(nullptr);
        pCurrMasterPlayer->SetSession(this);
        m_masterPlayer = pCurrMasterPlayer;
    }
    else
    {
        m_masterPlayer = new MasterPlayer(this);
        m_masterPlayer->LoadPlayer(GetPlayer());
        m_masterPlayer->LoadActions(holder->TakeResult(PLAYER_LOGIN_QUERY_LOADACTIONS));
        m_masterPlayer->LoadSocial(holder->TakeResult(PLAYER_LOGIN_QUERY_LOADSOCIALLIST));
        m_masterPlayer->LoadMails(holder->TakeResult(PLAYER_LOGIN_QUERY_LOADMAILS), lease.get());
        m_masterPlayer->LoadMailedItems(holder->TakeResult(PLAYER_LOGIN_QUERY_LOADMAILEDITEMS));
    }
    m_masterPlayer->UpdateNextMailTimeAndUnreads();

    sObjectAccessor.AddObject(m_masterPlayer);

    Position const& position = pCurrChar->GetTransport()
                             ? pCurrChar->m_movementInfo.GetTransportPos()
                             : pCurrChar->GetPosition();

    auto loginVerifyWorld = std::make_unique<WorldPackets::Character::LoginVerifyWorld>();
    loginVerifyWorld->location = position.WithMapId(pCurrChar->GetMapId());
    SendPacket(std::move(loginVerifyWorld));

    // load player specific part before send times
    LoadAccountData(holder->TakeResult(PLAYER_LOGIN_QUERY_LOADACCOUNTDATA), NewAccountData::PER_CHARACTER_CACHE_MASK);
    SendAccountDataTimes();

    pCurrChar->GetSocial()->SendFriendList();
    pCurrChar->GetSocial()->SendIgnoreList();

    // Send MOTD (1.12.1 not have SMSG_MOTD, so do it in another way)
    {
        uint32 linecount = 0;
        std::string str_motd = sWorld.GetMotd();
        std::string::size_type pos, nextpos;
        std::string motd;

        pos = 0;
        while ((nextpos = str_motd.find('@', pos)) != std::string::npos)
        {
            if (nextpos != pos)
            {
                pCurrChar->PSendSysMessage(str_motd.substr(pos, nextpos - pos).c_str());
                ++linecount;
            }
            pos = nextpos + 1;
        }

        if (pos < str_motd.length())
        {
            pCurrChar->PSendSysMessage(str_motd.substr(pos).c_str());
            ++linecount;
        }
    }

    if (Guild* guild = sGuildMgr.GetGuildById(pCurrChar->GetGuildId()))
    {
        auto guildEvent = std::make_unique<WorldPackets::Guild::GuildEvent>();
        guildEvent->event = GE_MOTD;
        guildEvent->params.push_back(guild->GetMOTD());
        SendPacket(std::move(guildEvent));

        guild->BroadcastEvent(GE_SIGNED_ON, pCurrChar->GetObjectGuid(), pCurrChar->GetName());
    }

    if (char const* warning = sAccountMgr.GetWarningText(GetAccountId()))
    {
        pCurrChar->PSendSysMessage(LANG_ACCOUNT_WARNED, warning);
        SendNotification("WARNING: %s", warning);
    }

    if (!pCurrChar->IsAlive())
        pCurrChar->SendCorpseReclaimDelay(true);

    pCurrChar->SendInitialPacketsBeforeAddToMap();
    GetMasterPlayer()->SendInitialActionButtons();

    // Show cinematic at the first time that player login
    if (pCurrChar->m_playedTime[PLAYED_TIME_TOTAL] == 0 && !sWorld.getConfig(CONFIG_BOOL_SKIP_CINEMATICS))
    {
        if (ChrRacesEntry const* rEntry = sChrRacesStore.LookupEntry(pCurrChar->GetRace()))
            pCurrChar->SendCinematicStart(rEntry->CinematicSequence);
    }

    if (!alreadyOnline && !pCurrChar->GetMap()->Add(pCurrChar))
    {
        // normal delayed teleport protection not applied (and this correct) for this case (Player object just created)
        AreaTriggerTeleport const* at = sObjectMgr.GetGoBackTrigger(pCurrChar->GetMapId());
        if (at)
            pCurrChar->TeleportTo(at->destination);
        else if (pCurrChar->GetMapId() == MAP_NAXXRAMAS)
            pCurrChar->TeleportTo(MAP_EASTERN_KINGDOMS, 3362.15f, -3379.35f, 144.782f, 6.28319f); // Naxxramas has no exit trigger
        else
            pCurrChar->TeleportToHomebind();

        sMapMgr.ExecuteSingleDelayedTeleport(pCurrChar);
    }

    if (alreadyOnline)
        pCurrChar->GetMap()->ExistingPlayerLogin(pCurrChar); // SendInitSelf ...
    else
        sObjectAccessor.AddObject(pCurrChar);

    //sLog.Out(LOG_BASIC, LOG_LVL_DEBUG, "Player %s added to Map.",pCurrChar->GetName());

    pCurrChar->SendInitialPacketsAfterAddToMap();
    if (alreadyOnline)
        pCurrChar->SendInitWorldStates(pCurrChar->GetCachedZoneId());

    static SqlStatementID updChars;
    static SqlStatementID updAccount;

    SqlStatement stmt = CharacterDatabase.CreateStatement(updChars, "UPDATE `characters` SET `online` = 1 WHERE `guid` = ?");
    stmt.PExecute(pCurrChar->GetGUIDLow());

    stmt = LoginDatabase.CreateStatement(updAccount, "UPDATE `account` SET current_realm = ?, online = 1 WHERE id = ?");
    stmt.PExecute(realmID, GetAccountId());

    pCurrChar->SetInGameTime(WorldTimer::getMSTime());

    // announce group about member online (must be after add to player list to receive announce to self)
    if (Group* group = pCurrChar->GetGroup())
        group->UpdatePlayerOnlineStatus(pCurrChar);

    // friend status
    // TODO: Call it when node finished loading also
    if (GetMasterPlayer())
    {
        GetMasterPlayer()->areaId = pCurrChar->GetCachedAreaId();
        GetMasterPlayer()->zoneId = pCurrChar->GetCachedZoneId();
        sSocialMgr.SendFriendStatus(GetMasterPlayer(), FRIEND_ONLINE, GetMasterPlayer()->GetObjectGuid(), true);
    }

    if (!alreadyOnline)
    {
        // Place character in world (and load zone) before some object loading
        pCurrChar->LoadCorpse();

        // setting Ghost+speed if dead
        if (pCurrChar->m_deathState != ALIVE)
        {
            // not blizz like, we must correctly save and load player instead...
            pCurrChar->ApplyGhostForm();
        }
    }

    // Load pet if any (if player not alive and in taxi flight or another then pet will remember as temporary unsummoned)
    if (alreadyOnline)
    {
        pCurrChar->PetSpellInitialize();
        pCurrChar->SendMirrorTimers(true);
    }
    else
    {
        pCurrChar->ContinueTaxiFlight();
        pCurrChar->LoadPet();
    }

    if (pCurrChar->HasFlag(PLAYER_FLAGS, PLAYER_FLAGS_CONTESTED_PVP))
        pCurrChar->UpdatePvPContested(true);

    if (pCurrChar->HasCharacterFlag(CHARACTER_FLAG_RESET_TALENTS_ON_LOGIN))
    {
        pCurrChar->ResetTalents(true);
        SendNotification(LANG_RESET_TALENTS);               // we can use SMSG_TALENTS_INVOLUNTARILY_RESET here
    }

    // show time before shutdown if shutdown planned.
    if (sWorld.IsShutdowning())
        sWorld.ShutdownMsg(true, pCurrChar);

    if (sWorld.getConfig(CONFIG_BOOL_ALL_TAXI_PATHS))
        pCurrChar->SetTaxiCheater(true);

    if (pCurrChar->IsGameMaster())
        SendNotification(LANG_GM_ON);

    if (!pCurrChar->IsGMVisible())
        SendNotification(LANG_INVISIBLE_INVISIBLE, pCurrChar->GetGMInvisibilityLevel());

    std::string IP_str = GetRemoteAddress();

    sLog.Player(this, LOG_CHAR, "Login", LOG_LVL_DETAIL, alreadyOnline ? "Player was already online" : "");

    if (!alreadyOnline && !pCurrChar->IsStandingUp() && !pCurrChar->HasUnitState(UNIT_STATE_STUNNED))
        pCurrChar->SetStandState(UNIT_STAND_STATE_STAND);

    m_playerLoading = false;

    if (alreadyOnline)
    {
        pCurrChar->UpdateControl();
        // Send "Release spirit" timer, etc ...
        if (pCurrChar->GetDeathState() == CORPSE)
            pCurrChar->KillPlayer();
    }
    pCurrChar->RestorePendingTeleport();

    sObjectMgr.UpdatePlayerCachedPosition(pCurrChar);

    if (sWorld.getConfig(CONFIG_BOOL_SEND_LOOT_ROLL_UPON_RECONNECT) && alreadyOnline)
        if (Group* pGroup = pCurrChar->GetGroup())
            pGroup->SendLootStartRollsForPlayer(pCurrChar);
}

void WorldSession::HandleSetFactionAtWarOpcode(WorldPackets::Misc::SetFactionAtWar const& packet)
{
    Player* pPlayer = GetPlayer();

    if (pPlayer->IsInCombat())
        return;

    pPlayer->GetReputationMgr().SetAtWar(packet.repListId, packet.flag);
}

void WorldSession::HandleTutorialFlagOpcode(WorldPackets::Misc::TutorialFlag const& packet)
{
    uint32 wInt = (packet.iFlag / 32);
    if (wInt >= 8)
        return;

    uint32 rInt = (packet.iFlag % 32);

    uint32 tutflag = GetTutorialInt(wInt);
    tutflag |= (1 << rInt);
    SetTutorialInt(wInt, tutflag);
}

void WorldSession::HandleTutorialClearOpcode(NullClientPacket const& /*packet*/)
{
    for (uint32 iI = 0; iI < 8; ++iI)
        SetTutorialInt(iI, 0xFFFFFFFF);
}

void WorldSession::HandleTutorialResetOpcode(NullClientPacket const& /*packet*/)
{
    for (uint32 iI = 0; iI < 8; iI++)
        SetTutorialInt(iI, 0x00000000);
}

#if SUPPORTED_CLIENT_BUILD > CLIENT_BUILD_1_9_4
void WorldSession::HandleSetWatchedFactionOpcode(WorldPackets::Misc::SetWatchedFaction const& packet)
{
    GetPlayer()->SetInt32Value(PLAYER_FIELD_WATCHED_FACTION_INDEX, packet.repId);
}

void WorldSession::HandleSetFactionInactiveOpcode(WorldPackets::Misc::SetFactionInactive const& packet)
{
    _player->GetReputationMgr().SetInactive(packet.replistid, packet.inactive);
}
#endif

void WorldSession::HandleShowingHelmOpcode(NullClientPacket const& /*packet*/)
{
    _player->ToggleFlag(PLAYER_FLAGS, PLAYER_FLAGS_HIDE_HELM);
}

void WorldSession::HandleShowingCloakOpcode(NullClientPacket const& /*packet*/)
{
    _player->ToggleFlag(PLAYER_FLAGS, PLAYER_FLAGS_HIDE_CLOAK);
}

void WorldSession::HandleCharRenameOpcode(WorldPackets::Character::CharRename const& packet)
{
    if (!packet.guid.IsPlayer() || GetPlayer() || PlayerLoading())
    {
        auto response = std::make_unique<WorldPackets::Character::CharRenameResponse>();
        response->result = CHAR_CREATE_ERROR;
        SendPacket(std::move(response));
        return;
    }

    std::string newName = packet.newname;
    PlayerNameCharsets const charsets = PlayerNameCharsets::ForClient(
        GetClientRegion(), static_cast<std::size_t>(GetClientLocale()));
    uint8 res = prepareFullPlayerName(newName, GetPlayerNameLists(), charsets);
    if (res != CHAR_NAME_SUCCESS)
    {
        auto response = std::make_unique<WorldPackets::Character::CharRenameResponse>();
        response->result = res;
        SendPacket(std::move(response));
        return;
    }

    // check name limitations
    if (GetSecurity() == SEC_PLAYER && sObjectMgr.IsReservedName(newName))
    {
        auto response = std::make_unique<WorldPackets::Character::CharRenameResponse>();
        response->result = CHAR_NAME_RESERVED;
        SendPacket(std::move(response));
        return;
    }

    AccountLifecycleLeasePtr admission =
        GetAccountLifecycleRegistry().TryAcquire(
            {GetAccountId()}, AccountLifecycleKind::Mutation);
    if (!admission)
    {
        auto response = std::make_unique<WorldPackets::Character::CharRenameResponse>();
        response->result = CHAR_CREATE_ERROR;
        SendPacket(std::move(response));
        return;
    }

    std::string escaped_newname = newName;
    CharacterDatabase.escape_string(escaped_newname);

    // make sure that the character belongs to the current account, that rename at login is enabled
    // and that there is no character with the desired new name
    if (!CharacterDatabase.AsyncPQuery(&WorldSession::HandleChangePlayerNameOpcodeCallBack,
                                  GetAccountId(), GetGUID(),
                                  CharacterRenameRequest{packet.guid.GetCounter(), newName, admission},
                                  "SELECT `guid`, `name`, `identity_generation` FROM `characters` WHERE `guid` = %u AND `account` = %u AND (`character_flags` & %u) = %u AND `deleted_time` IS NULL AND NOT EXISTS (SELECT NULL FROM `character_name_ownership` WHERE `canonical_name` = '%s' AND `guid` <> %u)",
                                  packet.guid.GetCounter(), GetAccountId(), CHARACTER_FLAG_RENAME, CHARACTER_FLAG_RENAME, escaped_newname.c_str(), packet.guid.GetCounter()
                                 ))
    {
        auto response = std::make_unique<WorldPackets::Character::CharRenameResponse>();
        response->result = CHAR_CREATE_ERROR;
        SendPacket(std::move(response));
    }
}

void WorldSession::HandleChangePlayerNameOpcodeCallBack(std::unique_ptr<QueryResult> result, uint32 accountId, uint32 sessionGuid, CharacterRenameRequest request)
{
    bool valid = false;
    uint32 guidLow = 0;
    std::string oldname;
    uint64 generation = 0;

    if (result && result->GetRowCount() == 1 && result->GetFieldCount() >= 3)
    {
        if (Field* fields = result->Fetch())
        {
            guidLow = fields[0].GetUInt32();
            oldname = fields[1].GetCppString();
            generation = fields[2].GetUInt64();
            valid = guidLow != 0 && guidLow == request.guid &&
                    generation > 0 &&
                    generation < std::numeric_limits<uint64>::max() &&
                    isCanonicalFullPlayerName(oldname) &&
                    isCanonicalFullPlayerName(request.newName);
        }
    }

    sWorld.GetMessager().AddMessage(
        [accountId, sessionGuid, request, valid, guidLow, oldname, generation](World* world)
        {
            WorldSession* session = world->FindSession(accountId);
            if (!session || session->GetGUID() != sessionGuid)
                return;

            auto failure = [session]()
            {
                auto response = std::make_unique<WorldPackets::Character::CharRenameResponse>();
                response->result = CHAR_CREATE_ERROR;
                session->SendPacket(std::move(response));
            };

            if (!valid || !request.lease || !request.lease->IsActive() ||
                !GetAccountLifecycleRegistry().Allows(
                    *request.lease, accountId, AccountLifecycleKind::Mutation) ||
                session->GetPlayer() || session->PlayerLoading())
            {
                failure();
                return;
            }

            ObjectGuid guid = ObjectGuid(HIGHGUID_PLAYER, guidLow);
            CharacterPersistenceContext context(CharacterDatabase, guidLow);

            static SqlStatementID updateCharacter;
            SqlStatement character = CharacterDatabase.CreateStatement(
                updateCharacter,
                "UPDATE `characters` SET `name`=?, "
                "`character_flags`=(`character_flags` & ~?) | ?, "
                "`identity_generation`=`identity_generation`+1 "
                "WHERE `guid`=? AND `account`=? AND BINARY `name`=BINARY ? "
                "AND `identity_generation`=? AND `deleted_time` IS NULL "
                "AND (`character_flags` & ?) = ? "
                "AND `identity_generation` < 18446744073709551615 "
                "AND EXISTS (SELECT 1 FROM `character_name_ownership` "
                "WHERE `guid`=? AND BINARY `canonical_name`=BINARY ?)");
            character.addString(request.newName);
            character.addUInt32(uint32(CHARACTER_FLAG_RENAME));
            character.addUInt32(uint32(CHARACTER_FLAG_RENAME_NEEDS_GM_REVIEW));
            character.addUInt32(guidLow);
            character.addUInt32(accountId);
            character.addString(oldname);
            character.addUInt64(generation);
            character.addUInt32(uint32(CHARACTER_FLAG_RENAME));
            character.addUInt32(uint32(CHARACTER_FLAG_RENAME));
            character.addUInt32(guidLow);
            character.addString(oldname);
            if (!character.AppendToCheckedBatch(
                    context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
            {
                failure();
                return;
            }

            if (request.newName != oldname)
            {
                static SqlStatementID updateOwnership;
                SqlStatement ownership = CharacterDatabase.CreateStatement(
                    updateOwnership,
                    "UPDATE `character_name_ownership` SET `canonical_name`=? "
                    "WHERE `guid`=? AND BINARY `canonical_name`=BINARY ?");
                ownership.addString(request.newName);
                ownership.addUInt32(guidLow);
                ownership.addString(oldname);
                if (!ownership.AppendToCheckedBatch(
                        context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
                {
                    failure();
                    return;
                }
            }

            SqlBatchResult const batch = context.Execute();
            CharacterNameWriteStatus const status =
                ClassifyCharacterNameClaim(batch, 1);
            if (status != CharacterNameWriteStatus::Committed)
            {
                if (status == CharacterNameWriteStatus::OutcomeUnknown)
                    BlockCharacterRecovery(CharacterRecoveryRecord{
                        CharacterRecoveryKind::Rename, {guidLow}, {accountId},
                        generation, "rename outcome unknown"});
                auto response = std::make_unique<WorldPackets::Character::CharRenameResponse>();
                response->result =
                    status == CharacterNameWriteStatus::NameInUse
                        ? CHAR_CREATE_NAME_IN_USE : CHAR_CREATE_ERROR;
                session->SendPacket(std::move(response));
                return;
            }

            if (!sObjectMgr.ChangePlayerNameInCache(guidLow, oldname, request.newName))
            {
                if (!sObjectMgr.LoadPlayerCacheData(guidLow))
                {
                    BlockCharacterRecovery(CharacterRecoveryRecord{
                        CharacterRecoveryKind::Rename, {guidLow}, {accountId},
                        generation,
                        "rename committed but cache publication failed"});
                    sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                             "Character rename committed but cache publication failed for guid %u",
                             guidLow);
                    failure();
                    return;
                }
            }

            sLog.Player(session->GetAccountId(), LOG_CHAR, LOG_LVL_BASIC,
                "Account: %d (IP: %s) Character:[%s] (guid:%u) Changed name to: %s",
                session->GetAccountId(), session->GetRemoteAddress().c_str(),
                oldname.c_str(), guidLow, request.newName.c_str());

            auto response = std::make_unique<WorldPackets::Character::CharRenameResponse>();
            response->result = RESPONSE_SUCCESS;
            response->renamedCharacterGuid = guid;
            response->newName = request.newName;
            session->SendPacket(std::move(response));

            sWorld.InvalidatePlayerDataToAllClient(guid);
        });
}
