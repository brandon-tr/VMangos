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

#include "WorldPacket.h"
#include "WorldSession.h"
#include "Opcodes.h"
#include "Log.h"
#include "World.h"
#include "ObjectMgr.h"
#include "ObjectGuid.h"
#include "Player.h"
#include "AuctionHouseMgr.h"
#include "TransactionLog.h"
#include "Mail.h"
#include "Mail/MailPersistence.h"
#include "Util.h"
#include "Chat.h"
#include "Anticheat.h"
#include "Database/CharacterPersistenceContext.h"
#include "Database/CharacterPersistenceRecovery.h"
#include "Database/DatabaseEnv.h"
#include "Database/SqlPreparedStatement.h"

namespace
{
CharacterIdentitySnapshot AuctionHandlerIdentity(Player const& player)
{
    CharacterIdentitySnapshot identity;
    identity.guid = player.GetGUIDLow();
    identity.accountId = player.GetSession() ? player.GetSession()->GetAccountId() : 0;
    identity.canonicalName = player.GetName();
    identity.generation = player.GetIdentityGeneration();
    return identity;
}
}

// please DO NOT use iterator++, because it is slower than ++iterator!!!
// post-incrementation is always slower than pre-incrementation !

// void called when player click on auctioneer npc
void WorldSession::HandleAuctionHelloOpcode(WorldPackets::AuctionHouse::AuctionHello const& packet)
{
    Creature* unit = GetPlayer()->GetNPCIfCanInteractWith(packet.auctioneerGuid, UNIT_NPC_FLAG_AUCTIONEER);
    if (!unit)
    {
        sLog.Out(LOG_BASIC, LOG_LVL_DEBUG, "WORLD: HandleAuctionHelloOpcode - %s not found or you can't interact with him.", packet.auctioneerGuid.GetString().c_str());
        return;
    }

    // remove fake death
    if (GetPlayer()->HasUnitState(UNIT_STATE_FEIGN_DEATH))
        GetPlayer()->RemoveSpellsCausingAura(SPELL_AURA_FEIGN_DEATH);

    SendAuctionHello(unit);
}

// this void causes that auction window is opened
void WorldSession::SendAuctionHello(Unit* unit)
{
    // always return pointer
    AuctionHouseEntry const* ahEntry = AuctionHouseMgr::GetAuctionHouseEntry(unit);

    auto helloResponse = std::make_unique<WorldPackets::AuctionHouse::AuctionHelloResponse>();
    helloResponse->auctioneerGuid = unit->GetObjectGuid();
    helloResponse->houseId = ahEntry->houseId;
    SendPacket(std::move(helloResponse));
}

// call this method when player bids, creates, or deletes auction
void WorldSession::SendAuctionCommandResult(AuctionEntry* auc, AuctionAction Action, AuctionError ErrorCode, InventoryResult invError)
{
    auto packet = std::make_unique<WorldPackets::AuctionHouse::AuctionCommandResult>();
    packet->auctionId = auc ? auc->Id : 0;
    packet->action = Action;
    packet->errorCode = ErrorCode;

    switch (ErrorCode)
    {
        case AUCTION_OK:
            if (Action == AUCTION_BID_PLACED)
                packet->auctionOutBid = auc->GetAuctionOutBid();    // new AuctionOutBid?
            break;
        case AUCTION_ERR_INVENTORY:
            packet->inventoryError = invError;
            break;
        case AUCTION_ERR_HIGHER_BID:
            packet->newBidderGuid = ObjectGuid(HIGHGUID_PLAYER, auc->bidder); // new bidder guid
            packet->newBid = auc->bid;                       // new bid
            packet->auctionOutBid = auc->GetAuctionOutBid(); // new AuctionOutBid?
            break;
        default:
            break;
    }

    SendPacket(std::move(packet));
}

// this function sends notification, if bidder is online
void WorldSession::SendAuctionBidderNotification(AuctionEntry* auction, bool won)
{
    auto notification = std::make_unique<WorldPackets::AuctionHouse::AuctionBidderNotification>();
    notification->houseId = auction->GetHouseId();
    notification->auctionId = auction->Id;
    notification->bidderGuid = ObjectGuid(HIGHGUID_PLAYER, auction->bidder);
    // if 0, client shows ERR_AUCTION_WON_S, else ERR_AUCTION_OUTBID_S
    notification->bidOrZero = won ? 0 : auction->bid;
    notification->outBid = auction->GetAuctionOutBid();
    notification->itemTemplate = auction->itemTemplate;

    Item *item = sAuctionMgr.GetAItem(auction->itemGuidLow);
    notification->randomPropertyId = item ? item->GetItemRandomPropertyId() : 0;

    SendPacket(std::move(notification));
}

