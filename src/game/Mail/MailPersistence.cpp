/*
 * Shared checked mail contract.
 *
 * Every composed mail operation (send, return-to-sender, take item/COD, take
 * money, template-item generation, expired-mail return/disposal) assembles its
 * item rows, ownership changes, mail/mail_items rows and body text into one
 * CharacterPersistenceContext.  Local publication - the online mail list,
 * MasterPlayer item registry, item object destruction, mail object destruction
 * - happens only from commit callbacks.  Join/rejected outcomes publish
 * nothing; unknown outcomes are reported to the caller and never replayed.
 */

#include "Mail/MailPersistence.h"

#include "Database/CharacterIdentityPersistence.h"
#include "Database/CharacterPersistenceRecovery.h"
#include "Database/DatabaseEnv.h"
#include "Database/SqlPreparedStatement.h"
#include "Chat/MasterPlayer.h"
#include "Log.h"
#include "ObjectAccessor.h"
#include "ObjectMgr.h"
#include "Objects/Item.h"
#include "Objects/Player.h"
#include "World.h"
#include "WorldSession.h"

#include <algorithm>
#include <limits>
#include <utility>

namespace
{
struct MailRow
{
    uint32 mailId = 0;
    uint8 messageType = MAIL_NORMAL;
    uint8 stationery = MAIL_STATIONERY_DEFAULT;
    uint16 mailTemplateId = 0;
    uint32 sender = 0;
    uint32 receiver = 0;
    std::string subject;
    uint32 itemTextId = 0;
    bool hasItems = false;
    uint64 expireTime = 0;
    uint64 deliverTime = 0;
    uint32 money = 0;
    uint32 cod = 0;
    uint32 checked = MAIL_CHECK_MASK_NONE;
};

bool AppendMailRow(CharacterPersistenceContext& context, MailRow const& row)
{
    Database* database = context.Batch().GetDatabase();
    if (!database)
    {
        context.Invalidate();
        return false;
    }

    static SqlStatementID insertMail;
    SqlStatement statement = database->CreateStatement(
        insertMail,
        "INSERT INTO `mail` (`id`, `message_type`, `stationery`, `mail_template_id`, `sender_guid`, `receiver_guid`, `subject`, `item_text_id`, `has_items`, `expire_time`, `deliver_time`, `money`, `cod`, `checked`) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)");
    statement.addUInt32(row.mailId);
    statement.addUInt32(row.messageType);
    statement.addUInt32(row.stationery);
    statement.addUInt32(row.mailTemplateId);
    statement.addUInt32(row.sender);
    statement.addUInt32(row.receiver);
    statement.addString(row.subject);
    statement.addUInt32(row.itemTextId);
    statement.addUInt32(row.hasItems ? 1 : 0);
    statement.addUInt64(row.expireTime);
    statement.addUInt64(row.deliverTime);
    statement.addUInt32(row.money);
    statement.addUInt32(row.cod);
    statement.addUInt32(row.checked);
    if (!statement.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
    {
        context.Invalidate();
        return false;
    }
    return true;
}

bool AppendMailItemLink(CharacterPersistenceContext& context, uint32 mailId,
                        uint32 itemGuid, uint32 itemId, uint32 receiverGuid)
{
    Database* database = context.Batch().GetDatabase();
    if (!database)
    {
        context.Invalidate();
        return false;
    }
    static SqlStatementID insertMailItem;
    SqlStatement statement = database->CreateStatement(
        insertMailItem,
        "INSERT INTO `mail_items` (`mail_id`, `item_guid`, `item_id`, `receiver_guid`) VALUES (?, ?, ?, ?)");
    statement.addUInt32(mailId);
    statement.addUInt32(itemGuid);
    statement.addUInt32(itemId);
    statement.addUInt32(receiverGuid);
    if (!statement.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
    {
        context.Invalidate();
        return false;
    }
    return true;
}

bool AppendItemOwnershipTransfer(CharacterPersistenceContext& context,
                                 uint32 itemGuid, uint32 fromOwner,
                                 uint32 toOwner)
{
    Database* database = context.Batch().GetDatabase();
    if (!database)
    {
        context.Invalidate();
        return false;
    }
    if (fromOwner == toOwner)
        return true;
    static SqlStatementID updateOwner;
    SqlStatement statement = database->CreateStatement(
        updateOwner,
        "UPDATE `item_instance` SET `owner_guid` = ? WHERE `guid` = ? AND `owner_guid` = ?");
    statement.addUInt32(toOwner);
    statement.addUInt32(itemGuid);
    statement.addUInt32(fromOwner);
    if (!statement.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
    {
        context.Invalidate();
        return false;
    }
    return true;
}

bool AppendItemDisposal(CharacterPersistenceContext& context, uint32 itemGuid,
                        uint32 ownerGuid)
{
    Database* database = context.Batch().GetDatabase();
    if (!database)
    {
        context.Invalidate();
        return false;
    }
    static SqlStatementID deleteItem;
    SqlStatement statement = database->CreateStatement(
        deleteItem, "DELETE FROM `item_instance` WHERE `guid` = ? AND `owner_guid` = ?");
    statement.addUInt32(itemGuid);
    statement.addUInt32(ownerGuid);
    if (!statement.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::Any))
    {
        context.Invalidate();
        return false;
    }
    return true;
}

CharacterIdentitySnapshot MailActorIdentity(Player const& player)
{
    CharacterIdentitySnapshot identity;
    identity.guid = player.GetGUIDLow();
    identity.accountId = player.GetSession() ? player.GetSession()->GetAccountId() : 0;
    identity.canonicalName = player.GetName();
    identity.generation = player.GetIdentityGeneration();
    return identity;
}

Mail* BuildPublishedMail(MailRow const& row, std::vector<Item*> const& items)
{
    Mail* mail = new Mail;
    mail->messageID = row.mailId;
    mail->messageType = row.messageType;
    mail->stationery = row.stationery;
    mail->mailTemplateId = row.mailTemplateId;
    mail->sender = row.sender;
    mail->receiverGuid = ObjectGuid(HIGHGUID_PLAYER, row.receiver);
    mail->subject = row.subject;
    mail->itemTextId = row.itemTextId;
    mail->has_items = row.hasItems;
    mail->expire_time = time_t(row.expireTime);
    mail->deliver_time = time_t(row.deliverTime);
    mail->money = row.money;
    mail->COD = row.cod;
    mail->checked = row.checked;
    mail->state = MAIL_STATE_UNCHANGED;
    for (Item* item : items)
        mail->AddItem(item->GetGUIDLow(), item->GetEntry());
    mail->has_items = row.hasItems;
    return mail;
}

/// Publishes an online delivery or disposes the staging Item objects.  The
/// staging objects are never destroyed before a confirmed commit.
void PublishDeliveredItems(MailRow const& row, std::vector<Item*> const& items,
                           bool deleteWhenOffline)
{
    MasterPlayer* master = sObjectAccessor.FindMasterPlayer(
        ObjectGuid(HIGHGUID_PLAYER, row.receiver));
    for (Item* item : items)
        item->SetOwnerGuid(ObjectGuid(HIGHGUID_PLAYER, row.receiver));

    if (!master)
    {
        if (deleteWhenOffline)
            for (Item* item : items)
                delete item;
        return;
    }

    Mail* mail = BuildPublishedMail(row, items);
    master->AddNewMailDeliverTime(time_t(row.deliverTime));
    master->AddMail(mail);
    for (Item* item : items)
        master->AddMItem(item);
}

uint32 MailExpireDelay(MailSender const& sender, bool hasItems, uint32 money,
                       uint32 cod)
{
    if (sender.GetMailMessageType() == MAIL_AUCTION && !hasItems && !money)
        return HOUR;
    return cod > 0 ? 3 * DAY : 30 * DAY;
}
struct MailPendingState
{
    uint32 itemTextId = 0;
    bool hasItems = false;
    uint64 expireTime = 0;
    uint64 deliverTime = 0;
    uint32 money = 0;
    uint32 cod = 0;
    uint32 checked = 0;
    std::vector<uint32> removedItems;
};

MailPendingState CapturePendingState(Mail const& mail)
{
    MailPendingState state;
    state.itemTextId = mail.itemTextId;
    state.hasItems = mail.HasItems();
    state.expireTime = uint64(mail.expire_time);
    state.deliverTime = uint64(mail.deliver_time);
    state.money = mail.money;
    state.cod = mail.COD;
    state.checked = mail.checked;
    state.removedItems = mail.removedItems;
    return state;
}

/// Persists the complete captured pending mail mutation so a partial action
/// does not acknowledge earlier unsaved changes.
bool AppendPendingMailState(CharacterPersistenceContext& context,
                            uint32 mailId, uint32 receiverGuid,
                            MailPendingState const& state, uint32 plannedMoney,
                            uint32 plannedCod)
{
    Database* database = context.Batch().GetDatabase();
    if (!database)
    {
        context.Invalidate();
        return false;
    }

    static SqlStatementID updateMail;
    SqlStatement statement = database->CreateStatement(
        updateMail,
        "UPDATE `mail` SET `item_text_id` = ?, `has_items` = ?, `expire_time` = ?, `deliver_time` = ?, `money` = ?, `cod` = ?, `checked` = ? WHERE `id` = ? AND `receiver_guid` = ?");
    statement.addUInt32(state.itemTextId);
    statement.addUInt32(state.hasItems ? 1 : 0);
    statement.addUInt64(state.expireTime);
    statement.addUInt64(state.deliverTime);
    statement.addUInt32(plannedMoney);
    statement.addUInt32(plannedCod);
    statement.addUInt32(state.checked);
    statement.addUInt32(mailId);
    statement.addUInt32(receiverGuid);
    if (!statement.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::Any))
    {
        context.Invalidate();
        return false;
    }

    for (uint32 removedItem : state.removedItems)
    {
        static SqlStatementID deleteRemovedLink;
        SqlStatement link = database->CreateStatement(
            deleteRemovedLink,
            "DELETE FROM `mail_items` WHERE `item_guid` = ? AND `mail_id` = ? AND `receiver_guid` = ?");
        link.addUInt32(removedItem);
        link.addUInt32(mailId);
        link.addUInt32(receiverGuid);
        if (!link.AppendToCheckedBatch(
                context.Batch(), SqlAffectedRowsPolicy::Any))
        {
            context.Invalidate();
            return false;
        }
    }
    return true;
}

/// Applies only the planned changes.  When anything else changed after the
/// snapshot, the remaining dirty state is preserved for a later save.
void PublishPendingMailState(Mail& mail, MailPendingState const& staged,
                             uint32 plannedMoney, uint32 plannedCod,
                             bool removeAttachment, uint32 itemGuid)
{
    bool const snapshotMatches =
        mail.itemTextId == staged.itemTextId &&
        mail.HasItems() == staged.hasItems &&
        uint64(mail.expire_time) == staged.expireTime &&
        uint64(mail.deliver_time) == staged.deliverTime &&
        mail.money == staged.money && mail.COD == staged.cod &&
        mail.checked == staged.checked &&
        mail.removedItems == staged.removedItems;

    mail.money = plannedMoney;
    mail.COD = plannedCod;
    if (removeAttachment)
        mail.RemoveItem(itemGuid);

    if (snapshotMatches)
    {
        mail.removedItems.clear();
        mail.state = MAIL_STATE_UNCHANGED;
    }
    else
        mail.state = MAIL_STATE_CHANGED;     // newer changes remain dirty
}

}

MailPersistenceStatus ClassifyMailBatch(SqlBatchResult const& result)
{
    if (result.outcome == SqlWriteOutcome::OutcomeUnknown)
        return MailPersistenceStatus::OutcomeUnknown;
    if (result.rollbackAttempted &&
        result.rollbackResult.outcome != SqlWriteOutcome::Succeeded)
        return MailPersistenceStatus::OutcomeUnknown;
    if (result.outcome == SqlWriteOutcome::Succeeded &&
        result.stage == SqlBatchStage::Complete)
        return MailPersistenceStatus::Committed;
    return MailPersistenceStatus::Rejected;
}

bool AppendItemTextSave(CharacterPersistenceContext& context, uint32 id,
                        std::string const& text)
{
    Database* database = context.Batch().GetDatabase();
    if (!database || id == 0)
    {
        context.Invalidate();
        return false;
    }
    static SqlStatementID insertItemText;
    SqlStatement statement = database->CreateStatement(
        insertItemText, "INSERT INTO `item_text` (`id`, `text`) VALUES (?, ?)");
    statement.addUInt32(id);
    statement.addString(text);
    if (!statement.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
    {
        context.Invalidate();
        return false;
    }
    return true;
}

bool MailDraft::AppendSendTo(CharacterPersistenceContext& context,
                             MailReceiver const& receiver,
                             MailSender const& sender, MailCheckMask checked,
                             uint32 deliver_delay, uint32 expire_delay,
                             uint32& mailId,
                             std::function<void()> prePublication)
{
    mailId = 0;
    ObjectGuid const receiverGuid = receiver.GetPlayerGuid();
    if (!receiverGuid.IsPlayer() || receiverGuid.GetCounter() == 0 ||
        context.IsInvalid() || context.WasSubmitted())
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

    Player* pReceiver = receiver.GetPlayer();
    bool templatePrepared = false;
    if (pReceiver)
        templatePrepared = prepareItems(pReceiver);

    bool const hasItems = !m_items.empty() || templatePrepared;
    mailId = sObjectMgr.GenerateMailID();
    if (mailId == 0)
    {
        context.Invalidate();
        return false;
    }

    time_t const now = time(nullptr);
    uint64 const deliverTime = uint64(now) + deliver_delay;
    if (!expire_delay)
        expire_delay = MailExpireDelay(sender, hasItems, m_money, m_COD);
    uint64 const expireTime = deliverTime + expire_delay;

    std::string const safeSubject = GetSubject();
    uint32 const bodyId = GetBodyId();
    bool const bodyPending = HasPendingBodyText();
    std::string bodyText = GetPendingBodyText();
    uint32 const money = GetMoney();
    uint32 const cod = GetCOD();
    uint16 const templateId = GetMailTemplateId();

    if (bodyPending &&
        !AppendItemTextSave(context, bodyId, bodyText))
        return false;

    std::vector<Item*> stagedItems;
    stagedItems.reserve(m_items.size());
    for (auto const& entry : m_items)
    {
        Item* item = entry.second;
        if (!item)
        {
            context.Invalidate();
            return false;
        }

        if (!item->AppendSaveToDB(context))
            return false;

        // The mailed item must leave the character inventory in the same unit.
        if (!item->AppendInventoryRemovalToDB(context))
            return false;

        // Ownership follows the mail row, never the full name.
        if (!AppendItemOwnershipTransfer(context, item->GetGUIDLow(),
                                         item->GetOwnerGuid().GetCounter(),
                                         receiverGuid.GetCounter()))
            return false;

        stagedItems.push_back(item);
    }

    MailRow row;
    row.mailId = mailId;
    row.messageType = uint8(sender.GetMailMessageType());
    row.stationery = uint8(sender.GetStationery());
    row.mailTemplateId = templateId;
    row.sender = sender.GetSenderId();
    row.receiver = receiverGuid.GetCounter();
    row.subject = safeSubject;
    row.itemTextId = bodyId;
    row.hasItems = hasItems;
    row.expireTime = expireTime;
    row.deliverTime = deliverTime;
    row.money = money;
    row.cod = cod;
    row.checked = uint32(checked);
    if (!AppendMailRow(context, row))
        return false;
    for (Item* item : stagedItems)
        if (!AppendMailItemLink(context, mailId, item->GetGUIDLow(),
                                item->GetEntry(), row.receiver))
            return false;

    // The caller's local sender-side effects (money deduction, inventory
    // removal) run after the commit and before the receiver publication, so a
    // rejected or unknown unit leaves the sender's state untouched.
    if (prePublication)
        context.OnCommit(std::move(prePublication));
    context.OnCommit([row, stagedItems, bodyPending, bodyId, bodyText]() {
        if (bodyPending)
            sObjectMgr.AddItemText(bodyId, bodyText);
        PublishDeliveredItems(row, stagedItems, true);
    });
    return true;
}

MailPersistenceStatus MailDraft::SendMailTo(MailReceiver const& receiver,
                                             MailSender const& sender,
                                             MailCheckMask checked,
                                             uint32 deliver_delay,
                                             uint32 expire_delay)
{
    ObjectGuid const receiverGuid = receiver.GetPlayerGuid();
    std::vector<uint32> accounts;
    if (Player* online = receiver.GetPlayer())
        if (online->GetSession())
            accounts.push_back(online->GetSession()->GetAccountId());
    if (accounts.empty() && receiverGuid.GetCounter())
    {
        uint32 const account =
            sObjectMgr.GetPlayerAccountIdByGUID(receiverGuid);
        if (account)
            accounts.push_back(account);
    }
    if (sender.GetMailMessageType() == MAIL_NORMAL && sender.GetSenderId())
    {
        uint32 const senderAccount =
            sObjectMgr.GetPlayerAccountIdByGUID(
                ObjectGuid(HIGHGUID_PLAYER, sender.GetSenderId()));
        if (senderAccount)
            accounts.push_back(senderAccount);
    }
    CharacterPersistenceAdmission admission;
    if (!admission.Admit(accounts, AccountLifecycleKind::Mutation))
    {
        sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                 "Mail send admission failed for receiver %u",
                 receiverGuid.GetCounter());
        return MailPersistenceStatus::Rejected;
    }

    CharacterPersistenceContext context(CharacterDatabase,
                                         receiverGuid.GetCounter());
    uint32 mailId = 0;
    if (!AppendSendTo(context, receiver, sender, checked, deliver_delay,
                      expire_delay, mailId))
    {
        sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                 "Mail send assembly failed for receiver %u",
                 receiverGuid.GetCounter());
        return MailPersistenceStatus::Rejected;
    }

