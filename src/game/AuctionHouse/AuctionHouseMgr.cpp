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

#include "AuctionHouseMgr.h"
#include "Database/DatabaseEnv.h"
#include "Database/CharacterPersistenceContext.h"
#include "Database/CharacterIdentityPersistence.h"
#include "Database/CharacterPersistenceRecovery.h"
#include "Database/SqlPreparedStatement.h"
#include "Mail/MailPersistence.h"
#include "DBCStores.h"
#include "AccountMgr.h"
#include "Item.h"
#include "Bag.h"
#include "Language.h"
#include "Log.h"
#include "ObjectMgr.h"
#include "ObjectGuid.h"
#include "Player.h"
#include "World.h"
#include "WorldPacket.h"
#include "WorldSession.h"
#include "Mail.h"
#include "TransactionLog.h"
#include "Policies/SingletonImp.h"

INSTANTIATE_SINGLETON_1(AuctionHouseMgr);

bool AuctionHouseObject::RemoveAuction(AuctionEntry* entry)
{
    // Clean up multimaps before final erasure
    auto bounds = OrderedAuctionMap.equal_range(entry->buyout);
    for (AuctionMultiMap::iterator itr = bounds.first; itr != bounds.second; ++itr)
    {
        if (itr->second->Id == entry->Id) {
            OrderedAuctionMap.erase(itr);
            break;
        }
    }

    bounds = AccountAuctionMap.equal_range(entry->ownerAccount);
    for (AuctionMultiMap::iterator itr = bounds.first; itr != bounds.second; ++itr)
    {
        if (itr->second->Id == entry->Id) {
            AccountAuctionMap.erase(itr);
            break;
        }
    }

    if (AuctionsMap.erase(entry->Id) > 0)
    {
        sObjectMgr.FreeAuctionID(entry->Id);
        return true;
    }
    return false;
}

void AuctionHouseObject::AddAuction(AuctionEntry* ah)
{
    MANGOS_ASSERT(ah);
    AuctionsMap[ah->Id] = ah;
    OrderedAuctionMap.insert(std::pair<uint32, AuctionEntry*>(ah->buyout, ah));
    AccountAuctionMap.insert(std::pair<uint32, AuctionEntry*>(ah->ownerAccount, ah));
}

AuctionHouseMgr::AuctionHouseMgr()
{
}

AuctionHouseMgr::~AuctionHouseMgr()
{
    for (const auto itr : mAitems)
        delete itr.second;
}

AuctionHouseObject * AuctionHouseMgr::GetAuctionsMap(AuctionHouseEntry const* house)
{
    auto itr = m_mAuctionHouses.find(house->houseId);
    if (itr != m_mAuctionHouses.end())
        return itr->second;

    return nullptr;
}

uint32 AuctionHouseMgr::GetAuctionDeposit(AuctionHouseEntry const* entry, uint32 time, Item *pItem)
{
    float deposit = float(pItem->GetProto()->SellPrice * pItem->GetCount() * (time / MIN_AUCTION_TIME));

    deposit = deposit * entry->depositPercent / 100.0f;

    float min_deposit = float(sWorld.getConfig(CONFIG_UINT32_AUCTION_DEPOSIT_MIN));

    if (deposit < min_deposit)
        deposit = min_deposit;

    return uint32(deposit * sWorld.getConfig(CONFIG_FLOAT_RATE_AUCTION_DEPOSIT));
}

namespace
{
void AddAuctionAccount(std::vector<uint32>& accounts, uint32 guid)
{
    if (!guid)
        return;
    uint32 const account = sObjectMgr.GetPlayerAccountIdByGUID(
        ObjectGuid(HIGHGUID_PLAYER, guid));
    if (account)
        accounts.push_back(account);
}

CharacterIdentitySnapshot BuildAuctionIdentity(Player const& player)
{
    CharacterIdentitySnapshot identity;
    identity.guid = player.GetGUIDLow();
    identity.accountId = player.GetSession()
        ? player.GetSession()->GetAccountId() : 0;
    identity.canonicalName = player.GetName();
    identity.generation = player.GetIdentityGeneration();
    return identity;
}

void BlockAuctionRecovery(std::vector<uint32> guids,
                          std::vector<uint32> accounts,
                          char const* detail)
{
    CharacterRecoveryRecord record;
    record.kind = CharacterRecoveryKind::WholeSave;
    record.guids = std::move(guids);
    record.accountIds = std::move(accounts);
    record.expectedGeneration = 0;
    record.detail = detail;
    if (!record.guids.empty() || !record.accountIds.empty())
        BlockCharacterRecovery(record);
}
}

bool AuctionHouseMgr::AppendAuctionWonMail(
    CharacterPersistenceContext& context, AuctionEntry* auction,
    uint32 bidderGuid, uint32 bidAmount)
{
    Item* pItem = GetAItem(auction->itemGuidLow);
    if (!pItem)
        return false;

    ObjectGuid const bidderGuidObj(HIGHGUID_PLAYER, bidderGuid);
    Player* bidder = sObjectMgr.GetPlayer(bidderGuidObj);
    uint32 const bidderAccount = bidder && bidder->GetSession()
        ? bidder->GetSession()->GetAccountId()
        : sObjectMgr.GetPlayerAccountIdByGUID(bidderGuidObj);
    if (!bidder && !bidderAccount)
        return false;

    std::ostringstream subject;
    subject << auction->itemTemplate << ":0:" << AUCTION_WON;
    std::ostringstream body;
    body.width(16);
    body << std::right << std::hex << auction->owner;
    body << std::dec << ":" << bidAmount << ":" << auction->buyout;

    uint32 mailId = 0;
    if (!MailDraft(subject.str(), body.str())
             .AddItem(pItem)
             .AppendSendTo(context, MailReceiver(bidder, bidderGuidObj),
                           auction, MAIL_CHECK_MASK_COPIED, 0, 0, mailId))
        return false;

    context.OnCommit([bidder, auction]() {
        if (bidder)
            bidder->GetSession()->SendAuctionBidderNotification(auction, true);
    });
    return true;
}