// this void causes on client to display: "Your auction sold"
void WorldSession::SendAuctionOwnerNotification(AuctionEntry* auction, bool sold)
{
    auto notification = std::make_unique<WorldPackets::AuctionHouse::AuctionOwnerNotification>();
    notification->auctionId = auction->Id;
    notification->bid = auction->bid;
    notification->outBid = auction->GetAuctionOutBid();

    if (!sold)                                               // not sold yet
        notification->bidderGuid = ObjectGuid(HIGHGUID_PLAYER, auction->bidder);

    notification->itemTemplate = auction->itemTemplate;

    Item *item = sAuctionMgr.GetAItem(auction->itemGuidLow);
    notification->randomPropertyId = item ? item->GetItemRandomPropertyId() : 0;

    SendPacket(std::move(notification));
}

// shows ERR_AUCTION_REMOVED_S
void WorldSession::SendAuctionRemovedNotification(AuctionEntry* auction)
{
    Item *item = sAuctionMgr.GetAItem(auction->itemGuidLow);

    auto packet = std::make_unique<WorldPackets::AuctionHouse::AuctionRemovedNotification>();
    packet->auctionId = auction->Id;
    packet->itemTemplate = auction->itemTemplate;
    packet->randomPropertyId = item ? item->GetItemRandomPropertyId() : 0;
    SendPacket(std::move(packet));
}

// this function sends mail to old bidder
void WorldSession::SendAuctionOutbiddedMail(AuctionEntry* auction)
{
    ObjectGuid oldBidder_guid = ObjectGuid(HIGHGUID_PLAYER, auction->bidder);
    Player* oldBidder = sObjectMgr.GetPlayer(oldBidder_guid);

    uint32 oldBidder_accId = 0;
    if (!oldBidder)
        oldBidder_accId = sObjectMgr.GetPlayerAccountIdByGUID(oldBidder_guid);

    // old bidder exist
    if (oldBidder || oldBidder_accId)
    {
        std::ostringstream msgAuctionOutbiddedSubject;
        msgAuctionOutbiddedSubject << auction->itemTemplate << ":0:" << AUCTION_OUTBIDDED;

        if (oldBidder)
            oldBidder->GetSession()->SendAuctionBidderNotification(auction, false);

        MailDraft(msgAuctionOutbiddedSubject.str())
        .SetMoney(auction->bid)
        .SendMailTo(MailReceiver(oldBidder, oldBidder_guid), auction, MAIL_CHECK_MASK_COPIED);
    }
}

// Stages the outbid refund in the caller's checked unit.
static bool AppendAuctionOutbiddedMail(CharacterPersistenceContext& context,
                                       AuctionEntry* auction,
                                       uint32 oldBidderGuid, uint32 oldBid)
{
    ObjectGuid oldBidder_guid(HIGHGUID_PLAYER, oldBidderGuid);
    Player* oldBidder = sObjectMgr.GetPlayer(oldBidder_guid);
    uint32 oldBidder_accId = oldBidder ? 0
        : sObjectMgr.GetPlayerAccountIdByGUID(oldBidder_guid);
    if (!oldBidder && !oldBidder_accId)
        return true;                                    // no recipient

    std::ostringstream subject;
    subject << auction->itemTemplate << ":0:" << AUCTION_OUTBIDDED;
    uint32 mailId = 0;
    if (!MailDraft(subject.str()).SetMoney(oldBid).AppendSendTo(
            context, MailReceiver(oldBidder, oldBidder_guid), auction,
            MAIL_CHECK_MASK_COPIED, 0, 0, mailId))
        return false;
    context.OnCommit([oldBidder, auction]() {
        if (oldBidder)
            oldBidder->GetSession()->SendAuctionBidderNotification(auction, false);
    });
    return true;
}

// this function sends mail, when auction is cancelled to old bidder
void WorldSession::SendAuctionCancelledToBidderMail(AuctionEntry* auction)
{
    ObjectGuid bidder_guid = ObjectGuid(HIGHGUID_PLAYER, auction->bidder);
    Player* bidder = sObjectMgr.GetPlayer(bidder_guid);

    uint32 bidder_accId = 0;
    if (!bidder)
        bidder_accId = sObjectMgr.GetPlayerAccountIdByGUID(bidder_guid);

    // bidder exist
    if (bidder || bidder_accId)
    {
        std::ostringstream msgAuctionCancelledSubject;
        msgAuctionCancelledSubject << auction->itemTemplate << ":0:" << AUCTION_CANCELLED_TO_BIDDER;

        if (bidder)
            bidder->GetSession()->SendAuctionRemovedNotification(auction);

        MailDraft(msgAuctionCancelledSubject.str())
        .SetMoney(auction->bid)
        .SendMailTo(MailReceiver(bidder, bidder_guid), auction, MAIL_CHECK_MASK_COPIED);
    }
}

