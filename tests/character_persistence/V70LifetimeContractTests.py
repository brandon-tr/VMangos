"""v70 source contracts supplementing the runtime lifetime probes."""

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


class V70LifetimeContractTests(unittest.TestCase):
    def test_acknowledgment_detaches_queue_before_state(self):
        item = read("Objects/Item.cpp")
        ack = function_body(item, "bool Item::AcknowledgeSave(Player* owner)")
        self.assertIn("GetItemUpdateQueue", ack)
        self.assertIn("for (Item*& queued : queue)", ack)
        self.assertLess(ack.index("queued = nullptr"),
                        ack.rindex("uState = ITEM_UNCHANGED"))

        save = item[item.index("bool Item::AppendSaveToDB("):
                    item.index("bool Item::LoadFromDB(")]
        self.assertIn("AcknowledgeSave(owner)", save)
        removal = save[save.index("Destructive removal is deferred"):]
        self.assertIn("AcknowledgeSave(owner)", removal)
        self.assertLess(removal.index("AcknowledgeSave(owner)"),
                        removal.index("delete this"))

    def test_unknown_take_quarantines_single_owner(self):
        mail = read("Mail/MailPersistence.cpp")
        quarantine = function_body(mail, "void QuarantineUnknownTake(")
        self.assertIn("master.RemoveMItem", quarantine)
        self.assertIn("GetItemByGuid", quarantine)
        self.assertIn("delete source", quarantine)
        take = function_body(mail, "MailPersistenceStatus TakeMailAttachment(")
        self.assertIn("QuarantineUnknownTake", take)

    def test_expiry_blocks_inside_operation(self):
        mail = read("Mail/MailPersistence.cpp")
        expiry = function_body(mail, "MailPersistenceStatus ProcessExpiredMail(")
        self.assertIn("CharacterPersistenceAdmission admission", expiry)
        self.assertIn("BlockCharacterRecovery", expiry)
        self.assertLess(expiry.index("context.Execute()"),
                        expiry.index("BlockCharacterRecovery"))
        self.assertIn('"expired mail receiver outcome unknown"', expiry)
        self.assertIn('"expired mail sender outcome unknown"', expiry)

        object_mgr = read("ObjectMgr.cpp")
        callback = function_body(object_mgr, "void Callback(std::unique_ptr<QueryResult> result)")
        self.assertIn("operation under admission", callback)

    def test_template_generation_has_explicit_lifetime(self):
        mail = read("Mail/MailPersistence.cpp")
        template = function_body(mail, "void Mail::prepareTemplateItems(")
        self.assertIn("discardGenerated", template)
        self.assertLess(template.index("discardGenerated"),
                        template.index("CharacterPersistenceAdmission"))
        unknown = template[template.index("status == MailPersistenceStatus::OutcomeUnknown"):]
        self.assertIn("masterReceiver->AddMItem", unknown)
        self.assertIn("BlockCharacterRecovery", unknown)
        self.assertIn("generated.clear()", unknown)

    def test_auction_uncertainty_is_not_retried(self):
        auction = read("AuctionHouse/AuctionHouseMgr.cpp")
        header = read("AuctionHouse/AuctionHouseMgr.h")
        self.assertIn("recoveryBlocked", header)
        for signature in ("MailPersistenceStatus AuctionHouseMgr::ProcessExpiredAuction(",
                          "MailPersistenceStatus AuctionHouseMgr::ProcessBuyoutAuction(",
                          "MailPersistenceStatus AuctionHouseMgr::ProcessCancelAuction(",
                          "MailPersistenceStatus AuctionHouseMgr::ProcessOrphanedAuction("):
            body = function_body(auction, signature)
            self.assertIn("recoveryBlocked = true", body)
            self.assertIn("BlockAuctionRecovery", body)

        update = function_body(auction, "void AuctionHouseObject::Update(")
        self.assertIn("if (entry->recoveryBlocked)", update)
        handler = read("Handlers/AuctionHouseHandler.cpp")
        bid = function_body(handler, "void WorldSession::HandleAuctionPlaceBid(")
        self.assertIn("auction->recoveryBlocked = true", bid)
        self.assertIn("if (auction->recoveryBlocked)", bid)


if __name__ == "__main__":
    unittest.main()
