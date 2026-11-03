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

/**
 * @addtogroup mailing
 * @{
 *
 * @file Mail.cpp
 * This file contains the the code needed for MaNGOS to handle mails.
 *
 */

#include "Mail.h"
#include "WorldSession.h"
#include "Opcodes.h"
#include "Log.h"
#include "World.h"
#include "ObjectMgr.h"
#include "ObjectGuid.h"
#include "Player.h"
#include "Item.h"
#include "AuctionHouseMgr.h"
#include "MasterPlayer.h"

/**
 * Creates a new MailSender object.
 *
 * @param sender The object/player sending this mail.
 * @param stationery The stationary associated with this sender.
 */
MailSender::MailSender(Object* sender, MailStationery stationery) : m_stationery(stationery)
{
    switch (sender->GetTypeId())
    {
        case TYPEID_UNIT:
            m_messageType = MAIL_CREATURE;
            m_senderId = sender->GetEntry();
            break;
        case TYPEID_GAMEOBJECT:
            m_messageType = MAIL_GAMEOBJECT;
            m_senderId = sender->GetEntry();
            break;
        case TYPEID_ITEM:
        case TYPEID_CONTAINER:
            m_messageType = MAIL_ITEM;
            m_senderId = sender->GetEntry();
            break;
        case TYPEID_PLAYER:
            m_messageType = MAIL_NORMAL;
            m_senderId = sender->GetGUIDLow();
            break;
        default:
            m_messageType = MAIL_NORMAL;
            m_senderId = 0;                                 // will show mail from nonexistent player
            sLog.Out(LOG_BASIC, LOG_LVL_ERROR, "MailSender::MailSender - Mail have unexpected sender typeid (%u)", sender->GetTypeId());
            break;
    }
}
/**
 * Creates a new MailSender object from an AuctionEntry.
 *
 * @param sender the AuctionEntry from which this mail is generated.
 */
MailSender::MailSender(AuctionEntry* sender)
    : m_messageType(MAIL_AUCTION), m_senderId(sender->GetHouseId()), m_stationery(MAIL_STATIONERY_AUCTION)
{
}

/**
 * Creates a new MailReceiver object.
 *
 * @param receiver The player receiving the mail.
 */
MailReceiver::MailReceiver(Player* receiver) : m_receiver(receiver), m_receiver_guid(receiver->GetObjectGuid())
{
}
/**
 * Creates a new MailReceiver object with a specified GUID.
 *
 * @param receiver The player receiving the mail.
 * @param receiver_lowguid The GUID to use instead of the receivers.
 */
MailReceiver::MailReceiver(Player* receiver, ObjectGuid receiver_guid) : m_receiver(receiver), m_receiver_guid(receiver_guid)
{
    MANGOS_ASSERT(!receiver || receiver->GetObjectGuid() == receiver_guid);
}

/**
* Creates a new MailDraft object using mail template id.
*
* @param mailTemplateId The ID of the Template to be used.
* @param a boolean specifying whether the mail needs items or not.
*
*/
MailDraft::MailDraft(uint16 mailTemplateId, bool need_items, LocaleConstant locale_idx) : m_mailTemplateId(mailTemplateId), m_mailTemplateItemsNeed(need_items), m_bodyId(0), m_bodyTextPending(false), m_money(0), m_COD(0)
{
#if SUPPORTED_CLIENT_BUILD <= CLIENT_BUILD_1_9_4
    std::string text = sObjectMgr.GetMailTextTemplate(mailTemplateId, locale_idx);
    if (!text.empty())
    {
        m_bodyId = sObjectMgr.GenerateItemTextID();
        m_bodyText = text;
        m_bodyTextPending = true;
    }
#endif
}

/**
 * Creates a new MailDraft object using subject and contect texts.
 *
 * @param subject The subject of the mail.
 * @param itemText The text of the body of the mail.
 */
MailDraft::MailDraft(std::string subject, std::string text) : m_mailTemplateId(0), m_mailTemplateItemsNeed(false), m_subject(subject),
    m_bodyId(!text.empty() ? sObjectMgr.GenerateItemTextID() : 0), m_bodyTextPending(!text.empty()), m_bodyText(text), m_money(0), m_COD(0)
{

}