AuctionHouseEntry const* WorldSession::GetCheckedAuctionHouseForAuctioneer(ObjectGuid guid)
{
    Unit* auctioneer = nullptr;

    // GM case
    if (guid == GetPlayer()->GetObjectGuid())
    {
        // command case will return only if player have real access to command
        // using special access modes (1,-1) done at mode set in command, so not need recheck
        if (GetPlayer()->GetAuctionAccessMode() == 0 && !ChatHandler(GetPlayer()).FindCommand("auction"))
        {
            sLog.Out(LOG_BASIC, LOG_LVL_DEBUG, "%s attempt open auction in cheating way.", guid.GetString().c_str());
            return nullptr;
        }

        auctioneer = GetPlayer();
    }
    // auctioneer case
    else
    {
        auctioneer = GetPlayer()->GetNPCIfCanInteractWith(guid, UNIT_NPC_FLAG_AUCTIONEER);
        if (!auctioneer)
        {
            sLog.Out(LOG_BASIC, LOG_LVL_DEBUG, "Auctioneeer %s accessed in cheating way.", guid.GetString().c_str());
            return nullptr;
        }
    }

    // always return pointer
    return AuctionHouseMgr::GetAuctionHouseEntry(auctioneer);
}

// this void creates new auction and adds auction to some auctionhouse
void WorldSession::HandleAuctionSellItem(WorldPackets::AuctionHouse::AuctionSellItem const& packet)
{
    if (!packet.bid || !packet.etime)
        return;                                             // check for cheaters

    // Client limit
    if (packet.bid > 2000000000 || packet.buyout > 2000000000)
    {
        SendAuctionCommandResult(nullptr, AUCTION_STARTED, AUCTION_ERR_NOT_ENOUGH_MONEY);
        ProcessAnticheatAction("GoldDupe", "Putting too high auction price", CHEAT_ACTION_LOG);
        return;
    }
    if (packet.buyout && packet.bid > packet.buyout)
    {
        SendAuctionCommandResult(nullptr, AUCTION_STARTED, AUCTION_ERR_HIGHER_BID);
        ProcessAnticheatAction("GoldDupe", "bid > buyout", CHEAT_ACTION_LOG);
        return;
    }

    if (!sWorld.getConfig(CONFIG_BOOL_GM_ALLOW_TRADES) && GetSecurity() > SEC_PLAYER)
    {
        SendAuctionCommandResult(nullptr, AUCTION_STARTED, AUCTION_ERR_RESTRICTED_ACCOUNT);
        return;
    }

    if (HasTrialRestrictions())
    {
        SendAuctionCommandResult(nullptr, AUCTION_STARTED, AUCTION_ERR_RESTRICTED_ACCOUNT);
        return;
    }

    Player* pl = GetPlayer();

    AuctionHouseEntry const* auctionHouseEntry = GetCheckedAuctionHouseForAuctioneer(packet.auctioneerGuid);
    if (!auctionHouseEntry)
    {
        SendAuctionCommandResult(nullptr, AUCTION_STARTED, AUCTION_ERR_DATABASE);
        return;
    }

    // always return pointer
    AuctionHouseObject* auctionHouse = sAuctionMgr.GetAuctionsMap(auctionHouseEntry);

    uint32 limit = sWorld.getConfig(CONFIG_UINT32_ACCOUNT_CONCURRENT_AUCTION_LIMIT);
    if (!!limit && auctionHouse->GetAccountAuctionCount(GetAccountId()) >= limit)
    {
        pl->SendSysMessage("You have reached the limit of active auctions on your account.");
        SendAuctionCommandResult(nullptr, AUCTION_STARTED, AUCTION_ERR_DATABASE);
        return;
    }

    // client send time in minutes, convert to common used sec time
    uint32 etime = packet.etime * MINUTE;

    // client understand only 3 auction time
    switch (etime)
    {
        case 1*MIN_AUCTION_TIME:
            break;
        case 4*MIN_AUCTION_TIME:
            break;
        case 12*MIN_AUCTION_TIME:
            break;
        default:
            SendAuctionCommandResult(nullptr, AUCTION_STARTED, AUCTION_ERR_DATABASE);
            return;
    }

    // remove fake death
    if (GetPlayer()->HasUnitState(UNIT_STATE_FEIGN_DEATH))
        GetPlayer()->RemoveSpellsCausingAura(SPELL_AURA_FEIGN_DEATH);

    if (!packet.itemGuid)
    {
        SendAuctionCommandResult(nullptr, AUCTION_STARTED, AUCTION_ERR_ITEM_NOT_FOUND);
        return;
    }

    Item *it = pl->GetItemByGuid(packet.itemGuid);

    // do not allow to sell already auctioned items
    if (sAuctionMgr.GetAItem(packet.itemGuid.GetCounter()))
    {
        sLog.Out(LOG_BASIC, LOG_LVL_ERROR, "AuctionError, %s is sending %s, but item is already in another auction", pl->GetGuidStr().c_str(), packet.itemGuid.GetString().c_str());
        SendAuctionCommandResult(nullptr, AUCTION_STARTED, AUCTION_ERR_INVENTORY, EQUIP_ERR_ITEM_NOT_FOUND);
        return;
    }

    // prevent sending bag with items (cheat: can be placed in bag after adding equipped empty bag to auction)
    if (!it)
    {
        SendAuctionCommandResult(nullptr, AUCTION_STARTED, AUCTION_ERR_INVENTORY, EQUIP_ERR_ITEM_NOT_FOUND);
        return;
    }

    // prevent selling item in bank slot
    if (_player->IsBankPos(it->GetPos()))
    {
        SendAuctionCommandResult(nullptr, AUCTION_STARTED, AUCTION_ERR_INVENTORY, EQUIP_ERR_ITEM_NOT_FOUND);
        return;
    }

    if (!it->CanBeTraded())
    {
        SendAuctionCommandResult(nullptr, AUCTION_STARTED, AUCTION_ERR_INVENTORY, EQUIP_ERR_ITEM_NOT_FOUND);
        return;
    }

    if ((it->GetProto()->Flags & ITEM_FLAG_CONJURED) || it->GetUInt32Value(ITEM_FIELD_DURATION))
    {
        SendAuctionCommandResult(nullptr, AUCTION_STARTED, AUCTION_ERR_INVENTORY, EQUIP_ERR_ITEM_NOT_FOUND);
        return;
    }

    // check money for deposit
    uint32 deposit = AuctionHouseMgr::GetAuctionDeposit(auctionHouseEntry, etime, it);

    if (pl->GetMoney() < deposit)
    {
        SendAuctionCommandResult(nullptr, AUCTION_STARTED, AUCTION_ERR_NOT_ENOUGH_MONEY);
        return;
    }

    if (GetSecurity() > SEC_PLAYER && sWorld.getConfig(CONFIG_BOOL_GM_LOG_TRADE))
    {
        sLog.Player(GetAccountId(), LOG_GM, LOG_LVL_BASIC,
            "GM %s (Account: %u) create auction: %s (Entry: %u Count: %u)",
            GetPlayerName(), GetAccountId(), it->GetProto()->Name1, it->GetEntry(), it->GetCount());
    }

    pl->ModifyMoney(-int32(deposit));

    uint32 auction_time = uint32(etime * sWorld.getConfig(CONFIG_FLOAT_RATE_AUCTION_TIME));

    AuctionEntry* AH = new AuctionEntry;
    AH->Id = sObjectMgr.GenerateAuctionID();
    AH->itemGuidLow = it->GetObjectGuid().GetCounter();
    AH->itemTemplate = it->GetEntry();
    AH->owner = pl->GetGUIDLow();
    AH->ownerAccount = pl->GetSession()->GetAccountId();
    AH->startbid = packet.bid;
    AH->bidder = 0;
    AH->bid = 0;
    AH->buyout = packet.buyout;
    AH->lockedIpAddress = GetRemoteAddress();
    AH->depositTime = time(nullptr);
    AH->expireTime = time(nullptr) + auction_time;
    AH->deposit = deposit;
    AH->auctionHouseEntry = auctionHouseEntry;

    sLog.Player(this, LOG_MONEY_TRADES, LOG_LVL_MINIMAL, "[AuctionHouse]: Player %s listing %s (%u) at auctioneer %s. Initial bid: %u, buyout: %u, duration: %u, auctionhouse: %u",
                pl->GetShortDescription().c_str(), it->GetGuidStr().c_str(), it->GetEntry(),
                packet.auctioneerGuid.GetString().c_str(), packet.bid, packet.buyout, auction_time, AH->GetHouseId());

    // Log this transaction
    PlayerTransactionData data;
    data.type = "PlaceAuction";
    data.parts[0].lowGuid = AH->owner;
    data.parts[0].itemsEntries[0] = AH->itemTemplate;
    data.parts[0].itemsCount[0] = it->GetCount();
    data.parts[0].itemsGuid[0] = it->GetGUIDLow();
    data.parts[0].money = packet.bid;
    data.parts[1].lowGuid = packet.auctioneerGuid.GetCounter();
    data.parts[1].money = packet.buyout;
    sWorld.LogTransaction(data);

    // Stage the inventory move locally so the save unit sees the removed item;
    // the destination is kept only for a known-failure restore.
    ItemPosCountVec restoreDest;
    uint8 restoreBagSlot = 0;
    bool const canRestore = pl->CanStoreItem(NULL_BAG, NULL_SLOT, restoreDest,
                                             it, restoreBagSlot, false) == EQUIP_ERR_OK;

    CharacterPersistenceAdmission admission;
    if (!admission.Admit({GetAccountId()}, AccountLifecycleKind::Mutation))
    {
        SendAuctionCommandResult(AH, AUCTION_STARTED, AUCTION_ERR_DATABASE);
        return;
    }

    pl->MoveItemFromInventory(it->GetBagSlot(), it->GetSlot(), true);

    CharacterIdentitySnapshot const identity = AuctionHandlerIdentity(*pl);
    CharacterPersistenceContext context(CharacterDatabase, identity.guid);
    bool assembled =
        context.AppendIdentityGuards({identity}) &&
        it->AppendSaveToDB(context, pl) &&
        it->AppendInventoryRemovalToDB(context) &&
        AH->AppendSaveToDB(context) &&
        pl->AppendInventoryToDB(context) &&
        pl->AppendGoldToDB(context);
    if (assembled)
    {
        pl->AppendIdentityGenerationPublication(context);
        SqlBatchResult const result = context.Execute();
        assembled = result.outcome == SqlWriteOutcome::Succeeded &&
                    result.stage == SqlBatchStage::Complete;
        if (!assembled)
        {
            if (result.outcome == SqlWriteOutcome::OutcomeUnknown)
                BlockCharacterRecovery(CharacterRecoveryRecord{
                    CharacterRecoveryKind::WholeSave, {identity.guid},
                    {identity.accountId}, identity.generation,
                    "auction placement outcome unknown"});
            sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                     "Auction placement failed for guid %u (outcome %u, stage %u)",
                     identity.guid, uint32(result.outcome), uint32(result.stage));
        }
    }

    if (!assembled)
    {
        if (canRestore)
            pl->MoveItemToInventory(restoreDest, it, true);
        SendAuctionCommandResult(AH, AUCTION_STARTED, AUCTION_ERR_DATABASE);
        return;
    }

    // Publication only after the composed unit committed.
    auctionHouse->AddAuction(AH);
    sAuctionMgr.AddAItem(it);

    SendAuctionCommandResult(AH, AUCTION_STARTED, AUCTION_OK);
}