    SqlBatchResult const result = context.Execute();
    MailPersistenceStatus const status = ClassifyMailBatch(result);
    if (status == MailPersistenceStatus::OutcomeUnknown)
    {
        uint64 const receiverGeneration =
            receiver.GetPlayer() ? receiver.GetPlayer()->GetIdentityGeneration() : 0;
        uint32 const receiverAccount = receiver.GetPlayer() &&
                receiver.GetPlayer()->GetSession()
            ? receiver.GetPlayer()->GetSession()->GetAccountId()
            : sObjectMgr.GetPlayerAccountIdByGUID(receiverGuid);
        BlockCharacterRecovery(CharacterRecoveryRecord{
            CharacterRecoveryKind::WholeSave, {receiverGuid.GetCounter()},
            {receiverAccount}, receiverGeneration,
            "mail send receiver outcome unknown"});
        if (sender.GetMailMessageType() == MAIL_NORMAL &&
            sender.GetSenderId() &&
            sender.GetSenderId() != receiverGuid.GetCounter())
        {
            uint32 const senderAccount =
                sObjectMgr.GetPlayerAccountIdByGUID(
                    ObjectGuid(HIGHGUID_PLAYER, sender.GetSenderId()));
            BlockCharacterRecovery(CharacterRecoveryRecord{
                CharacterRecoveryKind::WholeSave, {sender.GetSenderId()},
                {senderAccount}, 0,
                "mail send sender outcome unknown"});
        }
    }
    else if (status != MailPersistenceStatus::Committed)
        sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                 "Mail send %u failed (status %u, outcome %u, stage %u)",
                 mailId, uint32(status), uint32(result.outcome),
                 uint32(result.stage));
    return status;
}