MailDraft& MailDraft::SetSubjectAndBodyId(std::string subject, uint32 itemTextId)
{
    m_subject = subject;

    MANGOS_ASSERT(!m_bodyId);
    m_bodyId = itemTextId;
    m_bodyTextPending = false;
    m_bodyText.clear();

    return *this;
}

MailDraft& MailDraft::SetSubjectAndBody(std::string subject, std::string text)
{
    m_subject = subject;

    MANGOS_ASSERT(!m_bodyId);
    m_bodyId = !text.empty() ? sObjectMgr.GenerateItemTextID() : 0;
    m_bodyText = text;
    m_bodyTextPending = !text.empty();

    return *this;
}

/**
 * Adds an item to the MailDraft.
 *
 * @param item The item to be added to the MailDraft.
 * @returns the MailDraft the item was added to.
 */
MailDraft& MailDraft::AddItem(Item* item)
{
    m_items[item->GetGUIDLow()] = item;
    return *this;
}
/**
 * Prepares the items in a MailDraft.
 */
bool MailDraft::prepareItems(Player* receiver)
{
    if (!m_mailTemplateId || !m_mailTemplateItemsNeed)
        return false;

    m_mailTemplateItemsNeed = false;

    Loot mailLoot(nullptr);

    // can be empty
    mailLoot.FillLoot(m_mailTemplateId, LootTemplates_Mail, receiver, true, true);

    uint32 max_slot = mailLoot.GetMaxSlotInLootFor(receiver->GetGUIDLow());
    for (uint32 i = 0; m_items.empty() && i < max_slot; ++i)
    {
        if (LootItem* lootitem = mailLoot.LootItemInSlot(i, receiver->GetGUIDLow()))
        {
            if (Item* item = Item::CreateItem(lootitem->itemid, lootitem->count, receiver->GetObjectGuid()))
            {
                // Staged by the composing checked mail operation; persisting it
                // here would split the mail row and its attachment.
                AddItem(item);
            }
        }
    }

    return true;
}
/**
 * Deletes the items included in a MailDraft.
 *
 * @param inDB A boolean specifying whether the change should be saved to the database or not.
 */
void MailDraft::deleteIncludedItems(bool inDB /**= false*/)
{
    for (const auto& itr : m_items)
    {
        Item* item = itr.second;

        if (inDB)
            CharacterDatabase.PExecute("DELETE FROM `item_instance` WHERE `guid`='%u'", item->GetGUIDLow());

        delete item;
    }

    m_items.clear();
}
/**
 * Clone MailDraft from another MailDraft.
 *
 * @param draft Point to source for draft cloning.
 */
void MailDraft::CloneFrom(MailDraft const& draft)
{
    m_mailTemplateId = draft.GetMailTemplateId();
    m_mailTemplateItemsNeed = draft.m_mailTemplateItemsNeed;

    m_subject = draft.GetSubject();

    MANGOS_ASSERT(!m_bodyId);
    if (draft.HasPendingBodyText())
    {
        // The prototype body has not been inserted yet; copy its reserved text
        // into a new reserved id instead of reading a not-yet-published row.
        if (uint32 bodyId = draft.GetBodyId())
        {
            m_bodyId = sObjectMgr.GenerateItemTextID();
            m_bodyText = draft.GetPendingBodyText();
            m_bodyTextPending = true;
        }
    }
    else if (uint32 bodyId = draft.GetBodyId())
    {
        std::string text = sObjectMgr.GetItemText(bodyId);
        m_bodyId = sObjectMgr.GenerateItemTextID();
        m_bodyText = text;
        m_bodyTextPending = true;
    }

    m_money = draft.GetMoney();
    m_COD = draft.GetCOD();

    for (const auto& itr : draft.m_items)
    {
        Item* item = itr.second;

        if (Item* newitem = item->CloneItem(item->GetCount()))
            AddItem(newitem);       // staged by the composing checked send
    }
}

/*
 * Returns a mail to its sender.
 * @param sender_acc           The id of the account of the sender.
 * @param sender_guid          The low part of the GUID of the sender.
 * @param receiver_guid        The low part of the GUID of the reciever.
 */
// MailDraft::SendReturnToSender / SendMailTo and their checked-batch contract
// live in Mail/MailPersistence.cpp.

// Mail::prepareTemplateItems lives in Mail/MailPersistence.cpp with its checked
// attachment staging and commit-only publication.

/*! @} */