bool AuctionHouseMgr::AppendAuctionSuccessfulMail(
    CharacterPersistenceContext& context, AuctionEntry* auction,
    uint32 bidderGuid, uint32 bidAmount)
{
    ObjectGuid const ownerGuid(HIGHGUID_PLAYER, auction->owner);
    Player* owner = sObjectMgr.GetPlayer(ownerGuid);
    uint32 const ownerAccount = owner && owner->GetSession()
        ? owner->GetSession()->GetAccountId()
        : sObjectMgr.GetPlayerAccountIdByGUID(ownerGuid);
    if (!owner && !ownerAccount)
        return false;

    uint32 const auctionCut = auction->GetAuctionCut();
    uint32 const profit = bidAmount + auction->deposit - auctionCut;

    std::ostringstream subject;
    subject << auction->itemTemplate << ":0:" << AUCTION_SUCCESSFUL;
    std::ostringstream body;
    body.width(16);
    body << std::right << std::hex << bidderGuid;
    body << std::dec << ":" << bidAmount << ":" << auction->buyout;
    body << ":" << auction->deposit << ":" << auctionCut;

    uint32 mailId = 0;
    if (!MailDraft(subject.str(), body.str())
             .SetMoney(profit)
             .AppendSendTo(context, MailReceiver(owner, ownerGuid), auction,
                           MAIL_CHECK_MASK_COPIED, 0, 0, mailId))
        return false;

    context.OnCommit([owner, auction]() {
        if (owner)
            owner->GetSession()->SendAuctionOwnerNotification(auction, true);
    });
    return true;
}

bool AuctionHouseMgr::AppendAuctionExpiredMail(
    CharacterPersistenceContext& context, AuctionEntry* auction)
{
    Item* pItem = GetAItem(auction->itemGuidLow);
    if (!pItem)
        return false;

    ObjectGuid const ownerGuid(HIGHGUID_PLAYER, auction->owner);
    Player* owner = sObjectMgr.GetPlayer(ownerGuid);
    uint32 const ownerAccount = owner && owner->GetSession()
        ? owner->GetSession()->GetAccountId()
        : sObjectMgr.GetPlayerAccountIdByGUID(ownerGuid);
    if (!owner && !ownerAccount)
        return false;

    std::ostringstream subject;
    subject << auction->itemTemplate << ":0:" << AUCTION_EXPIRED;

    uint32 mailId = 0;
    if (!MailDraft(subject.str())
             .AddItem(pItem)
             .AppendSendTo(context, MailReceiver(owner, ownerGuid), auction,
                           MAIL_CHECK_MASK_COPIED, 0, 0, mailId))
        return false;

    context.OnCommit([owner, auction]() {
        if (owner)
            owner->GetSession()->SendAuctionOwnerNotification(auction, false);
    });
    return true;
}

bool AuctionHouseMgr::AppendAuctionOutbiddedMail(
    CharacterPersistenceContext& context, AuctionEntry* auction,
    uint32 oldBidderGuid, uint32 oldBid)
{
    if (!oldBidderGuid)
        return true;

    ObjectGuid const bidderGuid(HIGHGUID_PLAYER, oldBidderGuid);
    Player* bidder = sObjectMgr.GetPlayer(bidderGuid);
    uint32 const bidderAccount = bidder && bidder->GetSession()
        ? bidder->GetSession()->GetAccountId()
        : sObjectMgr.GetPlayerAccountIdByGUID(bidderGuid);
    if (!bidder && !bidderAccount)
        return false;                                   // cannot refund

    std::ostringstream subject;
    subject << auction->itemTemplate << ":0:" << AUCTION_OUTBIDDED;
    uint32 mailId = 0;
    if (!MailDraft(subject.str())
             .SetMoney(oldBid)
             .AppendSendTo(context, MailReceiver(bidder, bidderGuid), auction,
                           MAIL_CHECK_MASK_COPIED, 0, 0, mailId))
        return false;

    context.OnCommit([bidder, auction]() {
        if (bidder)
            bidder->GetSession()->SendAuctionBidderNotification(auction, false);
    });
    return true;
}

bool AuctionHouseMgr::AppendAuctionCancelledToBidderMail(
    CharacterPersistenceContext& context, AuctionEntry* auction,
    uint32 bidderGuid, uint32 bidAmount)
{
    if (!bidderGuid)
        return true;

    ObjectGuid const bidderGuidObj(HIGHGUID_PLAYER, bidderGuid);
    Player* bidder = sObjectMgr.GetPlayer(bidderGuidObj);
    uint32 const bidderAccount = bidder && bidder->GetSession()
        ? bidder->GetSession()->GetAccountId()
        : sObjectMgr.GetPlayerAccountIdByGUID(bidderGuidObj);
    if (!bidder && !bidderAccount)
        return false;

    std::ostringstream subject;
    subject << auction->itemTemplate << ":0:" << AUCTION_CANCELLED_TO_BIDDER;
    uint32 mailId = 0;
    if (!MailDraft(subject.str())
             .SetMoney(bidAmount)
             .AppendSendTo(context, MailReceiver(bidder, bidderGuidObj),
                           auction, MAIL_CHECK_MASK_COPIED, 0, 0, mailId))
        return false;

    context.OnCommit([bidder, auction]() {
        if (bidder)
            bidder->GetSession()->SendAuctionRemovedNotification(auction);
    });
    return true;
}