MailPersistenceStatus ReturnMailToSender(Player& returner, Mail* mail)
{
    if (!mail || !returner.GetSession())
        return MailPersistenceStatus::Rejected;
    if (mail->state == MAIL_STATE_DELETED || mail->deliver_time > time(nullptr))
        return MailPersistenceStatus::Rejected;

    MasterPlayer* master = returner.GetSession()->GetMasterPlayer();
    if (!master)
        return MailPersistenceStatus::Rejected;

    ObjectGuid const actorGuid = returner.GetObjectGuid();
    if (mail->receiverGuid != actorGuid)
        return MailPersistenceStatus::Rejected;

    CharacterIdentitySnapshot const identity = MailActorIdentity(returner);
    if (identity.generation == 0 ||
        identity.generation == std::numeric_limits<uint64>::max())
        return MailPersistenceStatus::Rejected;

    bool const returnable = mail->messageType == MAIL_NORMAL && mail->sender != 0;
    ObjectGuid const newReceiver(HIGHGUID_PLAYER, mail->sender);
    Player* onlineReceiver = returnable ? sObjectMgr.GetPlayer(newReceiver) : nullptr;
    uint32 receiverAccount = 0;
    if (returnable && onlineReceiver && onlineReceiver->GetSession())
        receiverAccount = onlineReceiver->GetSession()->GetAccountId();
    if (returnable && !receiverAccount)
        receiverAccount = sObjectMgr.GetPlayerAccountIdByGUID(newReceiver);
    bool const deliverable = returnable && (onlineReceiver || receiverAccount);

    std::vector<uint32> affectedAccounts{identity.accountId};
    if (deliverable && receiverAccount)
        affectedAccounts.push_back(receiverAccount);
    CharacterPersistenceAdmission admission;
    if (!admission.Admit(affectedAccounts, AccountLifecycleKind::Mutation))
        return MailPersistenceStatus::Rejected;

    CharacterPersistenceContext context(CharacterDatabase, actorGuid.GetCounter());
    if (!context.AppendIdentityGuards({identity}))
        return MailPersistenceStatus::Rejected;

    Database* database = context.Batch().GetDatabase();
    if (!database)
        return MailPersistenceStatus::Rejected;

    uint32 const oldMailId = mail->messageID;
    uint32 const oldItemTextId = mail->itemTextId;
    std::vector<MailItemInfo> const attachments = mail->items;

    static SqlStatementID deleteMail;
    SqlStatement removeMail = database->CreateStatement(
        deleteMail, "DELETE FROM `mail` WHERE `id` = ? AND `receiver_guid` = ?");
    removeMail.addUInt32(oldMailId);
    removeMail.addUInt32(actorGuid.GetCounter());
    if (!removeMail.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
        return MailPersistenceStatus::Rejected;

    static SqlStatementID deleteMailItems;
    SqlStatement removeLinks = database->CreateStatement(
        deleteMailItems,
        "DELETE FROM `mail_items` WHERE `mail_id` = ? AND `receiver_guid` = ?");
    removeLinks.addUInt32(oldMailId);
    removeLinks.addUInt32(actorGuid.GetCounter());
    if (!removeLinks.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::Any))
        return MailPersistenceStatus::Rejected;

    std::vector<Item*> stagedItems;
    std::vector<uint32> stagedGuids;
    for (MailItemInfo const& attachment : attachments)
    {
        if (deliverable)
        {
            if (!AppendItemOwnershipTransfer(context, attachment.itemGuid,
                                             actorGuid.GetCounter(),
                                             newReceiver.GetCounter()))
                return MailPersistenceStatus::Rejected;
        }
        else if (!AppendItemDisposal(context, attachment.itemGuid,
                                     actorGuid.GetCounter()))
            return MailPersistenceStatus::Rejected;

        // Both return and disposal must account for the live attachment object:
        // transfer it to the receiver or destroy it after commit.
        if (Item* item = master->GetMItem(attachment.itemGuid))
            stagedItems.push_back(item);
        stagedGuids.push_back(attachment.itemGuid);
    }

    uint32 newMailId = 0;
    uint64 deliverTime = 0;
    uint64 expireTime = 0;
    if (deliverable)
    {
        newMailId = sObjectMgr.GenerateMailID();
        if (newMailId == 0)
            return MailPersistenceStatus::Rejected;
        time_t const now = time(nullptr);
        deliverTime = uint64(now);
        expireTime = uint64(now) + 30 * DAY;

        MailRow row;
        row.mailId = newMailId;
        row.messageType = MAIL_NORMAL;
        row.stationery = MAIL_STATIONERY_DEFAULT;
        row.mailTemplateId = mail->mailTemplateId;
        row.sender = actorGuid.GetCounter();
        row.receiver = newReceiver.GetCounter();
        row.subject = mail->subject;
        row.itemTextId = oldItemTextId;
        row.hasItems = !attachments.empty();
        row.expireTime = expireTime;
        row.deliverTime = deliverTime;
        row.money = mail->money;
        row.cod = 0;                            // never pay COD on a return
        row.checked = MAIL_CHECK_MASK_RETURNED;
        if (!AppendMailRow(context, row))
            return MailPersistenceStatus::Rejected;
        for (MailItemInfo const& attachment : attachments)
            if (!AppendMailItemLink(context, newMailId, attachment.itemGuid,
                                    attachment.itemId, newReceiver.GetCounter()))
                return MailPersistenceStatus::Rejected;
    }
    else if (oldItemTextId)
    {
        // Disposed mail: the selected body must not be leaked.
        static SqlStatementID deleteItemText;
        SqlStatement text = database->CreateStatement(
            deleteItemText, "DELETE FROM `item_text` WHERE `id` = ?");
        text.addUInt32(oldItemTextId);
        if (!text.AppendToCheckedBatch(
                context.Batch(), SqlAffectedRowsPolicy::Any))
            return MailPersistenceStatus::Rejected;
    }

    MailRow published;
    if (deliverable)
    {
        published.mailId = newMailId;
        published.messageType = MAIL_NORMAL;
        published.stationery = MAIL_STATIONERY_DEFAULT;
        published.mailTemplateId = mail->mailTemplateId;
        published.sender = actorGuid.GetCounter();
        published.receiver = newReceiver.GetCounter();
        published.subject = mail->subject;
        published.itemTextId = oldItemTextId;
        published.hasItems = !attachments.empty();
        published.expireTime = expireTime;
        published.deliverTime = deliverTime;
        published.money = mail->money;
        published.cod = 0;
        published.checked = MAIL_CHECK_MASK_RETURNED;
    }

    context.OnCommit([master, mail, oldMailId, stagedGuids, stagedItems,
                      deliverable, published]() {
        master->RemoveMail(oldMailId);
        for (uint32 itemGuid : stagedGuids)
            master->RemoveMItem(itemGuid);
        if (deliverable)
            PublishDeliveredItems(published, stagedItems, true);
        else
        {
            for (Item* item : stagedItems)
                delete item;
            master->MarkMailsUpdated();
        }
        delete mail;
    });

    returner.AppendIdentityGenerationPublication(context);

    SqlBatchResult const result = context.Execute();
    MailPersistenceStatus const status = ClassifyMailBatch(result);
    if (status == MailPersistenceStatus::OutcomeUnknown)
    {
        BlockCharacterRecovery(CharacterRecoveryRecord{
            CharacterRecoveryKind::WholeSave, {identity.guid},
            {identity.accountId}, identity.generation,
            "mail return outcome unknown"});
        if (deliverable && newReceiver.GetCounter() != identity.guid)
            BlockCharacterRecovery(CharacterRecoveryRecord{
                CharacterRecoveryKind::WholeSave,
                {newReceiver.GetCounter()}, {receiverAccount}, 0,
                "mail return receiver outcome unknown"});
    }
    return status;
}

