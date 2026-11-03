import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "sql" / "characters.sql"
MIGRATION = ROOT / "sql" / "migrations" / "20261031010000_characters.sql"

ORIGINAL_TABLES = [
    "auction", "characters", "characters_guid_delete", "characters_item_delete",
    "character_action", "character_aura", "character_battleground_data",
    "character_deleted_items", "character_duplicate_account",
    "character_forgotten_skills", "character_gifts", "character_homebind",
    "character_honor_cp", "character_instance", "character_inventory",
    "character_pet", "character_queststatus", "character_reputation",
    "character_skills", "character_social", "character_spell",
    "character_spell_cooldown", "character_stats", "character_tutorial",
    "corpse", "creature_respawn", "gameobject_respawn", "game_event_status",
    "gm_subsurveys", "gm_surveys", "gm_tickets", "groups", "group_instance",
    "group_member", "guild", "guild_eventlog", "guild_member", "guild_rank",
    "instance", "instance_reset", "item_instance", "item_loot", "item_text",
    "mail", "mail_items", "migrations", "petition", "petition_sign", "pet_aura",
    "pet_spell", "pet_spell_cooldown", "playerbot", "saved_variables", "world",
    "worldstates",
]

HISTORICAL_MIGRATIONS = [
    "20220813085336", "20221001202815", "20241002031703", "20241113131610",
    "20241114131912", "20250428111913", "20250712023524", "20260826140125",
]
REMOVED_TABLES = {"worldstates", "character_duplicate_account"}
ADDED_TABLES = {"account_data", "character_account_data"}
FINAL_TABLES = sorted((set(ORIGINAL_TABLES) - REMOVED_TABLES) | ADDED_TABLES)


def read(path):
    return path.read_text(encoding="utf-8")


def schema_tables(text):
    return re.findall(r"CREATE TABLE IF NOT EXISTS `([^`]+)`", text)


def table_block(text, name):
    match = re.search(
        r"CREATE TABLE IF NOT EXISTS `" + re.escape(name) + r"` \(.*?\) ENGINE=.*?;",
        text,
        re.S,
    )
    if not match:
        raise AssertionError("missing table block: " + name)
    return match.group(0)