bool AuctionHouseMgr::AppendAuctionCancelledOwnerMail(
    CharacterPersistenceContext& context, AuctionEntry* auction)
{
    Item* pItem = GetAItem(auction->itemGuidLow);
    if (!pItem)
        return false;

    ObjectGuid const ownerGuid(HIGHGUID_PLAYER, auction->owner);
    Player* owner = sObjectMgr.GetPlayer(ownerGuid);
    uint32 const ownerAccount = owner && owner->GetSession()
        ? owner->GetSession()->GetAccountId()
        : sObjectMgr.GetPlayerAccountIdByGUID(ownerGuid);
    if (!owner && !ownerAccount)
        return false;

    std::ostringstream subject;
    subject << auction->itemTemplate << ":0:" << AUCTION_CANCELED;
    uint32 mailId = 0;
    return MailDraft(subject.str())
        .AddItem(pItem)
        .AppendSendTo(context, MailReceiver(owner, ownerGuid), auction,
                      MAIL_CHECK_MASK_COPIED, 0, 0, mailId);
}

MailPersistenceStatus AuctionHouseMgr::ProcessExpiredAuction(
    AuctionEntry* auction)
{
    Item* pItem = GetAItem(auction->itemGuidLow);
    if (!pItem)
        return MailPersistenceStatus::Rejected;

    std::vector<uint32> accounts;
    AddAuctionAccount(accounts, auction->owner);
    if (auction->bidder)
        AddAuctionAccount(accounts, auction->bidder);
    if (accounts.empty())
        return MailPersistenceStatus::Rejected;

    CharacterPersistenceAdmission admission;
    if (!admission.Admit(accounts, AccountLifecycleKind::Mutation))
        return MailPersistenceStatus::Rejected;

    CharacterPersistenceContext context(CharacterDatabase, auction->Id);
    bool assembled;
    if (auction->bidder == 0)
        assembled = AppendAuctionExpiredMail(context, auction);
    else
        assembled = AppendAuctionSuccessfulMail(context, auction,
                                                auction->bidder, auction->bid) &&
                    AppendAuctionWonMail(context, auction, auction->bidder,
                                         auction->bid);
    assembled = assembled && auction->AppendDeleteFromDB(context);
    if (!assembled)
        return MailPersistenceStatus::Rejected;

    MailPersistenceStatus const status =
        ClassifyMailBatch(context.Execute());
    if (status == MailPersistenceStatus::Committed)
        RemoveAItem(auction->itemGuidLow);
    else if (status == MailPersistenceStatus::OutcomeUnknown)
    {
        auction->recoveryBlocked = true;
        BlockAuctionRecovery({auction->owner, auction->bidder}, accounts,
                             "expired auction outcome unknown");
    }
    return status;
}

MailPersistenceStatus AuctionHouseMgr::ProcessBuyoutAuction(
    AuctionEntry* auction, Player* buyer, int32 paid,
    uint32 previousBidder, uint32 previousBid)
{
    if (!auction || !buyer || !buyer->GetSession() || paid < 0)
        return MailPersistenceStatus::Rejected;
    Item* pItem = GetAItem(auction->itemGuidLow);
    if (!pItem)
        return MailPersistenceStatus::Rejected;

    CharacterIdentitySnapshot const identity = BuildAuctionIdentity(*buyer);
    std::vector<uint32> accounts{identity.accountId};
    AddAuctionAccount(accounts, auction->owner);
    if (previousBidder && previousBidder != buyer->GetGUIDLow())
        AddAuctionAccount(accounts, previousBidder);

    CharacterPersistenceAdmission admission;
    if (!admission.Admit(accounts, AccountLifecycleKind::Mutation))
        return MailPersistenceStatus::Rejected;

    CharacterPersistenceContext context(CharacterDatabase, identity.guid);
    bool assembled =
        context.AppendIdentityGuards({identity}) &&
        buyer->AppendGoldProjectedToDB(
            context, buyer->GetMoney() - uint32(paid));
    if (assembled && previousBidder &&
        previousBidder != buyer->GetGUIDLow())
        assembled = AppendAuctionOutbiddedMail(context, auction,
                                               previousBidder, previousBid);
    assembled = assembled &&
                AppendAuctionSuccessfulMail(context, auction,
                                            buyer->GetGUIDLow(),
                                            auction->buyout) &&
                AppendAuctionWonMail(context, auction, buyer->GetGUIDLow(),
                                     auction->buyout) &&
                auction->AppendDeleteFromDB(context);
    if (!assembled)
        return MailPersistenceStatus::Rejected;

    context.OnCommit([buyer, auction, paid]() {
        buyer->LogModifyMoney(-paid, "AuctionBuyout",
                              ObjectGuid(HIGHGUID_PLAYER, auction->owner),
                              auction->itemTemplate);
        auction->bidder = buyer->GetGUIDLow();
        auction->bid = auction->buyout;
    });
    buyer->AppendIdentityGenerationPublication(context);

    MailPersistenceStatus const status =
        ClassifyMailBatch(context.Execute());
    if (status == MailPersistenceStatus::Committed)
        RemoveAItem(auction->itemGuidLow);
    else if (status == MailPersistenceStatus::OutcomeUnknown)
    {
        auction->recoveryBlocked = true;
        std::vector<uint32> guids{identity.guid, auction->owner};
        if (previousBidder && previousBidder != identity.guid)
            guids.push_back(previousBidder);
        BlockAuctionRecovery(std::move(guids), accounts,
                             "auction buyout outcome unknown");
    }
    return status;
}

