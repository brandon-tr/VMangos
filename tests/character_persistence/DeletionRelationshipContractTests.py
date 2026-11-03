#!/usr/bin/env python3
"""Source-contract regressions for deletion relationship persistence.

Protects the properties that are easy to regress while editing the plan/apply
seams: relationship SQL is appended before commit, local apply submits no SQL,
corpse rows are deleted in the same unit, and the disband instance hand-off to a
remaining member is staged before the local bind publication.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAYER = ROOT / "src/game/Objects/Player.cpp"
RELATIONSHIPS = ROOT / "src/game/Database/CharacterDeletionRelationships.cpp"
GUILD = ROOT / "src/game/Guild/Guild.cpp"
GROUP = ROOT / "src/game/Group/Group.cpp"
GUILD_MGR = ROOT / "src/game/Guild/GuildMgr.cpp"
CORPSE = ROOT / "src/game/Objects/Corpse.cpp"
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


class DeletionRelationshipContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.player = PLAYER.read_text(encoding="utf-8")
        cls.relationships = RELATIONSHIPS.read_text(encoding="utf-8")
        cls.guild = GUILD.read_text(encoding="utf-8")
        cls.group = GROUP.read_text(encoding="utf-8")
        cls.guild_mgr = GUILD_MGR.read_text(encoding="utf-8")
        cls.corpse = CORPSE.read_text(encoding="utf-8")
        cls.accessor = ACCESSOR.read_text(encoding="utf-8")

    def test_deletion_appends_relationships_before_execute(self):
        body = function_body(self.player, "bool Player::DeleteFromDB(")
        self.assertIn("AppendCharacterDeletionRelationships", body)
        self.assertLess(body.index("AppendCharacterDeletionRelationships"),
                        body.rindex("context.Execute()"))
        self.assertLess(body.rindex("context.Execute()"),
                        body.index("ApplyCharacterDeletionRelationships"))
        # The legacy independent relationship calls must not reappear.
        for legacy in ("guild->DelMember", "guild->Disband()",
                       "RemoveFromGroup(group", "RemovePetitionsAndSigns(playerGuid)"):
            self.assertNotIn(legacy, body)

    def test_corpse_row_is_deleted_inside_the_unit(self):
        body = function_body(self.relationships,
                             "bool AppendCharacterDeletionRelationships(")
        self.assertIn("DELETE FROM `corpse` WHERE `player_guid` = ? AND `corpse_type` <> 0", body)
        self.assertLess(body.index("DELETE FROM `corpse`"),
                        body.index("AppendDeletionMemberRemoval"))
        apply_body = function_body(self.player, "bool Player::DeleteFromDB(")
        self.assertIn("ConvertCorpseForPlayer(playerGuid, nullptr, false)", apply_body)

    def test_corpse_conversion_does_not_resubmit_the_row_delete(self):
        convert = function_body(self.accessor, "void ObjectAccessor::ConvertCorpseForPlayer(")
        self.assertIn("MarkDeletedFromDB", convert)
        delete = function_body(self.corpse, "void Corpse::DeleteFromDB(")
        self.assertIn("if (m_dbRowDeleted)", delete)
        self.assertIn("return;", delete)

    def test_guild_append_is_guarded_and_apply_is_local(self):
        append = function_body(self.guild, "bool Guild::AppendDeletionMemberRemoval(")
        self.assertIn("UPDATE `guild` SET `leader_guid` = ? WHERE `guild_id` = ? AND `leader_guid` = ?", append)
        self.assertIn("DELETE FROM `guild_member` WHERE `guid` = ? AND `guild_id` = ?", append)
        self.assertIn("DELETE FROM `guild` WHERE `guild_id` = ?", append)
        self.assertNotIn("BeginTransaction", append)
        apply = function_body(self.guild, "void Guild::ApplyDeletionMemberRemoval(")
        for sql in ("UPDATE", "DELETE", "INSERT"):
            self.assertNotIn(sql, apply)

    def test_group_succession_stages_leader_and_instance_rows(self):
        append = function_body(self.group, "bool Group::AppendDeletionMemberRemoval(")
        self.assertIn("DELETE FROM `group_member` WHERE `member_guid` = ? AND `group_id` = ?", append)
        self.assertIn("UPDATE `groups` SET `leader_guid` = ? WHERE `group_id` = ? AND `leader_guid` = ?", append)
        self.assertIn("UPDATE `group_instance` SET `leader_guid` = ? WHERE `leader_guid` = ?", append)
        self.assertIn("INSERT INTO `group_instance`", append)
        self.assertNotIn("BeginTransaction", append)
        apply = function_body(self.group, "void Group::ApplyDeletionMemberRemoval(")
        self.assertNotIn("CharacterDatabase", apply)
        self.assertIn("BindToInstance", apply)     # load=true only

    def test_group_disband_hands_instance_to_remaining_member_in_batch(self):
        append = function_body(self.group, "bool Group::AppendDeletionMemberRemoval(")
        self.assertIn("INSERT INTO `character_instance` (`guid`, `instance`, `permanent`) VALUES (?, ?, ?)", append)
        self.assertIn("DELETE FROM `group_instance` WHERE `leader_guid` = ? AND `instance` = ?", append)
        # The transferred bind is excluded from the reset list and published
        # locally via BindToInstance(load=true).
        self.assertIn("plan.hasTransferBind && bind.first == plan.transferBind.mapId", append)
        apply = function_body(self.group, "void Group::ApplyDeletionMemberRemoval(")
        self.assertIn("plan.hasTransferBind", apply)
        self.assertIn("/*load=*/true", apply)

    def test_petition_cleanup_is_checked_and_local_removal_is_separate(self):
        append = function_body(self.guild_mgr, "bool GuildMgr::AppendDeletionPetitionCleanup(")
        self.assertIn("DELETE FROM `petition` WHERE `owner_guid` = ?", append)
        self.assertIn("DELETE FROM `petition_sign` WHERE `owner_guid` = ? OR `player_guid` = ?", append)
        self.assertNotIn("BeginTransaction", append)
        apply = function_body(self.guild_mgr, "void GuildMgr::ApplyDeletionPetitionCleanup(")
        self.assertIn("DeleteSignatureByPlayer", apply)
        self.assertNotIn("CharacterDatabase", apply)

    def test_relationship_files_have_no_legacy_transactions(self):
        self.assertNotIn(
            "BeginTransaction",
            function_body(self.relationships,
                          "bool AppendCharacterDeletionRelationships("))
        self.assertNotIn(
            "CommitTransaction",
            function_body(self.relationships,
                          "bool AppendCharacterDeletionRelationships("))
        for signature in ("bool GuildMgr::AppendDeletionPetitionCleanup(",
                          "void GuildMgr::ApplyDeletionPetitionCleanup("):
            body = function_body(self.guild_mgr, signature)
            self.assertNotIn("BeginTransaction", body, signature)
            self.assertNotIn("CommitTransaction", body, signature)


if __name__ == "__main__":
    unittest.main(verbosity=2)