namespace
{
/// An unknown take may have committed or rolled back.  Keep exactly one
/// destruction owner: the moved Item stays with the Player when it reached a
/// destination; otherwise it is removed from the mail registry and deleted
/// here.  The participant blocks make the uncertain database state unreachable.
void QuarantineUnknownTake(Player& player, MasterPlayer& master,
                           Item* source, uint32 sourceGuid)
{
    if (!source)
        return;
    bool const ownedByPlayer =
        player.GetItemByGuid(source->GetObjectGuid()) == source ||
        player.GetItemByPos(source->GetBagSlot(), source->GetSlot()) == source;
    master.RemoveMItem(sourceGuid);
    if (ownedByPlayer)
        return;

    source->RemoveFromUpdateQueueOf(&player);
    if (source->IsInWorld())
        source->RemoveFromWorld();
    delete source;
}

struct TakeMoveSnapshot
{
    struct Position
    {
        uint8 bag = 0;
        uint8 slot = 0;
        Item* item = nullptr;
        uint32 count = 0;
        ItemUpdateState state = ITEM_UNCHANGED;
        int16 queuePos = -1;
        std::vector<uint32> values;
    };

    Item* source = nullptr;
    uint32 sourceGuid = 0;
    uint32 sourceEntry = 0;
    uint32 sourceCount = 0;
    ObjectGuid sourceOwner;
    ItemUpdateState sourceState = ITEM_UNCHANGED;
    int16 sourceQueuePos = -1;
    std::vector<uint32> sourceValues;
    std::vector<Position> positions;
    bool captured = false;
};

void CaptureItemValues(Item const& item, std::vector<uint32>& values)
{
    values.resize(ITEM_END);
    for (uint32 i = 0; i < ITEM_END; ++i)
        values[i] = item.GetUInt32Value(i);
}

void RestoreItemValues(Item& item, std::vector<uint32> const& values)
{
    for (uint32 i = 0; i < values.size() && i < ITEM_END; ++i)
        item.SetUInt32Value(i, values[i]);
}

bool CaptureTakeMove(Player& player, Item* source,
                     ItemPosCountVec const& dest, TakeMoveSnapshot& snapshot)
{
    if (!source)
        return false;
    snapshot.source = source;
    snapshot.sourceGuid = source->GetGUIDLow();
    snapshot.sourceEntry = source->GetEntry();
    snapshot.sourceCount = source->GetCount();
    snapshot.sourceOwner = source->GetOwnerGuid();
    snapshot.sourceState = source->GetState();
    snapshot.sourceQueuePos = source->GetQueuePos();
    CaptureItemValues(*source, snapshot.sourceValues);

    for (ItemPosCount const& position : dest)
    {
        TakeMoveSnapshot::Position captured;
        captured.bag = uint8(position.pos >> 8);
        captured.slot = uint8(position.pos & 255);
        captured.item = player.GetItemByPos(captured.bag, captured.slot);
        if (captured.item)
        {
            captured.count = captured.item->GetCount();
            captured.state = captured.item->GetState();
            captured.queuePos = captured.item->GetQueuePos();
            CaptureItemValues(*captured.item, captured.values);
        }
        snapshot.positions.push_back(std::move(captured));
    }
    snapshot.captured = true;
    return true;
}

/// Undoes a locally applied attachment move after a known rejection: every
/// merged stack count/binding, every split-created Item, the source object and
/// its mail registry membership, and the quest counter side effect.
void RollbackTakeMove(Player& player, MasterPlayer& master,
                      TakeMoveSnapshot& snapshot)
{
    if (!snapshot.captured || !snapshot.source)
        return;

    for (TakeMoveSnapshot::Position const& position : snapshot.positions)
    {
        Item* current = player.GetItemByPos(position.bag, position.slot);
        if (position.item)
        {
            if (current == position.item)
            {
                RestoreItemValues(*current, position.values);
                current->SetCount(position.count);
                current->RemoveFromUpdateQueueOf(&player);
                current->FSetState(position.state);
                if (position.state != ITEM_UNCHANGED && position.queuePos >= 0)
                    current->AddToUpdateQueueOf(&player);
            }
            continue;
        }
        if (!current)
            continue;
        if (current->GetGUIDLow() == snapshot.sourceGuid)
        {
            // The source object took this free slot; detach it for the mail.
            player.RemoveItem(position.bag, position.slot, true);
            current->RemoveFromUpdateQueueOf(&player);
        }
        else
        {
            // A split-created clone that never reached the database.
            player.RemoveItem(position.bag, position.slot, true);
            current->RemoveFromUpdateQueueOf(&player);
            if (current->IsInWorld())
                current->RemoveFromWorld();
            delete current;
        }
    }

    Item* source = snapshot.source;
    source->RemoveFromUpdateQueueOf(&player);
    RestoreItemValues(*source, snapshot.sourceValues);
    source->SetCount(snapshot.sourceCount);
    source->SetOwnerGuid(snapshot.sourceOwner);
    source->FSetState(snapshot.sourceState);
    if (snapshot.sourceState != ITEM_UNCHANGED && snapshot.sourceQueuePos >= 0)
        source->AddToUpdateQueueOf(&player);
    master.AddMItem(source);
    player.ItemRemovedQuestCheck(snapshot.sourceEntry, snapshot.sourceCount);
}
}