MailPersistenceStatus AuctionHouseMgr::ProcessOrphanedAuction(
    AuctionEntry* auction)
{
    if (!auction || !GetAItem(auction->itemGuidLow))
        return MailPersistenceStatus::Rejected;

    ObjectGuid const ownerGuid(HIGHGUID_PLAYER, auction->owner);
    Player* owner = sObjectMgr.GetPlayer(ownerGuid);
    uint32 const ownerAccount = owner && owner->GetSession()
        ? owner->GetSession()->GetAccountId()
        : sObjectMgr.GetPlayerAccountIdByGUID(ownerGuid);
    if (!ownerAccount)
        return MailPersistenceStatus::Rejected;

    CharacterPersistenceAdmission admission;
    if (!admission.Admit({ownerAccount}, AccountLifecycleKind::Mutation))
        return MailPersistenceStatus::Rejected;

    CharacterPersistenceContext context(CharacterDatabase, auction->Id);
    std::ostringstream subject;
    subject << auction->itemTemplate << ":0:" << AUCTION_CANCELED;
    uint32 mailId = 0;
    if (!MailDraft(subject.str())
             .AddItem(GetAItem(auction->itemGuidLow))
             .AppendSendTo(context, MailReceiver(owner, ownerGuid), auction,
                           MAIL_CHECK_MASK_COPIED, 0, 0, mailId) ||
        !auction->AppendDeleteFromDB(context))
        return MailPersistenceStatus::Rejected;

    MailPersistenceStatus const status = ClassifyMailBatch(context.Execute());
    if (status == MailPersistenceStatus::Committed)
        RemoveAItem(auction->itemGuidLow);
    else if (status == MailPersistenceStatus::OutcomeUnknown)
    {
        auction->recoveryBlocked = true;
        BlockAuctionRecovery({auction->owner}, {ownerAccount},
                             "orphaned auction outcome unknown");
    }
    return status;
}

MailPersistenceStatus AuctionHouseMgr::ProcessCancelAuction(
    AuctionEntry* auction, Player* owner, uint32 auctionCut)
{
    if (!auction || !owner || !owner->GetSession())
        return MailPersistenceStatus::Rejected;
    if (!GetAItem(auction->itemGuidLow))
        return MailPersistenceStatus::Rejected;

    CharacterIdentitySnapshot const identity = BuildAuctionIdentity(*owner);
    std::vector<uint32> accounts{identity.accountId};
    if (auction->bidder)
        AddAuctionAccount(accounts, auction->bidder);

    CharacterPersistenceAdmission admission;
    if (!admission.Admit(accounts, AccountLifecycleKind::Mutation))
        return MailPersistenceStatus::Rejected;

    CharacterPersistenceContext context(CharacterDatabase, identity.guid);
    bool assembled = context.AppendIdentityGuards({identity});
    if (assembled && auction->bidder)
    {
        if (auctionCut > 0)
            assembled = owner->AppendGoldProjectedToDB(
                context, owner->GetMoney() - auctionCut);
        assembled = assembled &&
                    AppendAuctionCancelledToBidderMail(
                        context, auction, auction->bidder, auction->bid);
    }
    assembled = assembled &&
                AppendAuctionCancelledOwnerMail(context, auction) &&
                auction->AppendDeleteFromDB(context);
    if (!assembled)
        return MailPersistenceStatus::Rejected;

    context.OnCommit([owner, auction, auctionCut]() {
        if (auction->bidder && auctionCut > 0)
            owner->ModifyMoney(-int32(auctionCut));
    });
    owner->AppendIdentityGenerationPublication(context);

    MailPersistenceStatus const status =
        ClassifyMailBatch(context.Execute());
    if (status == MailPersistenceStatus::Committed)
        RemoveAItem(auction->itemGuidLow);
    else if (status == MailPersistenceStatus::OutcomeUnknown)
    {
        auction->recoveryBlocked = true;
        BlockAuctionRecovery({identity.guid, auction->bidder}, accounts,
                             "auction cancel outcome unknown");
    }
    return status;
}

AuctionHouseObject* AuctionHouseMgr::MakeNewAuctionHouseObject()
{
    m_vRealAuctionHouses.push_back(std::make_unique<AuctionHouseObject>());
    return m_vRealAuctionHouses.back().get();
}

void AuctionHouseMgr::LoadAuctionHouses()
{
    // Cross Faction - Single AH for all
    if (sWorld.getConfig(CONFIG_BOOL_ALLOW_TWO_SIDE_INTERACTION_AUCTION))
    {
        AuctionHouseObject* CrossFactionAuctionHouse = MakeNewAuctionHouseObject();

        for (uint32 i = 0; i < sAuctionHouseStore.GetNumRows(); i++)
        {
            AuctionHouseEntry const* houseEntry = sAuctionHouseStore.LookupEntry(i);
            if (!houseEntry)
                continue;

            m_mAuctionHouses.insert(std::make_pair(houseEntry->houseId, CrossFactionAuctionHouse));
        }
    }
    // Non-Linked Auction Houses - Separate AH for every DBC entry
    else if (sWorld.getConfig(CONFIG_BOOL_UNLINKED_AUCTION_HOUSES) && (sWorld.GetWowPatch() < WOW_PATCH_109))
    {
        for (uint32 i = 0; i < sAuctionHouseStore.GetNumRows(); i++)
        {
            AuctionHouseEntry const* houseEntry = sAuctionHouseStore.LookupEntry(i);
            if (!houseEntry)
                continue;

            m_mAuctionHouses.insert(std::make_pair(houseEntry->houseId, MakeNewAuctionHouseObject()));
        }
    }
    // Linked Auction Houses - One per faction
    else
    {
        AuctionHouseObject* AllianceAuctionHouse = MakeNewAuctionHouseObject();
        AuctionHouseObject* HordeAuctionHouse = MakeNewAuctionHouseObject();
        AuctionHouseObject* NeutralAuctionHouse = MakeNewAuctionHouseObject();

        for (uint32 i = 0; i < sAuctionHouseStore.GetNumRows(); i++)
        {
            AuctionHouseEntry const* houseEntry = sAuctionHouseStore.LookupEntry(i);
            if (!houseEntry)
                continue;

            switch (GetAuctionHouseTeam(houseEntry))
            {
                case ALLIANCE:
                    m_mAuctionHouses.insert(std::make_pair(houseEntry->houseId, AllianceAuctionHouse));
                    break;
                case HORDE:
                    m_mAuctionHouses.insert(std::make_pair(houseEntry->houseId, HordeAuctionHouse));
                    break;
                default:
                    m_mAuctionHouses.insert(std::make_pair(houseEntry->houseId, NeutralAuctionHouse));
                    break;
            }
        }
    }
}