// this function is called when client bids or buys out auction
void WorldSession::HandleAuctionPlaceBid(WorldPackets::AuctionHouse::AuctionPlaceBid const& packet)
{
    if (!sWorld.getConfig(CONFIG_BOOL_GM_ALLOW_TRADES) && GetSecurity() > SEC_PLAYER)
    {
        SendAuctionCommandResult(nullptr, AUCTION_BID_PLACED, AUCTION_ERR_RESTRICTED_ACCOUNT);
        return;
    }

    if (HasTrialRestrictions())
    {
        SendAuctionCommandResult(nullptr, AUCTION_BID_PLACED, AUCTION_ERR_RESTRICTED_ACCOUNT);
        return;
    }

    if (!packet.auctionId || !packet.price)
    {
        SendAuctionCommandResult(nullptr, AUCTION_BID_PLACED, AUCTION_ERR_ITEM_NOT_FOUND);
        return;
    }

    AuctionHouseEntry const* auctionHouseEntry = GetCheckedAuctionHouseForAuctioneer(packet.auctioneerGuid);
    if (!auctionHouseEntry)
    {
        SendAuctionCommandResult(nullptr, AUCTION_BID_PLACED, AUCTION_ERR_ITEM_NOT_FOUND);
        return;
    }

    // always return pointer
    AuctionHouseObject* auctionHouse = sAuctionMgr.GetAuctionsMap(auctionHouseEntry);

    // remove fake death
    if (GetPlayer()->HasUnitState(UNIT_STATE_FEIGN_DEATH))
        GetPlayer()->RemoveSpellsCausingAura(SPELL_AURA_FEIGN_DEATH);

    AuctionEntry* auction = auctionHouse->GetAuction(packet.auctionId);
    Player* pl = GetPlayer();

    if (!auction)
    {
        // item not found; auction may have expired, or been bought out
        SendAuctionCommandResult(nullptr, AUCTION_BID_PLACED, AUCTION_ERR_ITEM_NOT_FOUND);
        return;
    }

    if (auction->recoveryBlocked)
    {
        SendAuctionCommandResult(nullptr, AUCTION_BID_PLACED,
                                 AUCTION_ERR_DATABASE);
        return;
    }

    if (auction->owner == pl->GetGUIDLow())
    {
        // you cannot bid your own auction:
        SendAuctionCommandResult(nullptr, AUCTION_BID_PLACED, AUCTION_ERR_BID_OWN);
        return;
    }

    ObjectGuid ownerGuid = ObjectGuid(HIGHGUID_PLAYER, auction->owner);

    // impossible have online own another character (use this for speedup check in case online owner)
    Player* auction_owner = sObjectMgr.GetPlayer(ownerGuid);
    if (!auction_owner && sObjectMgr.GetPlayerAccountIdByGUID(ownerGuid) == pl->GetSession()->GetAccountId())
    {
        // you cannot bid your another character auction:
        SendAuctionCommandResult(nullptr, AUCTION_BID_PLACED, AUCTION_ERR_BID_OWN);
        return;
    }

    // cheating
    if (packet.price < auction->startbid)
    {
        SendAuctionCommandResult(nullptr, AUCTION_BID_PLACED, AUCTION_ERR_BID_INCREMENT);
        return;
    }

    // cheating or client lags
    if (packet.price <= auction->bid)
    {
        // client test but possible in result lags
        SendAuctionCommandResult(auction, AUCTION_BID_PLACED, AUCTION_ERR_HIGHER_BID);
        return;
    }

    // price too low for next bid if not buyout
    if ((packet.price < auction->buyout || auction->buyout == 0) &&
         packet.price < auction->bid + auction->GetAuctionOutBid())
    {
        // client test but possible in result lags
        SendAuctionCommandResult(auction, AUCTION_BID_PLACED, AUCTION_ERR_BID_INCREMENT);
        return;
    }

    if (packet.price > pl->GetMoney())
    {
        // you don't have enough money!, client tests!
        // SendAuctionCommandResult(auction->auctionId, AUCTION_ERR_INVENTORY, EQUIP_ERR_NOT_ENOUGH_MONEY);
        return;
    }

    if ((packet.price < auction->buyout) || (auction->buyout == 0))// bid
    {
        uint32 const previousBidder = auction->bidder;
        uint32 const previousBid = auction->bid;
        uint32 const newBidder = pl->GetGUIDLow();
        int32 const paid = newBidder == previousBidder
            ? int32(packet.price - previousBid) : int32(packet.price);

        CharacterIdentitySnapshot const identity = AuctionHandlerIdentity(*pl);
        CharacterPersistenceAdmission admission;
        std::vector<uint32> accounts{identity.accountId};
        if (previousBidder && previousBidder != newBidder)
        {
            uint32 const account = sObjectMgr.GetPlayerAccountIdByGUID(
                ObjectGuid(HIGHGUID_PLAYER, previousBidder));
            if (account)
                accounts.push_back(account);
        }

        CharacterPersistenceContext context(CharacterDatabase, identity.guid);
        Database* database = context.Batch().GetDatabase();
        bool assembled = admission.Admit(accounts, AccountLifecycleKind::Mutation) &&
                         context.AppendIdentityGuards({identity});
        if (assembled && database)
        {
            static SqlStatementID updateAuctionBid;
            SqlStatement statement = database->CreateStatement(
                updateAuctionBid,
                "UPDATE `auction` SET `buyer_guid` = ?, `last_bid` = ? "
                "WHERE `id` = ? AND `buyer_guid` = ? AND `last_bid` = ?");
            statement.addUInt32(newBidder);
            statement.addUInt32(packet.price);
            statement.addUInt32(auction->Id);
            statement.addUInt32(previousBidder);
            statement.addUInt32(previousBid);
            assembled = statement.AppendToCheckedBatch(
                            context.Batch(), SqlAffectedRowsPolicy::ExactlyOne) &&
                        pl->AppendGoldProjectedToDB(context,
                                                    pl->GetMoney() - uint32(paid));
            if (assembled && previousBidder && previousBidder != newBidder)
                assembled = sAuctionMgr.AppendAuctionOutbiddedMail(
                    context, auction, previousBidder, previousBid);
        }
        else
            assembled = false;

        if (!assembled)
        {
            SendAuctionCommandResult(auction, AUCTION_BID_PLACED,
                                     AUCTION_ERR_DATABASE);
            return;
        }

        context.OnCommit([pl, auction, packet, auction_owner, paid,
                          newBidder, previousBidder, previousBid]() {
            pl->LogModifyMoney(-paid, "AuctionBid",
                               ObjectGuid(HIGHGUID_PLAYER, auction->owner),
                               auction->itemTemplate);
            auction->bidder = newBidder;
            auction->bid = packet.price;
            if (auction_owner)
                auction_owner->GetSession()->SendAuctionOwnerNotification(
                    auction, false);
            (void)previousBidder;
            (void)previousBid;
        });
        pl->AppendIdentityGenerationPublication(context);

        SqlBatchResult const result = context.Execute();
        if (result.outcome != SqlWriteOutcome::Succeeded ||
            result.stage != SqlBatchStage::Complete)
        {
            if (result.outcome == SqlWriteOutcome::OutcomeUnknown)
            {
                auction->recoveryBlocked = true;
                BlockCharacterRecovery(CharacterRecoveryRecord{
                    CharacterRecoveryKind::WholeSave, {identity.guid},
                    {identity.accountId}, identity.generation,
                    "auction bid outcome unknown"});
                if (previousBidder && previousBidder != identity.guid)
                {
                    uint32 const account = sObjectMgr.GetPlayerAccountIdByGUID(
                        ObjectGuid(HIGHGUID_PLAYER, previousBidder));
                    BlockCharacterRecovery(CharacterRecoveryRecord{
                        CharacterRecoveryKind::WholeSave, {previousBidder},
                        {account}, 0,
                        "auction bid counterparty outcome unknown"});
                }
            }
            sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                     "Auction bid %u failed (outcome %u, stage %u)",
                     auction->Id, uint32(result.outcome), uint32(result.stage));
            SendAuctionCommandResult(auction, AUCTION_BID_PLACED,
                                     AUCTION_ERR_DATABASE);
            return;
        }

        SendAuctionCommandResult(auction, AUCTION_BID_PLACED, AUCTION_OK);
        return;
    }
    else                                                    // buyout
    {
        uint32 const previousBidder = auction->bidder;
        uint32 const previousBid = auction->bid;
        int32 const paid = pl->GetGUIDLow() == previousBidder
            ? int32(auction->buyout - previousBid)
            : int32(auction->buyout);

        PlayerTransactionData data;
        data.type = "Buyout";
        data.parts[0].lowGuid = auction->owner;
        data.parts[0].itemsEntries[0] = auction->itemTemplate;
        Item* item = sAuctionMgr.GetAItem(auction->itemGuidLow);
        data.parts[0].itemsCount[0] = item ? item->GetCount() : 0;
        data.parts[0].itemsGuid[0] = auction->itemGuidLow;
        data.parts[1].lowGuid = pl->GetGUIDLow();
        data.parts[1].money = auction->buyout;
        sWorld.LogTransaction(data);

        // Buyer gold, old-bidder refund, both mails, Item ownership and the
        // auction row are one checked unit.
        MailPersistenceStatus const status =
            sAuctionMgr.ProcessBuyoutAuction(auction, pl, paid,
                                             previousBidder, previousBid);
        if (status != MailPersistenceStatus::Committed)
        {
            sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                     "Auction %u buyout failed (status %u); listing kept",
                     auction->Id, uint32(status));
            SendAuctionCommandResult(auction, AUCTION_BID_PLACED,
                                     AUCTION_ERR_DATABASE);
            return;
        }

        SendAuctionCommandResult(auction, AUCTION_BID_PLACED, AUCTION_OK);
        auctionHouse->RemoveAuction(auction);
        delete auction;
        return;
    }
}

