#ifndef MAIL_PERSISTENCE_H
#define MAIL_PERSISTENCE_H

#include "Database/CharacterPersistenceContext.h"
#include "Mail.h"
#include "Objects/Player.h"

#include <string>
#include <vector>

class MasterPlayer;

enum class MailPersistenceStatus
{
    Committed,
    Rejected,
    OutcomeUnknown
};

/// Classifies one checked mail unit.  Only a confirmed complete commit is
/// Committed; an uncertain commit (unknown write or failed rollback) is
/// OutcomeUnknown and must be reported, never replayed.
MailPersistenceStatus ClassifyMailBatch(SqlBatchResult const& result);

/// Inserts a reserved item_text row as the first statement of the unit.
bool AppendItemTextSave(CharacterPersistenceContext& context, uint32 id,
                        std::string const& text);

/// Returns a mail to its original sender.  Every attachment is transferred, or
/// every item row/link and the body row is disposed when the sender is gone.
/// The checked batch also removes the old mail.  On Committed the Mail object
/// and its transferred Item objects are consumed; callers must not touch them.
MailPersistenceStatus ReturnMailToSender(Player& returner, Mail* mail);

/// Takes exactly one attachment into the player's inventory.  The COD money
/// mail to the sender is inserted in the same unit; no COD is paid on a return.
MailPersistenceStatus TakeMailAttachment(Player& player, Mail* mail,
                                         uint32 itemGuid,
                                         ItemPosCountVec const& dest,
                                         uint32& takenCount,
                                         uint32& takenItemId);

/// Withdraws the mail money into the player's balance in one checked unit.
MailPersistenceStatus TakeMailMoney(Player& player, Mail* mail);

/// Offline expired-mail processing: return the mail to its sender or dispose
/// every attachment, the links and the body text.  No Player is involved.
MailPersistenceStatus ProcessExpiredMail(
    Mail const& mail, std::vector<MailItemInfo> const& attachments,
    bool returnToSender, time_t basetime);

#endif