void AuctionHouseMgr::LoadAuctionItems()
{
    //                                                                     0               1                    2        3           4          5        6               7                     8             9       10                           11
    std::unique_ptr<QueryResult> result = CharacterDatabase.Query("SELECT `creator_guid`, `gift_creator_guid`, `count`, `duration`, `charges`, `flags`, `enchantments`, `random_property_id`, `durability`, `text`, `item_guid`, `item_instance`.`item_id`, `item_instance`.`owner_guid` FROM `auction` JOIN `item_instance` ON `item_guid` = `guid`");

    if (!result)
    {
        BarGoLink bar(1);
        bar.step();
        sLog.Out(LOG_BASIC, LOG_LVL_MINIMAL, "");
        sLog.Out(LOG_BASIC, LOG_LVL_MINIMAL, ">> Loaded 0 auction items");
        return;
    }

    BarGoLink bar(result->GetRowCount());

    uint32 count = 0;

    Field* fields;
    do
    {
        bar.step();

        fields = result->Fetch();
        uint32 itemGuid = fields[10].GetUInt32();
        uint32 itemId = fields[11].GetUInt32();

        ItemPrototype const* proto = sObjectMgr.GetItemPrototype(itemId);

        if (!proto)
        {
            sLog.Out(LOG_BASIC, LOG_LVL_ERROR, "AuctionHouseMgr::LoadAuctionItems: Unknown item (GUID: %u id: #%u) in auction, skipped.", itemGuid, itemId);
            continue;
        }

        Item *item = NewItemOrBag(proto);

        // The persisted owner is the expected owner for the later transfer;
        // auction Items must not load with an empty owner.
        ObjectGuid const persistedOwner(HIGHGUID_PLAYER, fields[12].GetUInt32());
        if (!item->LoadFromDB(itemGuid, persistedOwner, fields, itemId))
        {
            delete item;
            continue;
        }
        AddAItem(item);

        ++count;
    }
    while (result->NextRow());

    sLog.Out(LOG_BASIC, LOG_LVL_MINIMAL, "");
    sLog.Out(LOG_BASIC, LOG_LVL_MINIMAL, ">> Loaded %u auction items", count);
}

void AuctionHouseMgr::LoadAuctions()
{
    std::unique_ptr<QueryResult> result = CharacterDatabase.Query("SELECT `id`, `house_id`, `item_guid`, `item_id`, `seller_guid`, `buyout_price`, `expire_time`, `buyer_guid`, `last_bid`, `start_bid`, `deposit` FROM `auction`");
    if (!result)
    {
        BarGoLink bar(1);
        bar.step();
        sLog.Out(LOG_BASIC, LOG_LVL_MINIMAL, "");
        sLog.Out(LOG_BASIC, LOG_LVL_MINIMAL, ">> Loaded 0 auctions. DB table `auction` is empty.");
        return;
    }

    BarGoLink bar(result->GetRowCount());
    uint32 count = 0;

    do
    {
        Field* fields = result->Fetch();

        bar.step();

        AuctionEntry* auction = new AuctionEntry;
        auction->Id = fields[0].GetUInt32();
        uint32 houseId  = fields[1].GetUInt32();
        auction->itemGuidLow = fields[2].GetUInt32();
        auction->itemTemplate = fields[3].GetUInt32();
        auction->owner = fields[4].GetUInt32();
        auction->buyout = fields[5].GetUInt32();
        auction->depositTime = 0;
        auction->expireTime = fields[6].GetUInt32();
        auction->bidder = fields[7].GetUInt32();
        auction->bid = fields[8].GetUInt32();
        auction->startbid = fields[9].GetUInt32();
        auction->deposit = fields[10].GetUInt32();
        auction->auctionHouseEntry = nullptr;                  // init later

        auction->ownerAccount = sObjectMgr.GetPlayerAccountIdByGUID(auction->owner);

        // check if sold item exists for guid
        // and item_template in fact (GetAItem will fail if problematic in result check in AuctionHouseMgr::LoadAuctionItems)
        Item* pItem = GetAItem(auction->itemGuidLow);
        if (!pItem)
        {
            auction->DeleteFromDB();
            sLog.Out(LOG_BASIC, LOG_LVL_ERROR, "Auction %u has not a existing item : %u, deleted", auction->Id, auction->itemGuidLow);
            delete auction;
            continue;
        }

        auction->auctionHouseEntry = sAuctionHouseStore.LookupEntry(houseId);

        if (!auction->auctionHouseEntry)
        {
            // need for send mail, use goblin auctionhouse
            auction->auctionHouseEntry = sAuctionHouseStore.LookupEntry(7);

            // Item return and auction deletion are one checked unit.
            MailPersistenceStatus const status =
                ProcessOrphanedAuction(auction);
            if (status != MailPersistenceStatus::Committed)
                sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                         "Auction %u orphan cancel failed (status %u); row kept",
                         auction->Id, uint32(status));

            delete auction;
            continue;
        }

        GetAuctionsMap(auction->auctionHouseEntry)->AddAuction(auction);
        ++count;
    }
    while (result->NextRow());

    sLog.Out(LOG_BASIC, LOG_LVL_MINIMAL, "");
    sLog.Out(LOG_BASIC, LOG_LVL_MINIMAL, ">> Loaded %u auctions", count);
}

