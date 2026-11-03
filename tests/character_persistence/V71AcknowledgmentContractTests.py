"""v71 source contracts for owner-aware acknowledgment and selective completion."""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GAME = ROOT / "src" / "game"


def read(relative):
    return (GAME / relative).read_text(encoding="utf-8")


def body(source, signature):
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
    raise AssertionError(signature)


class V71AcknowledgmentContractTests(unittest.TestCase):
    def test_owner_aware_acknowledgment(self):
        item = read("Objects/Item.cpp")
        ack = body(item, "bool Item::AcknowledgeSave(Player* owner)")
        self.assertIn("owner ? owner : GetOwner()", ack)
        self.assertIn("GetItemUpdateQueue", ack)
        self.assertIn("uState = ITEM_UNCHANGED", ack)

        save = body(item, "bool Item::AppendSaveToDB(CharacterPersistenceContext& context, Player* owner)")
        self.assertIn("AcknowledgeSave(owner)", save)
        self.assertLess(save.index("AcknowledgeSave(owner)"),
                        save.index("delete this"))

    def test_inventory_passes_owner_and_reconciles_selectively(self):
        save = read("Database/CharacterSavePersistence.cpp")
        inventory = body(save, "bool Player::AppendInventoryToDB(")
        self.assertIn("AppendSaveToDB(context, this)", inventory)
        self.assertIn("stagedItemGuids", inventory)
        self.assertIn("AcknowledgeSave(this)", inventory)
        self.assertIn("m_itemUpdateQueue.resize(write)", inventory)
        self.assertNotIn("m_itemUpdateQueue.clear()", inventory)
        self.assertIn("committed inventory acknowledgment failed", inventory)
        self.assertIn("destroyAfterAck", inventory)

    def test_auction_placement_supplies_owner(self):
        handler = read("Handlers/AuctionHouseHandler.cpp")
        sell = body(handler, "void WorldSession::HandleAuctionSellItem(")
        self.assertIn("AppendSaveToDB(context, pl)", sell)


if __name__ == "__main__":
    unittest.main()
