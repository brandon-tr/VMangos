#!/usr/bin/env python3
"""Source-contract regressions for composed save, logout and item publication.

Protects the properties that distinguish the checked adoption from the legacy
flow: no nested legacy transaction, guard before row/state appends, queue/dirty
acknowledgment only on commit, commit-only item destruction, and a logout that
does not destroy a MasterPlayer whose staged data was never published.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAVE = ROOT / "src/game/Database/CharacterSavePersistence.cpp"
ITEM = ROOT / "src/game/Objects/Item.cpp"
SESSION = ROOT / "src/game/Server/WorldSession.cpp"
MASTER = ROOT / "src/game/Chat/MasterPlayer.cpp"
ACCESSOR = ROOT / "src/game/ObjectAccessor.cpp"


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


class SaveLogoutContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.save = SAVE.read_text(encoding="utf-8")
        cls.item = ITEM.read_text(encoding="utf-8")
        cls.session = SESSION.read_text(encoding="utf-8")
        cls.master = MASTER.read_text(encoding="utf-8")
        cls.accessor = ACCESSOR.read_text(encoding="utf-8")

    def test_save_has_one_execute_and_no_legacy_transaction(self):
        body = function_body(self.save, "void Player::SaveToDB(")
        self.assertEqual(body.count("context.Execute()"), 1)
        self.assertNotIn("BeginTransaction", body)
        self.assertNotIn("CommitTransaction", body)
        self.assertLess(body.index("AppendIdentityGuards"), body.index("AppendSaveToDB("))
        self.assertLess(body.index("AppendSaveToDB("), body.index("context.Execute()"))

    def test_save_blocks_unknown_and_never_replays(self):
        body = function_body(self.save, "void Player::SaveToDB(")
        self.assertIn("OutcomeUnknown", body)
        self.assertIn("BlockCharacterRecovery", body)
        self.assertNotIn("for (", body[body.index("BlockCharacterRecovery"):body.index("if (Pet* pet")])

    def test_save_skips_disabled_bots_and_recovery_blocks_before_assembly(self):
        body = function_body(self.save, "void Player::SaveToDB(")
        disabled = body.index("IsSavingDisabled()")
        guard = body.index("AppendIdentityGuards")
        self.assertLess(disabled, guard)
        self.assertLess(body.index("IsCharacterRecoveryBlocked"), guard)

    def test_inventory_queue_clear_is_commit_only(self):
        body = function_body(self.save, "bool Player::AppendInventoryToDB(")
        commit = body.index("context.OnCommit")
        before = body[:commit]
        self.assertNotIn("m_itemUpdateQueue.clear()", before)
        self.assertIn("stagedItemGuids", body[commit:])
        self.assertIn("AcknowledgeSave(this)", body[commit:])
        self.assertIn("m_itemUpdateQueue.resize(write)", body[commit:])
        self.assertNotIn("m_itemUpdateQueue.clear()", body)

    def test_item_destruction_is_commit_only(self):
        body = function_body(self.item, "bool Item::AppendSaveToDB(")
        changed = body.index("if (uState == ITEM_CHANGED &&")
        removed = body[body.index("if (uState == ITEM_REMOVED)"):changed]
        commit = removed.index("context.OnCommit")
        self.assertLess(commit, removed.index("delete this"))
        self.assertIn("RemoveFromWorld", removed[commit:])
        ack = body[body.index("context.OnCommit", changed):]
        self.assertIn("AcknowledgeSave(owner)", ack)
        self.assertIn("SetLootState(ITEM_LOOT_UNCHANGED)", ack)

    def test_logout_saves_master_before_destruction_only_if_unpublished(self):
        body = function_body(self.session, "void WorldSession::LogoutPlayer(")
        pending = body.index("HasPendingSave()")
        save = body.index("m_masterPlayer->SaveToDB()", pending)
        destroy = body.index("delete m_masterPlayer", pending)
        self.assertLess(pending, save)
        self.assertLess(save, destroy)
        self.assertIn("m_masterPlayer = nullptr", body[destroy:])

    def test_composed_save_stages_master_actions_and_mail(self):
        body = function_body(self.save, "bool Player::AppendSaveToDB(")
        self.assertIn("master->AppendActionsToDB(context)", body)
        self.assertIn("master->AppendMailsToDB(context)", body)
        self.assertLess(body.index("AppendActionsToDB"),
                        body.index("AppendIdentityGenerationPublication"))

    def test_force_logout_and_autosave_use_the_composed_save(self):
        self.assertIn("GetPlayer()->SaveToDB()", self.session)
        save_all = function_body(self.accessor, "void ObjectAccessor::SaveAllPlayers(")
        self.assertIn("SaveToDB()", save_all)
        self.assertNotIn("BeginTransaction", save_all)

    def test_master_checked_save_is_single_execution(self):
        body = function_body(self.master, "bool MasterPlayer::SaveChecked(")
        self.assertEqual(body.count("context.Execute()"), 1)
        self.assertNotIn("BeginTransaction", body)
        self.assertNotIn("CommitTransaction", body)
        self.assertIn("AppendIdentityGuards", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
