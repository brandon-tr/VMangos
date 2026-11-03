#ifndef NATIVE_MAIL_SQL_GENERATED_H
#define NATIVE_MAIL_SQL_GENERATED_H

// Source-extracted SQL fixture.  NativeMailSqlCoverageTests.py checks these
// literals against the current MailPersistence.cpp bodies; this header is not
// an actual caller execution.

namespace NativeMailSql
{
char const* const ItemTextInsert =
    "INSERT INTO `item_text` (`id`, `text`) VALUES (?, ?)";
char const* const MailItemLinkInsert =
    "INSERT INTO `mail_items` (`mail_id`, `item_guid`, `item_id`, `receiver_guid`) VALUES (?, ?, ?, ?)";
char const* const ItemOwnershipUpdate =
    "UPDATE `item_instance` SET `owner_guid` = ? WHERE `guid` = ? AND `owner_guid` = ?";
char const* const ItemDisposalDelete =
    "DELETE FROM `item_instance` WHERE `guid` = ? AND `owner_guid` = ?";
char const* const MailItemLinkDelete =
    "DELETE FROM `mail_items` WHERE `mail_id` = ?";
char const* const ItemTextDelete =
    "DELETE FROM `item_text` WHERE `id` = ?";
}

#endif