MailPersistenceStatus TakeMailAttachment(Player& player, Mail* mail,
                                         uint32 itemGuid,
                                         ItemPosCountVec const& dest,
                                         uint32& takenCount,
                                         uint32& takenItemId)
{
    takenCount = 0;
    takenItemId = 0;
    if (!mail || !player.GetSession())
        return MailPersistenceStatus::Rejected;
    MasterPlayer* master = player.GetSession()->GetMasterPlayer();
    if (!master)
        return MailPersistenceStatus::Rejected;
    if (mail->receiverGuid != player.GetObjectGuid() ||
        mail->state == MAIL_STATE_DELETED)
        return MailPersistenceStatus::Rejected;

    Item* item = master->GetMItem(itemGuid);
    if (!item || item->GetOwnerGuid() != player.GetObjectGuid())
        return MailPersistenceStatus::Rejected;

    uint32 const cod = mail->COD;
    if (player.GetMoney() < cod)
        return MailPersistenceStatus::Rejected;

    takenItemId = item->GetEntry();
    takenCount = item->GetCount();

    ObjectGuid const senderGuid(HIGHGUID_PLAYER, mail->sender);
    bool const payCod = cod > 0 && mail->sender != 0;
    uint32 senderAccount = 0;
    if (payCod)
        senderAccount = sObjectMgr.GetPlayerAccountIdByGUID(senderGuid);

    std::vector<uint32> affectedAccounts{player.GetSession()->GetAccountId()};
    if (senderAccount)
        affectedAccounts.push_back(senderAccount);
    CharacterPersistenceAdmission admission;
    if (!admission.Admit(affectedAccounts, AccountLifecycleKind::Mutation))
        return MailPersistenceStatus::Rejected;

    MailPendingState const stagedMail = CapturePendingState(*mail);
    TakeMoveSnapshot move;
    if (!CaptureTakeMove(player, item, dest, move))
        return MailPersistenceStatus::Rejected;

    // The move is gameplay state; every mutation it makes is snapshotted so a
    // known rejection restores the item, merged stacks and the mail registry.
    item->SetState(ITEM_UNCHANGED);
    player.MoveItemToInventory(dest, item, true);

    uint32 const projectedBalance = player.GetMoney() - cod;

    auto reject = [&player, master, &mail, &move, itemGuid]() {
        RollbackTakeMove(player, *master, move);
        sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                 "Mail take attachment rejected for mail %u item %u; local "
                 "move rolled back and ownership restored.",
                 mail->messageID, itemGuid);
        return MailPersistenceStatus::Rejected;
    };

    CharacterIdentitySnapshot const identity = MailActorIdentity(player);
    CharacterPersistenceContext context(CharacterDatabase, identity.guid);
    if (!context.AppendIdentityGuards({identity}))
        return reject();

    if (!player.AppendInventoryToDB(context) ||
        !player.AppendGoldProjectedToDB(context, projectedBalance))
        return reject();

    Database* database = context.Batch().GetDatabase();
    if (!database)
        return reject();

    // Persist the complete captured mail mutation, not only the COD column, so
    // an earlier mark-as-read/expiry change is not acknowledged unsaved.
    if (!AppendPendingMailState(context, mail->messageID, identity.guid,
                                stagedMail, stagedMail.money, 0))
        return reject();

    static SqlStatementID deleteMailItem;
    SqlStatement link = database->CreateStatement(
        deleteMailItem,
        "DELETE FROM `mail_items` WHERE `item_guid` = ? AND `mail_id` = ? AND `receiver_guid` = ?");
    link.addUInt32(itemGuid);
    link.addUInt32(mail->messageID);
    link.addUInt32(identity.guid);
    if (!link.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::ExactlyOne))
        return reject();

    uint32 codMailId = 0;
    MailRow codRow;
    if (payCod && senderAccount != 0)
    {
        codMailId = sObjectMgr.GenerateMailID();
        if (!codMailId)
            return reject();
        codRow.mailId = codMailId;
        codRow.messageType = MAIL_NORMAL;
        codRow.stationery = MAIL_STATIONERY_DEFAULT;
        codRow.sender = identity.guid;
        codRow.receiver = senderGuid.GetCounter();
        codRow.subject = mail->subject;
        codRow.money = cod;
        codRow.checked = MAIL_CHECK_MASK_COD_PAYMENT;
        codRow.deliverTime = uint64(time(nullptr));
        codRow.expireTime = codRow.deliverTime + 30 * DAY;
        if (!AppendMailRow(context, codRow))
            return reject();
    }

    context.OnCommit([&player, mail, master, itemGuid, cod, codMailId, codRow,
                      stagedMail]() {
        master->RemoveMItem(itemGuid);
        PublishPendingMailState(*mail, stagedMail, stagedMail.money, 0, true,
                                itemGuid);
        if (cod)
            player.ModifyMoney(-int32(cod));
        if (codMailId)
            PublishDeliveredItems(codRow, {}, false);
    });
    player.AppendIdentityGenerationPublication(context);

    SqlBatchResult const result = context.Execute();
    MailPersistenceStatus const status = ClassifyMailBatch(result);
    if (status == MailPersistenceStatus::Rejected)
        return reject();
    if (status == MailPersistenceStatus::OutcomeUnknown)
    {
        QuarantineUnknownTake(player, *master, item, itemGuid);
        BlockCharacterRecovery(CharacterRecoveryRecord{
            CharacterRecoveryKind::WholeSave, {identity.guid},
            {identity.accountId}, identity.generation,
            "mail attachment take outcome unknown"});
        if (senderGuid.GetCounter() &&
            senderGuid.GetCounter() != identity.guid)
        {
            uint32 const senderAcc = senderAccount ? senderAccount
                : sObjectMgr.GetPlayerAccountIdByGUID(senderGuid);
            BlockCharacterRecovery(CharacterRecoveryRecord{
                CharacterRecoveryKind::WholeSave,
                {senderGuid.GetCounter()}, {senderAcc}, 0,
                "mail attachment take counterparty outcome unknown"});
        }
    }
    return status;
}

