DROP PROCEDURE IF EXISTS add_migration;
DELIMITER ??
CREATE PROCEDURE `add_migration`()
BEGIN
DECLARE v INT DEFAULT 1;
SET v = (SELECT COUNT(*) FROM `migrations` WHERE `id`='20261030222040');
IF v = 0 THEN
-- Add your query below.

-- Widen the three player-name columns to hold "First Last" (12 + 1 + 12).
-- The same definitions may be applied again after a partial failure.
ALTER TABLE `characters`
  MODIFY COLUMN `name` varchar(25) NOT NULL DEFAULT '',
  MODIFY COLUMN `deleted_name` varchar(25) DEFAULT NULL;

ALTER TABLE `gm_tickets`
  MODIFY COLUMN `name` varchar(25) NOT NULL COMMENT 'Name of ticket creator';

-- Record completion only after both ALTER statements. A failed DDL operation
-- must not leave this migration falsely registered as complete.
INSERT INTO `migrations` VALUES ('20261030222040');

-- End of migration.
END IF;
END??
DELIMITER ;
CALL add_migration();
DROP PROCEDURE IF EXISTS add_migration;