void AuctionHouseMgr::AddAItem(Item* it)
{
    MANGOS_ASSERT(it);
    MANGOS_ASSERT(mAitems.find(it->GetGUIDLow()) == mAitems.end());
    mAitems[it->GetGUIDLow()] = it;
}

bool AuctionHouseMgr::RemoveAItem(uint32 id)
{
    ItemMap::iterator i = mAitems.find(id);
    if (i == mAitems.end())
        return false;
    mAitems.erase(i);
    return true;
}

void AuctionHouseMgr::Update()
{
    for (const auto& itr : m_vRealAuctionHouses)
        itr->Update();
}

uint32 AuctionHouseMgr::GetAuctionHouseTeam(AuctionHouseEntry const* house)
{
    // auction houses have faction field pointing to PLAYER,* factions,
    // but player factions not have filled team field, and hard go from faction value to faction_template value,
    // so more easy just sort by auction house ids
    switch (house->houseId)
    {
        case 1:
        case 2:
        case 3:
            return ALLIANCE;
        case 4:
        case 5:
        case 6:
            return HORDE;
        case 7:
        default:
            return 0;                                       // neutral
    }
}

uint32 AuctionHouseMgr::GetAuctionHouseId(uint32 factionTemplateId)
{
    uint32 houseId = 1;
    switch (factionTemplateId)
    {
        case   11:
        case   12:
            houseId = 1; // Human
            break;
        case   29:
        case   85:
            houseId = 6; // Orc
            break;
        case   55:
        case   57:
            houseId = 2; // Dwarf
            break;
        case   68:
        case   71:
            houseId = 4; // Undead
            break;
        case   79:
        case   80:
            houseId = 3; // Night Elf
            break;
        case  104:
        case  105:
            houseId = 5; // Tauren
            break;
        case  120:
            houseId = 7; // Booty Bay
            break;
        case  474:
            houseId = 7; // Gadgetzan
            break;
        case  534:
            houseId = 2; // Alliance Generic
            break;
        case  855:
            houseId = 7; // Everlook
            break;
        default:                                    // for unknown case
        {
            FactionTemplateEntry const* u_entry = sObjectMgr.GetFactionTemplateEntry(factionTemplateId);
            if (!u_entry)
                houseId = 7;                        // goblin auction house
            else if (u_entry->ourMask & FACTION_MASK_ALLIANCE)
                houseId = 1;                        // human auction house
            else if (u_entry->ourMask & FACTION_MASK_HORDE)
                houseId = 6;                        // orc auction house
            else
                houseId = 7;                        // goblin auction house
            break;
        }
    }
    return houseId;
}

AuctionHouseEntry const* AuctionHouseMgr::GetAuctionHouseEntry(Unit* unit)
{
    uint32 houseId = 1;                                     // dwarf auction house (used for normal cut/etc percents)

    if (!sWorld.getConfig(CONFIG_BOOL_ALLOW_TWO_SIDE_INTERACTION_AUCTION))
    {
        if (unit->GetTypeId() == TYPEID_UNIT)
        {
            // FIXME: found way for proper auctionhouse selection by another way
            // AuctionHouse.dbc have faction field with _player_ factions associated with auction house races.
            // but no easy way convert creature faction to player race faction for specific city
            houseId = GetAuctionHouseId(unit->GetFactionTemplateId());
        }
        else
        {
            Player* player = (Player*)unit;
            if (player->GetAuctionAccessMode() > 0)
                houseId = 7;
            else
            {
                switch (((Player*)unit)->GetTeam())
                {
                    case ALLIANCE:
                        houseId = player->GetAuctionAccessMode() == 0 ? 1 : 6;
                        break;
                    case HORDE:
                        houseId = player->GetAuctionAccessMode() == 0 ? 6 : 1;
                        break;
                }
            }
        }
    }

    return sAuctionHouseStore.LookupEntry(houseId);
}

AuctionHouseEntry const* AuctionHouseMgr::GetAuctionHouseEntry(uint32 factionTemplateId)
{
    uint32 houseId = GetAuctionHouseId(factionTemplateId);

    return sAuctionHouseStore.LookupEntry(houseId);
}