MailPersistenceStatus TakeMailMoney(Player& player, Mail* mail)
{
    if (!mail || !player.GetSession())
        return MailPersistenceStatus::Rejected;
    if (mail->receiverGuid != player.GetObjectGuid() ||
        mail->state == MAIL_STATE_DELETED || mail->money == 0)
        return MailPersistenceStatus::Rejected;
    if (int64(player.GetMoney()) + int64(mail->money) > int64(player.GetMaxMoney()))
        return MailPersistenceStatus::Rejected;

    CharacterPersistenceAdmission admission;
    if (!admission.Admit({player.GetSession()->GetAccountId()},
                         AccountLifecycleKind::Mutation))
        return MailPersistenceStatus::Rejected;

    MailMoneyWithdrawal request;
    request.identity = MailActorIdentity(player);
    request.mailId = mail->messageID;
    request.amount = mail->money;
    request.currentBalance = player.GetMoney();
    request.maximumBalance = player.GetMaxMoney();

    CharacterPersistenceContext context(CharacterDatabase, request.identity.guid);
    uint32 projected = 0;
    if (!AppendMailMoneyWithdrawal(CharacterDatabase, context, request, projected))
        return MailPersistenceStatus::Rejected;

    // Persist the complete captured pending mutation with the withdrawal so an
    // earlier read/expiry change is not lost or falsely acknowledged.
    MailPendingState const stagedMail = CapturePendingState(*mail);
    if (!AppendPendingMailState(context, mail->messageID, request.identity.guid,
                                stagedMail, 0, stagedMail.cod))
        return MailPersistenceStatus::Rejected;

    uint32 const amount = mail->money;
    uint32 const sender = mail->sender;
    context.OnCommit([&player, mail, amount, sender, stagedMail]() {
        player.LogModifyMoney(int32(amount), "Mail",
                              ObjectGuid(HIGHGUID_PLAYER, sender));
        PublishPendingMailState(*mail, stagedMail, 0, stagedMail.cod, false, 0);
    });
    player.AppendIdentityGenerationPublication(context);

    SqlBatchResult const result = context.Execute();
    MailPersistenceStatus const status = ClassifyMailBatch(result);
    if (status == MailPersistenceStatus::OutcomeUnknown)
        BlockCharacterRecovery(CharacterRecoveryRecord{
            CharacterRecoveryKind::WholeSave, {request.identity.guid},
            {request.identity.accountId}, request.identity.generation,
            "mail money take outcome unknown"});
    return status;
}

