#!/usr/bin/env python3
"""Source-contract regression tests for the shared checked mail/COD path.

These are deliberately source-level contract checks: the production bodies are
extracted from the actual files, and every assertion names the concrete defect
or ownership rule it protects.  They are not native MySQL or runtime ownership
evidence; the execution boundaries disclosed in the completion report apply.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAIL_PERSISTENCE = ROOT / "src/game/Mail/MailPersistence.cpp"
MAIL_CPP = ROOT / "src/game/Mail/Mail.cpp"
MASTER_PLAYER = ROOT / "src/game/Chat/MasterPlayer.cpp"
OBJECT_MGR = ROOT / "src/game/ObjectMgr.cpp"


def function_body(source, signature):
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    in_string = in_char = in_line = in_block = False
    escaped = False
    i = brace
    while i < len(source):
        ch = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""
        if in_line:
            if ch == "\n":
                in_line = False
        elif in_block:
            if ch == "*" and nxt == "/":
                in_block = False
                i += 1
        elif in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif in_char:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == "'":
                in_char = False
        else:
            if ch == "/" and nxt == "/":
                in_line = True
                i += 1
            elif ch == "/" and nxt == "*":
                in_block = True
                i += 1
            elif ch == '"':
                in_string = True
            elif ch == "'":
                in_char = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return source[start:i + 1]
        i += 1
    raise AssertionError("unterminated function body: " + signature)


class MailPersistenceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mail_src = MAIL_PERSISTENCE.read_text(encoding="utf-8")
        cls.mail_cpp = MAIL_CPP.read_text(encoding="utf-8")
        cls.master_src = MASTER_PLAYER.read_text(encoding="utf-8")
        cls.objectmgr = OBJECT_MGR.read_text(encoding="utf-8")

    def test_send_stages_body_items_links_and_ownership_in_one_unit(self):
        body = function_body(self.mail_src, "bool MailDraft::AppendSendTo(")
        # Reserved body text, each attachment row, inventory removal, guided
        # ownership transfer, mail row and every link are appended before the
        # single caller-owned Execute.
        self.assertIn("AppendItemTextSave", body)
        self.assertIn("item->AppendSaveToDB(context)", body)
        self.assertIn("AppendInventoryRemovalToDB", body)
        self.assertIn("AppendItemOwnershipTransfer", body)
        self.assertIn("AppendMailRow", body)
        self.assertIn("AppendMailItemLink", body)
        self.assertIn("context.OnCommit", body)
        # Publication must be commit-only: the online mail list is built inside
        # the callback, not from the assembly path.
        publish = body[body.index("context.OnCommit"):]
        self.assertIn("PublishDeliveredItems", publish)
        self.assertLess(body.index("context.OnCommit"), body.index("return true;"))

    def test_send_mail_to_executes_and_classifies_the_same_unit(self):
        body = function_body(self.mail_src, "MailPersistenceStatus MailDraft::SendMailTo(")
        self.assertIn("AppendSendTo", body)
        self.assertIn("context.Execute()", body)
        self.assertIn("ClassifyMailBatch", body)
        self.assertIn("CharacterPersistenceAdmission", body)
        self.assertNotIn("BeginTransaction", body)
        self.assertNotIn("CommitTransaction", body)

    def test_return_handles_every_attachment_and_never_pays_cod(self):
        body = function_body(self.mail_src, "MailPersistenceStatus ReturnMailToSender(")
        # The legacy handler read only the first attachment row; the checked
        # path iterates the complete attachment vector for transfer/disposal.
        self.assertIn("for (MailItemInfo const& attachment : attachments)", body)
        transfer_loop = body[body.index("for (MailItemInfo const& attachment : attachments)"):]
        self.assertIn("AppendItemOwnershipTransfer", transfer_loop)
        self.assertIn("AppendItemDisposal", transfer_loop)
        # Old mail removal is one guarded row plus every link row.
        self.assertIn("DELETE FROM `mail` WHERE `id` = ? AND `receiver_guid` = ?", body)
        self.assertIn("DELETE FROM `mail_items` WHERE `mail_id` = ? AND `receiver_guid` = ?", body)
        # Returned mail must not carry the original COD.
        self.assertIn("row.cod = 0;", body)
        # Missing sender: every item row and the selected body are disposed.
        self.assertIn("AppendItemDisposal", body)
        self.assertIn("DELETE FROM `item_text` WHERE `id` = ?", body)
        self.assertIn("ClassifyMailBatch", body)

    def test_return_publication_is_commit_only_and_unknown_blocks(self):
        body = function_body(self.mail_src, "MailPersistenceStatus ReturnMailToSender(")
        commit_index = body.index("context.OnCommit")
        execute_index = body.index("context.Execute()")
        self.assertLess(commit_index, execute_index)
        callback = body[commit_index:execute_index]
        self.assertIn("master->RemoveMail", callback)
        self.assertIn("PublishDeliveredItems", callback)
        self.assertIn("delete mail", callback)
        self.assertIn("BlockCharacterRecovery", body)
        self.assertNotIn("BeginTransaction", body)
        self.assertNotIn("CommitTransaction", body)

    def test_take_attachment_moves_cod_and_inventory_into_same_batch(self):
        body = function_body(self.mail_src, "MailPersistenceStatus TakeMailAttachment(")
        self.assertIn("AppendInventoryToDB", body)
        self.assertIn("AppendGoldProjectedToDB", body)
        self.assertIn("DELETE FROM `mail_items` WHERE `item_guid` = ? AND `mail_id` = ? AND `receiver_guid` = ?", body)
        self.assertIn("PublishPendingMailState", body[body.index("context.OnCommit"):])
        self.assertIn("AppendMailRow", body)      # COD payment mail to the sender
        self.assertIn("MAIL_CHECK_MASK_COD_PAYMENT", body)
        self.assertIn("BlockCharacterRecovery", body)
        self.assertNotIn("SendMailTo", body)      # no nested independent mail unit

    def test_take_money_uses_checked_withdrawal_and_commit_only_balance(self):
        body = function_body(self.mail_src, "MailPersistenceStatus TakeMailMoney(")
        self.assertIn("AppendMailMoneyWithdrawal", body)
        commit = body[body.index("context.OnCommit"):]
        self.assertIn("LogModifyMoney", commit)
        self.assertIn("PublishPendingMailState", commit)
        self.assertIn("AppendIdentityGenerationPublication", body)
        self.assertIn("BlockCharacterRecovery", body)

    def test_template_items_are_staged_and_published_on_commit(self):
        body = function_body(self.mail_src, "void Mail::prepareTemplateItems(")
        self.assertIn("item->AppendSaveToDB(context)", body)
        self.assertIn("AppendMailItemLink", body)
        self.assertIn("context.OnCommit", body)
        callback = body[body.index("context.OnCommit"):]
        self.assertIn("AddMItem", callback)
        self.assertIn("SetState(ITEM_UNCHANGED)", callback)
        self.assertNotIn("BeginTransaction", body)
        self.assertNotIn("item->SaveToDB()", body)

    def test_master_mail_save_is_checked_and_commit_only(self):
        body = function_body(self.master_src, "bool MasterPlayer::AppendMailsToDB(")
        self.assertIn("context.HasIdentityGuard", body)
        self.assertIn("DELETE FROM `mail` WHERE `id` = ? AND `receiver_guid` = ?", body)
        self.assertIn("DELETE FROM `mail_items` WHERE `mail_id` = ? AND `receiver_guid` = ?", body)
        self.assertIn("DELETE FROM `item_instance` WHERE `guid` = ? AND `owner_guid` = ?", body)
        self.assertIn("DELETE FROM `item_text` WHERE `id` = ?", body)
        callback = body[body.index("context.OnCommit"):]
        self.assertIn("MAIL_STATE_UNCHANGED", callback)
        self.assertIn("delete mail", callback)
        # No dirty flag may be cleared before the callback.
        before_callback = body[:body.index("context.OnCommit")]
        self.assertNotIn("m_mailsUpdated = false", before_callback)

    def test_master_save_entry_points_use_the_checked_contract(self):
        save_db = function_body(self.master_src, "void MasterPlayer::SaveToDB(")
        save_mails = function_body(self.master_src, "void MasterPlayer::SaveMails(")
        save_checked = function_body(self.master_src, "bool MasterPlayer::SaveChecked(")
        self.assertIn("SaveChecked", save_db)
        self.assertIn("SaveChecked", save_mails)
        self.assertIn("AppendIdentityGuards", save_checked)
        self.assertIn("context.Execute()", save_checked)
        self.assertNotIn("BeginTransaction", save_checked)
        self.assertNotIn("CommitTransaction", save_checked)

    def test_expired_mail_returner_handles_all_rows(self):
        body = function_body(self.objectmgr, "void Callback(std::unique_ptr<QueryResult> result)")
        # The single-mail returner callback: iterate every row, never Fetch()[0].
        callback = self.objectmgr[self.objectmgr.index("class SingleMailReturner"):]
        callback = callback[:callback.index("class OldMailsReturner")]
        self.assertIn("NextRow()", callback)
        self.assertIn("attachments.push_back", callback)
        self.assertNotIn("fields2 = result->Fetch();\n\n            item_guid", callback)
        self.assertIn("ProcessExpiredMail", callback)
        self.assertIn("returnToLowGuid != 0", callback)

    def test_expired_mail_query_retains_selected_item_text_id(self):
        callback = self.objectmgr[self.objectmgr.index("class OldMailsReturner"):]
        callback = callback[:callback.index("void ObjectMgr::ReturnOrDeleteOldMails")]
        self.assertIn("m.itemTextId = fields[4].GetUInt32();", callback)
        self.assertIn("ProcessExpiredMail", callback)
        # The empty-mail path disposes text and mail in the checked unit.
        self.assertNotIn(
            'CharacterDatabase.PExecute("DELETE FROM `item_text`', callback)

    def test_expired_mail_processor_covers_return_and_disposal_attachments(self):
        body = function_body(self.mail_src, "MailPersistenceStatus ProcessExpiredMail(")
        self.assertIn("for (MailItemInfo const& attachment : attachments)", body)
        self.assertIn("UPDATE `mail_items` SET `receiver_guid` = ? WHERE `item_guid` = ? AND `mail_id` = ?", body)
        self.assertIn("AppendItemDisposal", body)
        self.assertIn("DELETE FROM `mail_items` WHERE `mail_id` = ?", body)
        self.assertIn("DELETE FROM `item_text` WHERE `id` = ?", body)
        self.assertIn("ClassifyMailBatch", body)
        self.assertNotIn("BeginTransaction", body)

    def test_sendmails_legacy_writes_are_gone(self):
        # The only remaining legacy call syntax in these files must be cleared:
        # no direct PExecute/transactions inside the checked mail path.
        for source, name in ((self.mail_src, "MailPersistence.cpp"),):
            self.assertNotIn("BeginTransaction", source, name)
            self.assertNotIn("CommitTransaction", source, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