// this void is called when auction_owner cancels his auction
void WorldSession::HandleAuctionRemoveItem(WorldPackets::AuctionHouse::AuctionRemoveItem const& packet)
{
    AuctionHouseEntry const* auctionHouseEntry = GetCheckedAuctionHouseForAuctioneer(packet.auctioneerGuid);
    if (!auctionHouseEntry)
        return;

    // remove fake death
    if (GetPlayer()->HasUnitState(UNIT_STATE_FEIGN_DEATH))
        GetPlayer()->RemoveSpellsCausingAura(SPELL_AURA_FEIGN_DEATH);

    // always return pointer
    AuctionHouseObject* auctionHouse = sAuctionMgr.GetAuctionsMap(auctionHouseEntry);
    AuctionEntry* auction = auctionHouse->GetAuction(packet.auctionId);
    Player* pl = GetPlayer();

    if (auction && auction->recoveryBlocked)
    {
        SendAuctionCommandResult(nullptr, AUCTION_REMOVED, AUCTION_ERR_DATABASE);
        return;
    }

    if (!auction || auction->owner != pl->GetGUIDLow())
    {
        SendAuctionCommandResult(nullptr, AUCTION_REMOVED, AUCTION_ERR_DATABASE);
        sLog.Out(LOG_BASIC, LOG_LVL_ERROR, "CHEATER : %u, he tried to cancel auction (id: %u) of another player, or auction is nullptr", pl->GetGUIDLow(), packet.auctionId);
        return;
    }

    if (!sAuctionMgr.GetAItem(auction->itemGuidLow))
    {
        sLog.Out(LOG_BASIC, LOG_LVL_ERROR, "Auction id: %u has nonexistent item (item guid : %u)!!!", auction->Id, auction->itemGuidLow);
        SendAuctionCommandResult(nullptr, AUCTION_REMOVED, AUCTION_ERR_INVENTORY, EQUIP_ERR_ITEM_NOT_FOUND);
        return;
    }

    uint32 const auctionCut = auction->bidder ? auction->GetAuctionCut() : 0;
    if (auctionCut > 0 && pl->GetMoney() < auctionCut)
        return;

    // Owner gold, bidder refund, both mails, Item ownership and the auction
    // row are one checked unit.
    MailPersistenceStatus const status =
        sAuctionMgr.ProcessCancelAuction(auction, pl, auctionCut);
    if (status != MailPersistenceStatus::Committed)
    {
        sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                 "Auction %u cancel failed (status %u); listing kept",
                 auction->Id, uint32(status));
        SendAuctionCommandResult(auction, AUCTION_REMOVED, AUCTION_ERR_DATABASE);
        return;
    }

    SendAuctionCommandResult(auction, AUCTION_REMOVED, AUCTION_OK);
    auctionHouse->RemoveAuction(auction);
    delete auction;
}