MailPersistenceStatus ProcessExpiredMail(
    Mail const& mail, std::vector<MailItemInfo> const& attachments,
    bool returnToSender, time_t basetime)
{
    ObjectGuid const returnReceiverPre(HIGHGUID_PLAYER, mail.sender);
    std::vector<uint32> affectedAccounts;
    if (mail.receiverGuid.GetCounter())
    {
        uint32 const account = sObjectMgr.GetPlayerAccountIdByGUID(
            mail.receiverGuid);
        if (account)
            affectedAccounts.push_back(account);
    }
    if (returnReceiverPre.GetCounter())
    {
        uint32 const account = sObjectMgr.GetPlayerAccountIdByGUID(
            returnReceiverPre);
        if (account)
            affectedAccounts.push_back(account);
    }
    CharacterPersistenceAdmission admission;
    if (!affectedAccounts.empty() &&
        !admission.Admit(affectedAccounts, AccountLifecycleKind::Mutation))
        return MailPersistenceStatus::Rejected;

    CharacterPersistenceContext context(CharacterDatabase, mail.messageID);
    Database* database = context.Batch().GetDatabase();
    if (!database)
        return MailPersistenceStatus::Rejected;

    ObjectGuid const returnReceiver(HIGHGUID_PLAYER, mail.sender);
    uint32 const returnAccount = returnReceiver.GetCounter()
        ? sObjectMgr.GetPlayerAccountIdByGUID(returnReceiver) : 0;
    bool const deliverable = returnToSender && returnReceiver.GetCounter() != 0 &&
                             returnAccount != 0;

    if (deliverable)
    {
        static SqlStatementID updateMail;
        SqlStatement statement = database->CreateStatement(
            updateMail,
            "UPDATE `mail` SET `sender_guid` = ?, `receiver_guid` = ?, `expire_time` = ?, `deliver_time` = ?, `cod` = 0, `checked` = ? WHERE `id` = ? AND `receiver_guid` = ?");
        statement.addUInt32(mail.receiverGuid.GetCounter());
        statement.addUInt32(returnReceiver.GetCounter());
        statement.addUInt64(uint64(basetime + 30 * DAY));
        statement.addUInt64(uint64(basetime));
        statement.addUInt32(uint32(MAIL_CHECK_MASK_RETURNED));
        statement.addUInt32(mail.messageID);
        statement.addUInt32(mail.receiverGuid.GetCounter());
        // A re-processed expired mail can already carry these values; the
        // ownership predicate is kept, but changed-row semantics cannot be an
        // exact claim here.
        if (!statement.AppendToCheckedBatch(
                context.Batch(), SqlAffectedRowsPolicy::Any))
            return MailPersistenceStatus::Rejected;

        for (MailItemInfo const& attachment : attachments)
        {
            if (!AppendItemOwnershipTransfer(context, attachment.itemGuid,
                                             mail.receiverGuid.GetCounter(),
                                             returnReceiver.GetCounter()))
                return MailPersistenceStatus::Rejected;

            static SqlStatementID updateLink;
            SqlStatement link = database->CreateStatement(
                updateLink,
                "UPDATE `mail_items` SET `receiver_guid` = ? WHERE `item_guid` = ? AND `mail_id` = ?");
            link.addUInt32(returnReceiver.GetCounter());
            link.addUInt32(attachment.itemGuid);
            link.addUInt32(mail.messageID);
            // Return to the same GUID is a valid no-op (changed rows 0).
            if (!link.AppendToCheckedBatch(
                    context.Batch(), SqlAffectedRowsPolicy::Any))
                return MailPersistenceStatus::Rejected;
        }
    }
    else
    {
        for (MailItemInfo const& attachment : attachments)
            if (!AppendItemDisposal(context, attachment.itemGuid,
                                    mail.receiverGuid.GetCounter()))
                return MailPersistenceStatus::Rejected;

        static SqlStatementID deleteLinks;
        SqlStatement links = database->CreateStatement(
            deleteLinks, "DELETE FROM `mail_items` WHERE `mail_id` = ?");
        links.addUInt32(mail.messageID);
        if (!links.AppendToCheckedBatch(
                context.Batch(), SqlAffectedRowsPolicy::Any))
            return MailPersistenceStatus::Rejected;

        if (mail.itemTextId)
        {
            static SqlStatementID deleteText;
            SqlStatement text = database->CreateStatement(
                deleteText, "DELETE FROM `item_text` WHERE `id` = ?");
            text.addUInt32(mail.itemTextId);
            if (!text.AppendToCheckedBatch(
                    context.Batch(), SqlAffectedRowsPolicy::Any))
                return MailPersistenceStatus::Rejected;
        }

        static SqlStatementID deleteMail;
        SqlStatement remove = database->CreateStatement(
            deleteMail, "DELETE FROM `mail` WHERE `id` = ?");
        remove.addUInt32(mail.messageID);
        if (!remove.AppendToCheckedBatch(
                context.Batch(), SqlAffectedRowsPolicy::Any))
            return MailPersistenceStatus::Rejected;
    }

    SqlBatchResult const result = context.Execute();
    MailPersistenceStatus const status = ClassifyMailBatch(result);
    if (status == MailPersistenceStatus::OutcomeUnknown)
    {
        uint32 const receiverAccount =
            sObjectMgr.GetPlayerAccountIdByGUID(mail.receiverGuid);
        BlockCharacterRecovery(CharacterRecoveryRecord{
            CharacterRecoveryKind::WholeSave,
            {mail.receiverGuid.GetCounter()}, {receiverAccount}, 0,
            "expired mail receiver outcome unknown"});
        if (mail.sender &&
            mail.sender != mail.receiverGuid.GetCounter())
        {
            uint32 const senderAccount =
                sObjectMgr.GetPlayerAccountIdByGUID(
                    ObjectGuid(HIGHGUID_PLAYER, mail.sender));
            BlockCharacterRecovery(CharacterRecoveryRecord{
                CharacterRecoveryKind::WholeSave, {mail.sender},
                {senderAccount}, 0,
                "expired mail sender outcome unknown"});
        }
    }
    return status;
}