class CharacterNameSchemaSqlTests(unittest.TestCase):
    def setUp(self):
        self.schema = read(SCHEMA)
        self.migration = read(MIGRATION)

    def test_original_inventory_is_preserved(self):
        tables = schema_tables(self.schema)
        self.assertEqual(len(tables), 56)
        self.assertEqual([t for t in tables if t != "character_name_ownership"], ORIGINAL_TABLES)
        self.assertEqual(tables.count("character_name_ownership"), 1)

    def test_all_original_tables_are_innodb_compact(self):
        for name in ORIGINAL_TABLES:
            block = table_block(self.schema, name)
            self.assertRegex(block, r"ENGINE=InnoDB\b")
            self.assertRegex(block, r"ROW_FORMAT=COMPACT\b")
            self.assertNotRegex(block, r"ENGINE=MyISAM\b")
            self.assertNotRegex(block, r"ROW_FORMAT=(FIXED|DYNAMIC)\b")

    def test_existing_character_name_storage_is_preserved(self):
        characters = table_block(self.schema, "characters")
        self.assertIn("`name` varchar(25) NOT NULL DEFAULT ''", characters)
        self.assertIn("`deleted_name` varchar(25) DEFAULT NULL", characters)
        self.assertIn("KEY `idx_name` (`name`)", characters)
        self.assertNotIn("UNIQUE KEY `idx_name`", characters)
        self.assertIn("DEFAULT CHARSET=utf8", characters)
        self.assertIn("COMMENT='Player System'", characters)
        self.assertIn("`world_phase_mask` int(11) DEFAULT '0',", characters)
        self.assertIn(
            "`identity_generation` bigint(20) unsigned NOT NULL DEFAULT 1,",
            characters,
        )
        self.assertLess(
            characters.index("`world_phase_mask`"),
            characters.index("`identity_generation`"),
        )
        self.assertLess(
            characters.index("`identity_generation`"),
            characters.index("PRIMARY KEY"),
        )

    def test_gm_ticket_name_width_is_preserved(self):
        gm_tickets = table_block(self.schema, "gm_tickets")
        self.assertIn(
            "`name` varchar(25) NOT NULL COMMENT 'Name of ticket creator'",
            gm_tickets,
        )

    def test_ownership_table_structure(self):
        ownership = table_block(self.schema, "character_name_ownership")
        self.assertIn("`canonical_name` varbinary(75) NOT NULL", ownership)
        self.assertIn("`guid` int(11) unsigned NOT NULL", ownership)
        self.assertIn("PRIMARY KEY (`canonical_name`)", ownership)
        self.assertIn("UNIQUE KEY `uq_character_name_guid` (`guid`)", ownership)
        self.assertIn("CONSTRAINT `fk_character_name_owner` FOREIGN KEY (`guid`)", ownership)
        self.assertIn("REFERENCES `characters` (`guid`)", ownership)
        self.assertIn("ON DELETE RESTRICT ON UPDATE RESTRICT", ownership)
        self.assertIn("ENGINE=InnoDB", ownership)
        self.assertIn("ROW_FORMAT=COMPACT", ownership)
        self.assertNotIn("AUTO_INCREMENT", ownership)
        self.assertNotIn("CASCADE", ownership)
        self.assertNotIn("name` varchar", ownership)

    def test_migration_explicitly_converts_the_full_chain_set(self):
        alters = re.findall(
            r"ALTER TABLE `([^`]+)` ENGINE=InnoDB, ROW_FORMAT=COMPACT;",
            self.migration,
        )
        self.assertEqual(len(alters), 55)
        self.assertEqual(sorted(alters), FINAL_TABLES)
        self.assertNotIn("character_name_ownership", alters)
        for name in REMOVED_TABLES:
            self.assertNotIn(name, alters)
        for name in ADDED_TABLES:
            self.assertIn(name, alters)

    def test_feature_migration_requires_the_full_historical_chain(self):
        first_ddl = self.migration.index("ALTER TABLE `auction`")
        for migration in HISTORICAL_MIGRATIONS:
            self.assertIn(migration, self.migration)
            self.assertLess(self.migration.index(migration), first_ddl)
        self.assertIn("requires all eight historical Character migrations",
                      self.migration)
        self.assertIn("20261030222040", self.migration)
        self.assertLess(self.migration.index("20261030222040"), first_ddl)

    def test_production_required_tables_match_full_chain(self):
        header = read(ROOT / "src" / "game" / "Database" /
                      "CharacterNameSchemaChecks.h")
        segment = header[header.index("tableNames"):
                         header.index("std::string const tables")]
        names = re.findall(r"'([^']+)'", segment)
        self.assertEqual(len(names), 56)
        self.assertEqual(len(set(names)), 56)
        self.assertEqual(set(names), set(FINAL_TABLES) |
                         {"character_name_ownership"})
        self.assertNotIn("worldstates", names)
        self.assertNotIn("character_duplicate_account", names)
        self.assertIn("account_data", names)
        self.assertIn("character_account_data", names)

    def test_base_schema_remains_the_pre_history_inventory(self):
        tables = schema_tables(self.schema)
        self.assertEqual(len(tables), 56)
        self.assertIn("worldstates", tables)
        self.assertIn("character_duplicate_account", tables)
        self.assertNotIn("account_data", tables)
        self.assertNotIn("character_account_data", tables)

    def test_migration_has_prior_width_dependency_before_ddl(self):
        first_ddl = self.migration.index("ALTER TABLE `auction`")
        dependency = self.migration.index("20261030222040")
        self.assertLess(dependency, first_ddl)
        self.assertIn("requires migration 20261030222040", self.migration)

    def test_migration_rejects_nonempty_characters_before_ddl(self):
        guard = self.migration.index("SELECT COUNT(*) INTO v_count FROM `characters`")
        signal = self.migration.index("requires an empty characters table")
        first_ddl = self.migration.index("ALTER TABLE `auction`")
        self.assertLess(guard, signal)
        self.assertLess(signal, first_ddl)

    def test_migration_rejects_nonempty_partial_ownership_before_ddl(self):
        ownership_guard = self.migration.index(
            "SELECT COUNT(*) INTO v_count FROM `character_name_ownership`"
        )
        ownership_signal = self.migration.index("ownership table must be empty")
        first_ddl = self.migration.index("ALTER TABLE `auction`")
        self.assertLess(ownership_guard, ownership_signal)
        self.assertLess(ownership_signal, first_ddl)

    def test_generation_addition_is_conditional_and_not_replaced(self):
        add = self.migration.index(
            "ADD COLUMN `identity_generation` bigint(20) unsigned NOT NULL DEFAULT 1"
        )
        check = self.migration.index(
            "FROM information_schema.COLUMNS\n"
            " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='characters'\n"
            " AND COLUMN_NAME='identity_generation'"
        )
        self.assertLess(check, add)
        self.assertIn("IF v_count = 0 THEN", self.migration[check - 100:add])
        self.assertNotIn("MODIFY COLUMN `identity_generation`", self.migration)
        self.assertNotIn("CHANGE COLUMN `identity_generation`", self.migration)

    def test_ownership_creation_is_idempotent(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS `character_name_ownership`", self.migration)

    def test_engine_and_structure_validation_precede_marker(self):
        marker = self.migration.rindex("INSERT INTO `migrations`")
        engine_validation = self.migration.index("required tables are not all InnoDB")
        generation_validation = self.migration.index("incompatible identity_generation")
        ownership_validation = self.migration.index("incompatible ownership columns")
        primary_validation = self.migration.index("incompatible ownership primary key")
        unique_validation = self.migration.index("incompatible ownership unique key")
        fk_validation = self.migration.index("incompatible ownership foreign key")
        rules_validation = self.migration.index("incompatible ownership foreign-key rules")
        for position in (
            engine_validation,
            generation_validation,
            ownership_validation,
            primary_validation,
            unique_validation,
            fk_validation,
            rules_validation,
        ):
            self.assertLess(position, marker)

    def test_completion_marker_is_last_and_single(self):
        self.assertEqual(self.migration.count("INSERT INTO `migrations`"), 1)
        marker = self.migration.rindex("INSERT INTO `migrations`")
        end_if = self.migration.index("\nEND IF;", marker)
        end_proc = self.migration.index("\nEND??", marker)
        self.assertLess(marker, end_if)
        self.assertLess(end_if, end_proc)
        self.assertIn("20261031010000", self.migration[marker:end_if])

    def test_startup_gates_remain_before_cleanup(self):
        master = (ROOT / "src" / "mangosd" / "Master.cpp").read_text(
            encoding="utf-8")
        world = (ROOT / "src" / "game" / "World.cpp").read_text(
            encoding="utf-8")
        start = master.index("bool Master::_StartDB()")
        end = master.index("// Clear 'online' status", start)
        body = master[start:end]
        self.assertIn('#include "Database/CharacterNameSchema.h"', master)
        self.assertLess(body.index("CheckCharacterNameSchema"),
                        body.index("CheckCharacterNameOwnership"))
        self.assertLess(body.index("CheckCharacterNameOwnership"),
                        body.index("clearOnlineAccounts();"))
        self.assertLess(world.index("CheckCharacterNameSchema"),
                        world.index("CheckCharacterNameOwnership"))

    def test_no_destructive_or_dynamic_data_operations(self):
        forbidden = [
            "DELETE FROM",
            "TRUNCATE",
            "DROP TABLE",
            "RENAME TABLE",
            "UPDATE `characters`",
            "INSERT INTO `characters`",
            "PREPARE ",
            "EXECUTE ",
            "START TRANSACTION",
            "COMMIT;",
            "ROLLBACK;",
        ]
        for token in forbidden:
            self.assertNotIn(token, self.migration)
        for token in ("DROP PROCEDURE IF EXISTS add_migration", "CALL add_migration()"):
            self.assertIn(token, self.migration)


if __name__ == "__main__":
    unittest.main()
