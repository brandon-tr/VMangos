"""v69 correction contracts: row-count semantics, rollback, admission,
auction ownership/status propagation, staged successor rank and expiry fail-closed.

These are source contracts for orchestration that is not linked into the
execution harnesses; the execution behavior of the extracted bodies is covered
by the lifecycle fixtures and the C++ context tests.
"""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GAME = ROOT / "src" / "game"


def read(relative):
    return (GAME / relative).read_text(encoding="utf-8")


def function_body(source, signature):
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace:index + 1]
    raise AssertionError("unterminated " + signature)


class V69CorrectionContractTests(unittest.TestCase):
    def test_changed_row_value_updates_are_not_exactly_one(self):
        save = read("Database/CharacterSavePersistence.cpp")
        row = function_body(save, "bool Player::AppendCharacterRowToDB(")
        self.assertIn("SqlAffectedRowsPolicy::Any", row)
        self.assertNotIn("SqlAffectedRowsPolicy::ExactlyOne", row)

        gold = function_body(save, "bool Player::AppendGoldProjectedToDB(")
        self.assertIn("HasIdentityGuard", gold)
        self.assertIn("SqlAffectedRowsPolicy::Any", gold)
        self.assertNotIn("SqlAffectedRowsPolicy::ExactlyOne", gold)

        mail = read("Mail/MailPersistence.cpp")
        take = function_body(mail, "MailPersistenceStatus TakeMailAttachment(")
        self.assertIn("AppendGoldProjectedToDB", take)
        self.assertIn("AppendPendingMailState", take)
        pending = function_body(mail, "bool AppendPendingMailState(")
        self.assertIn("SqlAffectedRowsPolicy::Any", pending)
        link = take[take.index("DELETE FROM `mail_items`"):]
        self.assertIn("`receiver_guid` = ?", link)
        self.assertIn("SqlAffectedRowsPolicy::ExactlyOne", link)

        expiry = function_body(mail, "MailPersistenceStatus ProcessExpiredMail(")
        self.assertIn("UPDATE `mail_items` SET `receiver_guid` = ?", expiry)
        update = expiry[expiry.index("UPDATE `mail_items`"):]
        self.assertIn("SqlAffectedRowsPolicy::Any", update)

    def test_take_rollback_and_complete_pending_state(self):
        mail = read("Mail/MailPersistence.cpp")
        take = function_body(mail, "MailPersistenceStatus TakeMailAttachment(")
        self.assertIn("CaptureTakeMove", take)
        self.assertIn("RollbackTakeMove", take)
        self.assertLess(take.index("RollbackTakeMove"),
                        take.index("context.Execute()"))
        self.assertNotIn("master->RemoveMItem(itemGuid);\n    item->SetState",
                         take)
        self.assertIn("PublishPendingMailState", take)
        self.assertNotIn("mail->state = MAIL_STATE_UNCHANGED", take)

        money = function_body(mail, "MailPersistenceStatus TakeMailMoney(")
        self.assertIn("CapturePendingState", money)
        self.assertIn("AppendPendingMailState", money)
        self.assertIn("PublishPendingMailState", money)
        self.assertNotIn("mail->money = 0;", money)
        self.assertNotIn("mail->state = MAIL_STATE_UNCHANGED", money)

    def test_send_keeps_local_ownership_and_money_until_commit(self):
        handler = read("Handlers/MailHandler.cpp")
        send = function_body(handler, "void WorldSession::HandleSendMailCallback(")
        self.assertIn("AppendGoldProjectedToDB", send)
        self.assertIn("publishSender", send)
        self.assertIn("MoveItemFromInventory", send)
        self.assertNotIn("AppendInventoryToDB", send)
        # The move/money deduction is inside the commit callback, after the
        # checked send append; it must not run before the unit executes.
        self.assertIn("mailId, publishSender", send)
        self.assertLess(send.index("auto publishSender"), send.index("ModifyMoney"))
        self.assertGreaterEqual(send.count("MAIL_ERR_INTERNAL_ERROR"), 2)

    def test_admission_on_save_and_shared_mail(self):
        save = read("Database/CharacterSavePersistence.cpp")
        for signature in ("void Player::SaveToDB(",
                          "void Player::SaveInventoryAndGoldToDB(",
                          "void Player::SaveGoldToDB("):
            self.assertIn("CharacterPersistenceAdmission", function_body(save, signature))

        master = read("Chat/MasterPlayer.cpp")
        self.assertIn("CharacterPersistenceAdmission",
                      function_body(master, "bool MasterPlayer::SaveChecked("))

        mail = read("Mail/MailPersistence.cpp")
        for signature in ("MailPersistenceStatus MailDraft::SendMailTo(",
                          "MailPersistenceStatus ReturnMailToSender(",
                          "MailPersistenceStatus TakeMailAttachment(",
                          "MailPersistenceStatus TakeMailMoney(",
                          "MailPersistenceStatus ProcessExpiredMail("):
            self.assertIn("CharacterPersistenceAdmission",
                          function_body(mail, signature))

        # Unknown outcomes cover every affected participant, not only the actor.
        send = function_body(mail, "MailPersistenceStatus MailDraft::SendMailTo(")
        self.assertIn("mail send receiver outcome unknown", send)
        self.assertIn("mail send sender outcome unknown", send)
        take = function_body(mail, "MailPersistenceStatus TakeMailAttachment(")
        self.assertIn("mail attachment take counterparty outcome unknown", take)
        ret = function_body(mail, "MailPersistenceStatus ReturnMailToSender(")
        self.assertIn("mail return receiver outcome unknown", ret)

    def test_guild_successor_rank_is_staged_and_completion_has_no_sql(self):
        guild = read("Guild/Guild.cpp")
        append = function_body(guild, "bool Guild::AppendDeletionMemberRemoval(")
        self.assertIn("UPDATE `guild_member` SET `rank` = ?", append)
        self.assertIn("GR_GUILDMASTER", append)
        rank = append[append.index("UPDATE `guild_member` SET `rank` = ?"):]
        self.assertIn("SqlAffectedRowsPolicy::ExactlyOne", rank)

        apply_body = function_body(guild, "void Guild::ApplyDeletionMemberRemoval(")
        self.assertNotIn("ChangeRank", apply_body)
        self.assertNotIn("CreateStatement", apply_body)
        self.assertNotIn("Execute(", apply_body)
        self.assertIn("slot->RankId = GR_GUILDMASTER", apply_body)

        group = read("Group/Group.cpp")
        group_apply = function_body(group, "void Group::ApplyDeletionMemberRemoval(")
        for fragment in ("CreateStatement", "PExecute", "DeleteFromDB",
                         "SaveToDB", "ChangeRank"):
            self.assertNotIn(fragment, group_apply)

        relationships = read("Database/CharacterDeletionRelationships.cpp")
        relation_apply = function_body(
            relationships, "void ApplyCharacterDeletionRelationships(")
        for fragment in ("CreateStatement", "PExecute", "DeleteFromDB",
                         "SaveToDB"):
            self.assertNotIn(fragment, relation_apply)

    def test_auction_owner_is_persisted_and_failed_mail_keeps_listing(self):
        auction = read("AuctionHouse/AuctionHouseMgr.cpp")
        load = function_body(auction, "void AuctionHouseMgr::LoadAuctionItems(")
        self.assertIn("`item_instance`.`owner_guid`", load)
        self.assertIn("persistedOwner", load)
        self.assertNotIn("LoadFromDB(itemGuid, ObjectGuid(),", load)

        won = function_body(auction, "bool AuctionHouseMgr::AppendAuctionWonMail(")
        self.assertIn("AppendSendTo", won)
        self.assertNotIn("RemoveAItem", won)
        expired = function_body(auction, "bool AuctionHouseMgr::AppendAuctionExpiredMail(")
        self.assertIn("AppendSendTo", expired)

        process = function_body(auction, "MailPersistenceStatus AuctionHouseMgr::ProcessExpiredAuction(")
        self.assertIn("AppendAuctionSuccessfulMail", process)
        self.assertIn("AppendAuctionWonMail", process)
        self.assertIn("AppendAuctionExpiredMail", process)
        self.assertIn("AppendDeleteFromDB", process)
        self.assertIn("ClassifyMailBatch", process)
        self.assertNotIn("SendMailTo(", process)
        self.assertLess(process.index("AppendDeleteFromDB"),
                        process.index("context.Execute()"))

        buyout_process = function_body(
            auction, "MailPersistenceStatus AuctionHouseMgr::ProcessBuyoutAuction(")
        for fragment in ("AppendGoldProjectedToDB",
                         "AppendAuctionOutbiddedMail",
                         "AppendAuctionSuccessfulMail",
                         "AppendAuctionWonMail",
                         "AppendDeleteFromDB",
                         "ClassifyMailBatch"):
            self.assertIn(fragment, buyout_process)
        self.assertNotIn("SendMailTo(", buyout_process)
        self.assertLess(buyout_process.index("AppendDeleteFromDB"),
                        buyout_process.index("context.Execute()"))

        orphan_process = function_body(
            auction, "MailPersistenceStatus AuctionHouseMgr::ProcessOrphanedAuction(")
        self.assertIn("AppendDeleteFromDB", orphan_process)
        self.assertNotIn("SendMailTo(", orphan_process)

        cancel_process = function_body(
            auction, "MailPersistenceStatus AuctionHouseMgr::ProcessCancelAuction(")
        for fragment in ("AppendAuctionCancelledToBidderMail",
                         "AppendAuctionCancelledOwnerMail",
                         "AppendDeleteFromDB",
                         "ClassifyMailBatch"):
            self.assertIn(fragment, cancel_process)
        self.assertNotIn("SendMailTo(", cancel_process)

        update = function_body(auction, "void AuctionHouseObject::Update(")
        self.assertIn("MailPersistenceStatus const status", update)
        self.assertIn("ProcessExpiredAuction", update)
        self.assertIn("if (status != MailPersistenceStatus::Committed)", update)
        status_check = update.index("if (status != MailPersistenceStatus::Committed)")
        self.assertLess(status_check, update.index("RemoveAuction(entry)"))

        handler = read("Handlers/AuctionHouseHandler.cpp")
        buyout = function_body(handler, "void WorldSession::HandleAuctionPlaceBid(")
        self.assertIn("MailPersistenceStatus const status", buyout)
        self.assertIn("ProcessBuyoutAuction", buyout)
        self.assertLess(buyout.index("if (status != MailPersistenceStatus::Committed)"),
                        buyout.index("RemoveAuction(auction)"))
        self.assertIn("updateAuctionBid", buyout)
        self.assertNotIn("CharacterDatabase.PExecute", buyout)

        cancel = function_body(handler, "void WorldSession::HandleAuctionRemoveItem(")
        self.assertIn("ProcessCancelAuction", cancel)
        self.assertNotIn("SendAuctionCancelledToBidderMail", cancel)

    def test_expired_mail_attachment_failure_is_not_zero_attachments(self):
        mgr = read("ObjectMgr.cpp")
        callback = function_body(mgr, "void Callback(std::unique_ptr<QueryResult> result)")
        self.assertIn("if (!result)", callback)
        fail_at = callback.index("if (!result)")
        process_at = callback.index("ProcessExpiredMail(")
        self.assertLess(fail_at, process_at)
        self.assertIn("BlockExpiredMailFailure", callback)
        self.assertIn("LEFT JOIN `mail_items`", mgr)
        self.assertIn("GetRowCount() == 0 && hasItems", callback)


if __name__ == "__main__":
    unittest.main()