void AuctionHouseObject::Update()
{
    time_t curTime = sWorld.GetGameTime();
    // Handle expired auctions
    AuctionEntryMap::iterator next;
    // Store a ref to the entry and use it rather than derefencing the itr.
    // Also required to properly erase the itr and delete the entry if
    // necessary
    AuctionEntry* entry = nullptr;
    for (AuctionEntryMap::iterator itr = AuctionsMap.begin(); itr != AuctionsMap.end(); itr = next)
    {
        entry = itr->second;
        if (entry->depositTime + 5*60 < curTime) // Locked for 5 minutes on IP to prevent AH snipping
            entry->lockedIpAddress.clear();

        next = itr;
        ++next;
        if (entry->recoveryBlocked)
            continue;
        if (curTime > (entry->expireTime))
        {
            if (entry->bidder != 0)
            {
                PlayerTransactionData data;
                data.type = "Bid";
                data.parts[0].lowGuid = entry->owner;
                data.parts[0].itemsEntries[0] = entry->itemTemplate;
                Item* item = sAuctionMgr.GetAItem(entry->itemGuidLow);
                data.parts[0].itemsCount[0] = item ? item->GetCount() : 0;
                data.parts[0].itemsGuid[0] = entry->itemGuidLow;
                data.parts[1].lowGuid = entry->bidder;
                data.parts[1].money = entry->bid;
                sWorld.LogTransaction(data);
            }

            // One checked unit: mails, Item ownership and the auction row.
            MailPersistenceStatus const status =
                sAuctionMgr.ProcessExpiredAuction(entry);
            if (status != MailPersistenceStatus::Committed)
            {
                // Keep the listing and its Item for a later attempt; never
                // delete auction/Item rows after a failed unit.
                sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                         "Auction %u expiry/sale failed (status %u); kept",
                         entry->Id, uint32(status));
                continue;
            }

            // Invalidates the ref to itr, cannot call delete on itr->second
            // after removal.
            RemoveAuction(entry);
            delete entry;
            entry = nullptr;
        }
    }
}

void AuctionHouseObject::BuildListBidderItems(WorldPacket& data, Player* player, uint32 listfrom, uint32& count, uint32& totalcount)
{
    for (const auto& itr : AuctionsMap)
    {
        AuctionEntry* auctionEntry = itr.second;
        if (auctionEntry && auctionEntry->bidder == player->GetGUIDLow())
        {
            ++totalcount;

            if (count < 50 && totalcount > listfrom)
                if (itr.second->BuildAuctionInfo(data))
                    ++count;
        }
    }
}

void AuctionHouseObject::BuildListOwnerItems(WorldPacket& data, Player* player, uint32 listfrom, uint32& count, uint32& totalcount)
{
    auto bounds = AccountAuctionMap.equal_range(player->GetSession()->GetAccountId());
    for (auto itr = bounds.first; itr != bounds.second; ++itr)
    {
        AuctionEntry* auctionEntry = itr->second;
        if (auctionEntry && auctionEntry->owner == player->GetGUIDLow())
        {
            ++totalcount;
            if (count < 50 && totalcount > listfrom)
                if (auctionEntry->BuildAuctionInfo(data))
                    ++count;
        }
    }
}

void AuctionHouseObject::BuildListAuctionItems(WorldPacket& data, Player* player,
        AuctionHouseClientQuery const& query,
        uint32& count, uint32& totalcount)
{
    // Happening often, and easy to deal with
    if (query.auctionMainCategory == 0xffffffff && query.auctionSubCategory == 0xffffffff && query.auctionSlotID == 0xffffffff &&
        query.quality == 0xffffffff && query.levelmin == 0x00 && query.levelmax == 0x00 && query.usable == 0x00 && query.wsearchedname.empty())
    {
        totalcount = OrderedAuctionMap.size();
        if (query.listfrom < totalcount)
        {
            auto itr = OrderedAuctionMap.cbegin();
            std::advance(itr, query.listfrom);
            for (; itr != OrderedAuctionMap.cend(); ++itr)
            {
                if (!itr->second->IsAvailableFor(player))
                    continue;

                itr->second->BuildAuctionInfo(data);
                if ((++count) >= 50)
                    break;
            }
        }
        return;
    }

    int loc_idx = player->GetSession()->GetSessionDbLocaleIndex();
    LocaleConstant dbc_loc = player->GetSession()->GetSessionDbcLocale();

    // Micro opt on name/suffix initialization
    std::string name;
    name.reserve(140);

    for (const auto& itr : OrderedAuctionMap)
    {
        AuctionEntry* auctionEntry = itr.second;
        Item *item = sAuctionMgr.GetAItem(auctionEntry->itemGuidLow);
        if (!item)
            continue;

        {
            ItemPrototype const* proto = item->GetProto();

            if (query.auctionMainCategory != 0xffffffff && proto->Class != query.auctionMainCategory)
                continue;

            if (query.auctionSubCategory != 0xffffffff && proto->SubClass != query.auctionSubCategory)
                continue;

            if (query.auctionSlotID != 0xffffffff && proto->InventoryType != query.auctionSlotID &&
                    (query.auctionSlotID != INVTYPE_CHEST || (query.auctionSlotID == INVTYPE_CHEST && proto->InventoryType != INVTYPE_ROBE)))
                continue;

            if (query.quality != 0xffffffff && proto->Quality < query.quality)
                continue;

            if (query.levelmin != 0x00 && (proto->RequiredLevel < query.levelmin || (query.levelmax != 0x00 && proto->RequiredLevel > query.levelmax)))
                continue;

            if (query.usable != 0x00 && player->CanUseItem(item) != EQUIP_ERR_OK)
                continue;

            if (query.usable != 0x00 && proto->Class == ITEM_CLASS_RECIPE)
                if (SpellEntry const* spell = sSpellMgr.GetSpellEntry(proto->Spells[0].SpellId))
                    if (player->HasSpell(spell->EffectTriggerSpell[EFFECT_INDEX_0]))
                        continue;

            // IP locked auction
            if (!auctionEntry->IsAvailableFor(player))
                continue;

            if (!query.wsearchedname.empty())
            {
                name = proto->Name1;
                if (name.empty())
                    continue;

                int32 propertyId = item->GetItemRandomPropertyId();
                ItemRandomPropertiesEntry const* randomProperty = nullptr;
                if (propertyId > 0)
                     randomProperty = sItemRandomPropertiesStore.LookupEntry(static_cast<uint32>(propertyId));

                Item::GetLocalizedNameWithSuffix(name, proto, randomProperty, loc_idx, dbc_loc);

                if (!Utf8FitTo(name, query.wsearchedname))
                    continue;
            }

            if (count < 50 && totalcount >= query.listfrom)
            {
                ++count;
                auctionEntry->BuildAuctionInfo(data);
            }
        }

        ++totalcount;
    }
}