class AuctionHouseClientQueryTask : public AuctionHouseClientQuery
{
public:
    AuctionHouseClientQueryTask(AuctionClientQueryType type) : _queryType(type)
    {
    }
    void operator ()()
    {
        if (WorldSession* sess = sWorld.FindSession(accountId))
        {
            sess->SetReceivedAHListRequest(false);

            Player* player = sess->GetPlayer();
            if (!player || !player->IsInWorld())
                return;

            WorldPacket data(0, 12);
            uint32 count = 0;
            uint32 totalcount = 0;
            size_t countPos = data.wpos();
            data << uint32(count);
            switch (_queryType)
            {
                case AUCTION_QUERY_LIST:
                {
                    data.SetOpcode(SMSG_AUCTION_LIST_RESULT);
                    auctionHouse->BuildListAuctionItems(data, player, *this, count, totalcount);

                    break;
                }
                case AUCTION_QUERY_LIST_BIDDER:
                {
                    data.SetOpcode(SMSG_AUCTION_BIDDER_LIST_RESULT);
                    for (const auto& outbiddedAuctionId : outbiddedAuctionIds)
                    {
                        AuctionEntry* auction = auctionHouse->GetAuction(outbiddedAuctionId);
                        if (auction)
                        {
                            ++totalcount;

                            if (count < 50 && totalcount > listfrom)
                                if (auction->BuildAuctionInfo(data))
                                    ++count;
                        }
                    }

                    auctionHouse->BuildListBidderItems(data, player, listfrom, count, totalcount);
                    break;
                }
                case AUCTION_QUERY_LIST_OWNER:
                {
                    data.SetOpcode(SMSG_AUCTION_OWNER_LIST_RESULT);
                    auctionHouse->BuildListOwnerItems(data, player, listfrom, count, totalcount);
                    break;
                }
                default:
                {
                    sLog.Out(LOG_BASIC, LOG_LVL_ERROR, "[AsyncAuctionQuery] Invalid query type %u", _queryType);
                    return;
                }
            }

            data.put<uint32>(countPos, count);
            data << uint32(totalcount);

            sess->SendPacket(&data);
        }
    }
    AuctionHouseObject* auctionHouse;
    AuctionClientQueryType _queryType;
};

