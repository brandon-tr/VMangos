DROP PROCEDURE IF EXISTS add_migration;
DELIMITER ??
CREATE PROCEDURE `add_migration`()
BEGIN
DECLARE v INT DEFAULT 1;
DECLARE v_count BIGINT DEFAULT 0;

SET v = (SELECT COUNT(*) FROM `migrations` WHERE `id`='20261031010000');
IF v = 0 THEN
SET v = (SELECT COUNT(*) FROM `migrations` WHERE `id`='20261030222040');
IF v = 0 THEN
SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='FN-05F requires migration 20261030222040';
END IF;

-- The feature migration applies only after the complete historical Character
-- chain.  The historical files record their markers before their DDL, so this
-- count is a prerequisite gate, not proof that each historical effect exists;
-- the post-DDL structural checks below remain authoritative.
SET v_count = (SELECT COUNT(*) FROM `migrations` WHERE `id` IN (
'20220813085336','20221001202815','20241002031703','20241113131610',
'20241114131912','20250428111913','20250712023524','20260826140125'));
IF v_count <> 8 THEN
SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='FN-05F requires all eight historical Character migrations';
END IF;

-- This migration is only for the approved empty-character realm.
SELECT COUNT(*) INTO v_count FROM `characters`;
IF v_count <> 0 THEN
SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='FN-05F requires an empty characters table';
END IF;

-- A partial attempt may already have created an empty ownership table.
SET v_count = (SELECT COUNT(*) FROM information_schema.TABLES
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='character_name_ownership'
 AND TABLE_TYPE='BASE TABLE');
IF v_count <> 0 THEN
SELECT COUNT(*) INTO v_count FROM `character_name_ownership`;
IF v_count <> 0 THEN
SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='FN-05F ownership table must be empty';
END IF;
END IF;

-- Convert exactly the 55 existing character tables. These ALTERs are
-- intentionally explicit and are safe to repeat after a partial attempt.
ALTER TABLE `auction` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `characters` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `characters_guid_delete` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `characters_item_delete` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `character_action` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `character_aura` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `character_battleground_data` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `character_deleted_items` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `account_data` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `character_forgotten_skills` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `character_gifts` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `character_homebind` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `character_honor_cp` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `character_instance` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `character_inventory` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `character_pet` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `character_queststatus` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `character_reputation` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `character_skills` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `character_social` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `character_spell` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `character_spell_cooldown` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `character_stats` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `character_tutorial` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `corpse` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `creature_respawn` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `gameobject_respawn` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `game_event_status` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `gm_subsurveys` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `gm_surveys` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `gm_tickets` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `groups` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `group_instance` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `group_member` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `guild` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `guild_eventlog` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `guild_member` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `guild_rank` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `instance` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `instance_reset` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `item_instance` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `item_loot` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `item_text` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `mail` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `mail_items` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `migrations` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `petition` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `petition_sign` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `pet_aura` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `pet_spell` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `pet_spell_cooldown` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `playerbot` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `saved_variables` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `world` ENGINE=InnoDB, ROW_FORMAT=COMPACT;
ALTER TABLE `character_account_data` ENGINE=InnoDB, ROW_FORMAT=COMPACT;

-- Add the generation only when absent; an existing incompatible definition is
-- caught by the post-DDL validation below rather than silently replaced.
SET v_count = (SELECT COUNT(*) FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='characters'
 AND COLUMN_NAME='identity_generation');
IF v_count = 0 THEN
ALTER TABLE `characters`
ADD COLUMN `identity_generation` bigint(20) unsigned NOT NULL DEFAULT 1;
END IF;

