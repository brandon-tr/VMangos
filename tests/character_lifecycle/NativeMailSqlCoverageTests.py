"""Source-extracted native SQL coverage for multi-attachment/body mail.

This is not a native database execution: it checks that the current production
mail bodies still contain the multi-row and body-text SQL the future native case
matrix depends on.  The actual native DB case is listed NOT RUN in the report.
"""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "src/game/Mail/MailPersistence.cpp").read_text(encoding="utf-8")


def body(signature, end_signature):
    start = SOURCE.index(signature)
    end = SOURCE.index(end_signature, start)
    return SOURCE[start:end]


class NativeMailSqlCoverageTests(unittest.TestCase):
    def test_send_stages_every_attachment_and_body(self):
        send = body("bool MailDraft::AppendSendTo(",
                    "MailPersistenceStatus MailDraft::SendMailTo(")
        self.assertIn("for (auto const& entry : m_items)", send)
        self.assertIn("AppendItemTextSave(context, bodyId, bodyText)", send)
        self.assertIn("AppendItemOwnershipTransfer", send)
        self.assertIn("AppendMailItemLink", send)
        self.assertIn("PublishDeliveredItems", send)

    def test_expiry_handles_every_attachment_and_body(self):
        expiry = body("MailPersistenceStatus ProcessExpiredMail(",
                      "void Mail::prepareTemplateItems(")
        self.assertEqual(expiry.count("for (MailItemInfo const& attachment : attachments)"),
                         2)
        self.assertIn("AppendItemOwnershipTransfer", expiry)
        self.assertIn("AppendItemDisposal", expiry)
        self.assertIn("DELETE FROM `item_text`", expiry)
        self.assertIn("DELETE FROM `mail_items`", expiry)
        self.assertIn("itemTextId", expiry)

    def test_disposal_covers_item_instance_and_links_are_expiry_owned(self):
        disposal = body("bool AppendItemDisposal(", "bool AppendItemTextSave(")
        self.assertIn("DELETE FROM `item_instance`", disposal)
        expiry = body("MailPersistenceStatus ProcessExpiredMail(",
                      "void Mail::prepareTemplateItems(")
        self.assertIn("DELETE FROM `item_text`", expiry)
        self.assertIn("DELETE FROM `mail_items`", expiry)

    def test_generated_header_matches_current_source(self):
        header = (ROOT / "tests" / "character_lifecycle" /
                  "NativeMailSqlGenerated.h").read_text(encoding="utf-8")
        normalized = re.sub(r"\s+", " ", SOURCE)
        literals = re.findall(r'=\s*"((?:[^"\\]|\\.)*)"\s*;', header)
        self.assertEqual(len(literals), 6)
        for literal in literals:
            self.assertIn(re.sub(r"\s+", " ", literal), normalized)

    def test_ownership_helper_skips_same_owner(self):
        transfer = body("bool AppendItemOwnershipTransfer(",
                        "bool AppendItemDisposal(")
        self.assertIn("if (fromOwner == toOwner)", transfer)
        self.assertLess(transfer.index("if (fromOwner == toOwner)"),
                        transfer.index("CreateStatement"))
        self.assertIn("statement.addUInt32(toOwner)", transfer)
        self.assertIn("statement.addUInt32(itemGuid)", transfer)
        self.assertIn("statement.addUInt32(fromOwner)", transfer)

    def test_corrected_fixture_policies_and_sequence(self):
        cpp = (ROOT / "tests" / "character_lifecycle" /
               "NativeCharacterPersistenceTests.cpp").read_text(encoding="utf-8")
        self.assertIn("statement.addUInt32(toOwner);\n"
                      "    statement.addUInt32(itemGuid);\n"
                      "    statement.addUInt32(fromOwner);", cpp)
        for helper in ("DeleteMailLinks", "DeleteMailBody", "DeleteMailItem"):
            start = cpp.index("bool " + helper + "(")
            end = cpp.index("\n}", start)
            function = cpp[start:end]
            self.assertIn("SqlAffectedRowsPolicy::Any", function,
                          helper + " must use Any")
        success = cpp[cpp.index("void TestMailAttachmentAndBodySql("):
                      cpp.index("void TestUnchangedBalanceAndStaleClaims(")]
        self.assertLess(success.index("AddMailBody(database, insert"),
                        success.index("AddMailOwnership(database, insert, 101"),
                        "body insert precedes ownership")
        self.assertLess(success.index("AddMailOwnership(database, insert, 101"),
                        success.index("AddMailLink(database, insert, 9, 101"),
                        "ownership precedes link")
        self.assertIn("statementResults[0].affectedRows == 2", success)
        self.assertIn("stale owner rejected at ownership statement", success)
        self.assertIn("same-owner unit has no ownership statement", success)
        self.assertIn("late failure rolls ownership back", success)

    def test_native_fixture_has_the_required_tables(self):
        fixture = (ROOT / "tests" / "character_lifecycle" /
                   "NativeSchemaFixture.py").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE item_text", fixture)
        self.assertIn("item_text_id", fixture)
        self.assertIn("mail_items(receiver_guid", fixture)
        self.assertIn("item_guid", fixture)


if __name__ == "__main__":
    unittest.main()