void Mail::prepareTemplateItems(Player* receiver, AccountLifecycleLease const* lease)
{
    MasterPlayer* masterReceiver = receiver && receiver->GetSession()
        ? receiver->GetSession()->GetMasterPlayer() : nullptr;
    if (!mailTemplateId || !items.empty() || !masterReceiver)
        return;

    Loot mailLoot(nullptr);
    mailLoot.FillLoot(mailTemplateId, LootTemplates_Mail, receiver, true, true);

    std::vector<Item*> generated;
    uint32 max_slot = mailLoot.GetMaxSlotInLootFor(receiver->GetGUIDLow());
    for (uint32 i = 0; items.empty() && i < max_slot; ++i)
    {
        if (LootItem* lootitem = mailLoot.LootItemInSlot(i, receiver->GetGUIDLow()))
        {
            if (Item* item = Item::CreateItem(lootitem->itemid, lootitem->count,
                                              receiver->GetObjectGuid()))
                generated.push_back(item);
        }
    }

    if (generated.empty())
        return;

    auto discardGenerated = [&generated]() {
        for (Item* item : generated)
        {
            if (item->IsInWorld())
                item->RemoveFromWorld();
            delete item;
        }
        generated.clear();
    };

    CharacterPersistenceAdmission admission;
    if (!receiver->GetSession() ||
        !admission.Admit({receiver->GetSession()->GetAccountId()},
                         AccountLifecycleKind::Mutation, lease))
    {
        discardGenerated();
        return;
    }

    CharacterPersistenceContext context(CharacterDatabase, masterReceiver->GetGUIDLow());
    Database* database = context.Batch().GetDatabase();
    if (!database)
    {
        discardGenerated();
        return;
    }

    static SqlStatementID updateHasItems;
    SqlStatement update = database->CreateStatement(
        updateHasItems, "UPDATE `mail` SET `has_items` = 1 WHERE `id` = ?");
    update.addUInt32(messageID);
    if (!update.AppendToCheckedBatch(
            context.Batch(), SqlAffectedRowsPolicy::Any))
    {
        discardGenerated();
        return;
    }

    for (Item* item : generated)
    {
        if (!item->AppendSaveToDB(context) ||
            !AppendMailItemLink(context, messageID, item->GetGUIDLow(),
                                item->GetEntry(), masterReceiver->GetGUIDLow()))
        {
            discardGenerated();
            return;
        }
    }

    std::vector<MailItemInfo> links;
    for (Item* item : generated)
        links.push_back(MailItemInfo{item->GetGUIDLow(), item->GetEntry()});
    uint32 const mailId = messageID;
    context.OnCommit([this, masterReceiver, generated, links, mailId]() {
        has_items = true;
        for (MailItemInfo const& link : links)
            AddItem(link.itemGuid, link.itemId);
        for (Item* item : generated)
        {
            item->SetOwnerGuid(masterReceiver->GetObjectGuid());
            masterReceiver->AddMItem(item);
            item->SetState(ITEM_UNCHANGED);
        }
        (void)mailId;
    });

    SqlBatchResult const result = context.Execute();
    MailPersistenceStatus const status = ClassifyMailBatch(result);
    if (status == MailPersistenceStatus::Committed)
        return;
    if (status == MailPersistenceStatus::OutcomeUnknown)
    {
        // The database may or may not have committed the generated rows.  Give
        // the generated objects exactly one destruction owner (the MasterPlayer)
        // and keep the account blocked rather than publishing a success state.
        for (Item* item : generated)
        {
            item->SetOwnerGuid(masterReceiver->GetObjectGuid());
            item->FSetState(ITEM_UNCHANGED);
            masterReceiver->AddMItem(item);
        }
        generated.clear();
        BlockCharacterRecovery(CharacterRecoveryRecord{
            CharacterRecoveryKind::WholeSave, {receiver->GetGUIDLow()},
            {receiver->GetSession()->GetAccountId()},
            receiver->GetIdentityGeneration(),
            "mail template generation outcome unknown"});
        sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
                 "Mail template item generation outcome unknown for mail %u",
                 messageID);
        return;
    }

    // Known rejection: the unit rolled back and the generated items were never
    // published.  No recovery block is recorded for a proven rejection.
    discardGenerated();
    sLog.Out(LOG_BASIC, LOG_LVL_ERROR,
             "Mail template item generation failed for mail %u", messageID);
}