// called when player lists his bids
void WorldSession::HandleAuctionListBidderItems(WorldPackets::AuctionHouse::AuctionListBidderItem const& packet)
{
    if (ReceivedAHListRequest())
        return; // Only one AH request at a time is allowed

    AuctionHouseEntry const* auctionHouseEntry = GetCheckedAuctionHouseForAuctioneer(packet.auctioneerGuid);
    if (!auctionHouseEntry)
        return;

    // remove fake death
    if (GetPlayer()->HasUnitState(UNIT_STATE_FEIGN_DEATH))
        GetPlayer()->RemoveSpellsCausingAura(SPELL_AURA_FEIGN_DEATH);

    AuctionHouseClientQueryTask task(AUCTION_QUERY_LIST_BIDDER);
    task.auctionHouse = sAuctionMgr.GetAuctionsMap(auctionHouseEntry);
    task.accountId = GetAccountId();
    task.listfrom = packet.pagingElementStartIndex;
    task.outbiddedAuctionIds = packet.bidAuctionIdsToRefresh;
    SetReceivedAHListRequest(true);
    sWorld.AddAsyncTask(std::move(task));
}

// this void sends player info about his auctions
void WorldSession::HandleAuctionListOwnerItems(WorldPackets::AuctionHouse::AuctionListOwnerItems const& packet)
{
    if (ReceivedAHListRequest())
        return;

    AuctionHouseEntry const* auctionHouseEntry = GetCheckedAuctionHouseForAuctioneer(packet.auctioneerGuid);
    if (!auctionHouseEntry)
        return;

    // remove fake death
    if (GetPlayer()->HasUnitState(UNIT_STATE_FEIGN_DEATH))
        GetPlayer()->RemoveSpellsCausingAura(SPELL_AURA_FEIGN_DEATH);

    AuctionHouseClientQueryTask task(AUCTION_QUERY_LIST_OWNER);
    task.auctionHouse = sAuctionMgr.GetAuctionsMap(auctionHouseEntry);
    task.accountId = GetAccountId();
    task.listfrom = packet.listfrom;
    SetReceivedAHListRequest(true);
    sWorld.AddAsyncTask({std::move(task)});
}