CREATE TABLE IF NOT EXISTS `character_name_ownership` (
  `canonical_name` varbinary(75) NOT NULL,
  `guid` int(11) unsigned NOT NULL,
  PRIMARY KEY (`canonical_name`),
  UNIQUE KEY `uq_character_name_guid` (`guid`),
  CONSTRAINT `fk_character_name_owner` FOREIGN KEY (`guid`)
    REFERENCES `characters` (`guid`)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8 ROW_FORMAT=COMPACT;

-- Validate actual engines, not just requested ALTER clauses.
SET v_count = (SELECT COUNT(*) FROM information_schema.TABLES
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE' AND ENGINE='InnoDB'
 AND TABLE_NAME IN (
  'auction','characters','characters_guid_delete','characters_item_delete',
  'character_action','character_aura','character_battleground_data',
  'character_deleted_items','account_data',
  'character_forgotten_skills','character_gifts','character_homebind',
  'character_honor_cp','character_instance','character_inventory',
  'character_pet','character_queststatus','character_reputation',
  'character_skills','character_social','character_spell',
  'character_spell_cooldown','character_stats','character_tutorial',
  'corpse','creature_respawn','gameobject_respawn','game_event_status',
  'gm_subsurveys','gm_surveys','gm_tickets','groups','group_instance',
  'group_member','guild','guild_eventlog','guild_member','guild_rank',
  'instance','instance_reset','item_instance','item_loot','item_text',
  'mail','mail_items','migrations','petition','petition_sign','pet_aura',
  'pet_spell','pet_spell_cooldown','playerbot','saved_variables','world',
  'character_account_data','character_name_ownership'));
IF v_count <> 56 THEN
SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='FN-05F required tables are not all InnoDB';
END IF;

SET v_count = (SELECT COUNT(*) FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='characters'
 AND COLUMN_NAME='identity_generation' AND DATA_TYPE='bigint'
 AND COLUMN_TYPE LIKE '%unsigned%' AND IS_NULLABLE='NO'
 AND COLUMN_DEFAULT='1');
IF v_count <> 1 THEN
SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='FN-05F incompatible identity_generation';
END IF;

SET v_count = (SELECT COUNT(*) FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA=DATABASE() AND DATA_TYPE='varchar'
 AND CHARACTER_MAXIMUM_LENGTH=25 AND
 ((TABLE_NAME='characters' AND COLUMN_NAME IN ('name','deleted_name'))
  OR (TABLE_NAME='gm_tickets' AND COLUMN_NAME='name')));
IF v_count <> 3 THEN
SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='FN-05F incompatible naming columns';
END IF;

SET v_count = (SELECT COUNT(*) FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='character_name_ownership'
 AND ((COLUMN_NAME='canonical_name' AND DATA_TYPE='varbinary'
       AND CHARACTER_MAXIMUM_LENGTH=75 AND IS_NULLABLE='NO')
   OR (COLUMN_NAME='guid' AND DATA_TYPE='int'
       AND COLUMN_TYPE LIKE '%unsigned%' AND IS_NULLABLE='NO'))
 AND (SELECT COUNT(*) FROM information_schema.COLUMNS c2
  WHERE c2.TABLE_SCHEMA=DATABASE()
  AND c2.TABLE_NAME='character_name_ownership')=2);
IF v_count <> 2 THEN
SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='FN-05F incompatible ownership columns';
END IF;

SET v_count = (SELECT COUNT(*) FROM information_schema.STATISTICS s
 WHERE s.TABLE_SCHEMA=DATABASE()
 AND s.TABLE_NAME='character_name_ownership'
 AND s.INDEX_NAME='PRIMARY' AND s.COLUMN_NAME='canonical_name'
 AND s.SEQ_IN_INDEX=1 AND s.NON_UNIQUE=0 AND s.SUB_PART IS NULL
 AND (SELECT COUNT(*) FROM information_schema.STATISTICS s2
  WHERE s2.TABLE_SCHEMA=DATABASE()
  AND s2.TABLE_NAME='character_name_ownership'
  AND s2.INDEX_NAME='PRIMARY')=1);
IF v_count <> 1 THEN
SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='FN-05F incompatible ownership primary key';
END IF;

SET v_count = (SELECT COUNT(*) FROM information_schema.STATISTICS s
 WHERE s.TABLE_SCHEMA=DATABASE()
 AND s.TABLE_NAME='character_name_ownership'
 AND s.INDEX_NAME='uq_character_name_guid' AND s.COLUMN_NAME='guid'
 AND s.SEQ_IN_INDEX=1 AND s.NON_UNIQUE=0 AND s.SUB_PART IS NULL
 AND (SELECT COUNT(*) FROM information_schema.STATISTICS s2
  WHERE s2.TABLE_SCHEMA=DATABASE()
  AND s2.TABLE_NAME='character_name_ownership'
  AND s2.INDEX_NAME='uq_character_name_guid')=1);
IF v_count <> 1 THEN
SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='FN-05F incompatible ownership unique key';
END IF;

SET v_count = (SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE k
 WHERE k.TABLE_SCHEMA=DATABASE()
 AND k.TABLE_NAME='character_name_ownership'
 AND k.CONSTRAINT_NAME='fk_character_name_owner'
 AND k.COLUMN_NAME='guid' AND k.REFERENCED_TABLE_SCHEMA=DATABASE()
 AND k.REFERENCED_TABLE_NAME='characters'
 AND k.REFERENCED_COLUMN_NAME='guid'
 AND (SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE k2
  WHERE k2.TABLE_SCHEMA=DATABASE()
  AND k2.TABLE_NAME='character_name_ownership'
  AND k2.CONSTRAINT_NAME='fk_character_name_owner')=1);
IF v_count <> 1 THEN
SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='FN-05F incompatible ownership foreign key';
END IF;

SET v_count = (SELECT COUNT(*) FROM information_schema.REFERENTIAL_CONSTRAINTS
 WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='character_name_ownership'
 AND CONSTRAINT_NAME='fk_character_name_owner'
 AND UPDATE_RULE='RESTRICT' AND DELETE_RULE='RESTRICT');
IF v_count <> 1 THEN
SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='FN-05F incompatible ownership foreign-key rules';
END IF;

-- Record completion only after all DDL and structural validation.
INSERT INTO `migrations` VALUES ('20261031010000');

-- End of migration.
END IF;
END??
DELIMITER ;
CALL add_migration();
DROP PROCEDURE IF EXISTS add_migration;