// this function inserts to WorldPacket auction's data
bool AuctionEntry::BuildAuctionInfo(WorldPacket& data) const
{
    Item *pItem = sAuctionMgr.GetAItem(itemGuidLow);
    if (!pItem)
    {
        sLog.Out(LOG_BASIC, LOG_LVL_ERROR, "auction to item, that doesn't exist !!!!");
        return false;
    }
    data << uint32(Id);
    data << uint32(pItem->GetEntry());

    // [-ZERO] no other infos about enchantment in 1.12 [?]
    //for (uint8 i = 0; i < MAX_INSPECTED_ENCHANTMENT_SLOT; ++i)
    //{
    data << uint32(pItem->GetEnchantmentId(EnchantmentSlot(PERM_ENCHANTMENT_SLOT)));
    //    data << uint32(pItem->GetEnchantmentDuration(EnchantmentSlot(i)));
    //    data << uint32(pItem->GetEnchantmentCharges(EnchantmentSlot(i)));
    //}

    data << uint32(pItem->GetItemRandomPropertyId());       // random item property id
    data << uint32(pItem->GetItemSuffixFactor());           // SuffixFactor
    data << uint32(pItem->GetCount());                      // item->count
    data << uint32(pItem->GetSpellCharges());               // item->charge FFFFFFF
    data << ObjectGuid(HIGHGUID_PLAYER, owner);             // Auction->owner
    data << uint32(startbid);                               // Auction->startbid (not sure if useful)
    data << uint32(bid ? GetAuctionOutBid() : 0);           // minimal outbid
    data << uint32(buyout);                                 // auction->buyout
    int64 const timeLeftMs = std::min<int64>(std::max<int64>(0, int64(expireTime - time(nullptr)) * IN_MILLISECONDS), INT32_MAX);
    data << uint32(timeLeftMs);                             // time left
    data << ObjectGuid(HIGHGUID_PLAYER, bidder);            // auction->bidder current
    data << uint32(bid);                                    // current bid
    return true;
}

uint32 AuctionEntry::GetAuctionCut() const
{
    return uint32(auctionHouseEntry->cutPercent * bid * sWorld.getConfig(CONFIG_FLOAT_RATE_AUCTION_CUT) / 100.0f);
}

// The sum of outbid is (1% from current bid)*5, if bid is very small, it is 1c
uint32 AuctionEntry::GetAuctionOutBid() const
{
    uint32 outbid = (bid / 100) * 5;
    if (!outbid)
        outbid = 1;
    return outbid;
}

void AuctionEntry::DeleteFromDB() const
{
    //No SQL injection (Id is integer)
    CharacterDatabase.PExecute("DELETE FROM `auction` WHERE `id` = '%u'", Id);
}

bool AuctionEntry::AppendDeleteFromDB(CharacterPersistenceContext& context) const
{
    Database* database = context.Batch().GetDatabase();
    if (!database)
    {
        context.Invalidate();
        return false;
    }
    static SqlStatementID deleteAuction;
    SqlStatement statement = database->CreateStatement(
        deleteAuction, "DELETE FROM `auction` WHERE `id` = ?");
    statement.addUInt32(Id);
    if (!statement.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
    {
        context.Invalidate();
        return false;
    }
    return true;
}

bool AuctionEntry::AppendSaveToDB(CharacterPersistenceContext& context) const
{
    Database* database = context.Batch().GetDatabase();
    if (!database || !auctionHouseEntry)
    {
        context.Invalidate();
        return false;
    }
    static SqlStatementID insertAuction;
    SqlStatement statement = database->CreateStatement(
        insertAuction,
        "INSERT INTO `auction` (`id`, `house_id`, `item_guid`, `item_id`, `seller_guid`, `buyout_price`, `expire_time`, `buyer_guid`, `last_bid`, `start_bid`, `deposit`) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)");
    statement.addUInt32(Id);
    statement.addUInt32(auctionHouseEntry->houseId);
    statement.addUInt32(itemGuidLow);
    statement.addUInt32(itemTemplate);
    statement.addUInt32(owner);
    statement.addUInt32(buyout);
    statement.addUInt64(uint64(expireTime));
    statement.addUInt32(bidder);
    statement.addUInt32(bid);
    statement.addUInt32(startbid);
    statement.addUInt32(deposit);
    if (!statement.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
    {
        context.Invalidate();
        return false;
    }
    return true;
}

void AuctionEntry::SaveToDB() const
{
    //No SQL injection (no strings)
    CharacterDatabase.PExecute("INSERT INTO `auction` (`id`, `house_id`, `item_guid`, `item_id`, `seller_guid`, `buyout_price`, `expire_time`, `buyer_guid`, `last_bid`, `start_bid`, `deposit`) "
                               "VALUES ('%u', '%u', '%u', '%u', '%u', '%u', '" UI64FMTD "', '%u', '%u', '%u', '%u')",
                               Id, auctionHouseEntry->houseId, itemGuidLow, itemTemplate, owner, buyout, (uint64)expireTime, bidder, bid, startbid, deposit);
}

bool AuctionEntry::IsAvailableFor(Player* player)
{
    if (!lockedIpAddress.empty())
        return lockedIpAddress == player->GetSession()->GetRemoteAddress();

    return true;
}