void WorldSession::HandleAuctionListItems(WorldPackets::AuctionHouse::AuctionListItems const& packet)
{
    if (ReceivedAHListRequest())
        return;

    AuctionHouseClientQueryTask task(AUCTION_QUERY_LIST);
    task.accountId = GetAccountId();

    task.listfrom = packet.listfrom;
    task.levelmin = packet.levelmin;
    task.levelmax = packet.levelmax;
    task.auctionSlotID = packet.auctionSlotID;
    task.auctionMainCategory = packet.auctionMainCategory;
    task.auctionSubCategory = packet.auctionSubCategory;
    task.quality = packet.quality;
    task.usable = packet.usable;

    AuctionHouseEntry const* auctionHouseEntry = GetCheckedAuctionHouseForAuctioneer(packet.auctioneerGuid);
    if (!auctionHouseEntry)
        return;

    // always return pointer
    task.auctionHouse = sAuctionMgr.GetAuctionsMap(auctionHouseEntry);

    // remove fake death
    if (GetPlayer()->HasUnitState(UNIT_STATE_FEIGN_DEATH))
        GetPlayer()->RemoveSpellsCausingAura(SPELL_AURA_FEIGN_DEATH);

    // converting string that we try to find to lower case
    if (!Utf8toWStr(packet.searchedname, task.wsearchedname))
        return;

    wstrToLower(task.wsearchedname);
    SetReceivedAHListRequest(true);
    sWorld.AddAsyncTask(std::move(task));
}
