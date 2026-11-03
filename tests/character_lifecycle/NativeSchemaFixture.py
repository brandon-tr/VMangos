import re
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from NativeTargetGuard import CONTAINER, SCHEMA_RE


class MySql:
    def __init__(self):
        self._schema = "fn05h_v20_test_" + uuid.uuid4().hex[:12]
        self._owned_schema = None
        self.created = False
        if not SCHEMA_RE.fullmatch(self._schema):
            raise ValueError("generated schema failed its own guard")

    @property
    def schema(self):
        return self._schema

    @schema.setter
    def schema(self, value):
        # The declared name may be changed for the substitution tests, but no
        # SQL path will ever use it unless it is the exact owned identity.
        self._schema = value

    @property
    def owned_schema(self):
        return self._owned_schema

    def _require_owned(self):
        if not self.created or self._owned_schema is None:
            raise RuntimeError("fixture schema not created by this run")
        if self._schema != self._owned_schema:
            raise ValueError("schema substitution rejected")
        if not SCHEMA_RE.fullmatch(self._owned_schema):
            raise ValueError("invalid owned schema identifier")
        return self._owned_schema

    def _run(self, sql, database=None):
        cmd = ["docker", "exec", "-i", CONTAINER, "sh", "-c",
               'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8 --batch --raw -N'
               + (f" -D {database}" if database else "")]
        result = subprocess.run(cmd, input=sql, text=True, encoding="utf-8",
                                capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        return result.stdout

    def create(self):
        if self.created:
            raise RuntimeError("fixture schema already created by this run")
        candidate = self._schema
        if not SCHEMA_RE.fullmatch(candidate):
            raise ValueError("refusing invalid schema identifier")
        # No IF NOT EXISTS: a collision must fail rather than adopt a schema.
        self._run(f"CREATE DATABASE `{candidate}` "
                  "CHARACTER SET utf8 COLLATE utf8_bin;")
        self._owned_schema = candidate
        self.created = True
        try:
            self._run("""
                CREATE TABLE characters(
                    guid INT UNSIGNED PRIMARY KEY,
                    account INT UNSIGNED NOT NULL,
                    name VARCHAR(25) NOT NULL,
                    deleted_name VARCHAR(25) NOT NULL DEFAULT '',
                    deleted_account INT UNSIGNED NOT NULL DEFAULT 0,
                    deleted_time BIGINT UNSIGNED NULL,
                    identity_generation BIGINT UNSIGNED NOT NULL,
                    money INT UNSIGNED NOT NULL DEFAULT 0,
                    character_flags INT UNSIGNED NOT NULL DEFAULT 0,
                    KEY idx_name(name)
                ) ENGINE=InnoDB;
                CREATE TABLE character_name_ownership(
                    canonical_name VARBINARY(75) PRIMARY KEY,
                    guid INT UNSIGNED NOT NULL UNIQUE,
                    CONSTRAINT fk_claim_character FOREIGN KEY(guid)
                        REFERENCES characters(guid) ON DELETE RESTRICT
                ) ENGINE=InnoDB;
                CREATE TABLE character_asset(
                    guid INT UNSIGNED NOT NULL,
                    label VARCHAR(32) NOT NULL,
                    CONSTRAINT fk_asset_character FOREIGN KEY(guid)
                        REFERENCES characters(guid) ON DELETE RESTRICT
                ) ENGINE=InnoDB;
                CREATE TABLE character_action(guid INT UNSIGNED, button INT UNSIGNED);
                CREATE TABLE mail(receiver_guid INT UNSIGNED, id INT UNSIGNED, has_items TINYINT UNSIGNED NOT NULL DEFAULT 0, cod INT UNSIGNED NOT NULL DEFAULT 0, money INT UNSIGNED NOT NULL DEFAULT 0, item_text_id INT UNSIGNED NOT NULL DEFAULT 0, KEY idx_receiver(receiver_guid)) ENGINE=InnoDB;
                CREATE TABLE mail_items(receiver_guid INT UNSIGNED, mail_id INT UNSIGNED, item_guid INT UNSIGNED, item_id INT UNSIGNED, PRIMARY KEY(mail_id,item_guid)) ENGINE=InnoDB;
                CREATE TABLE item_text(id INT UNSIGNED PRIMARY KEY, text TEXT) ENGINE=InnoDB;
                CREATE TABLE character_pet(owner_guid INT UNSIGNED, id INT UNSIGNED);
                CREATE TABLE pet_aura(guid INT UNSIGNED, spell INT UNSIGNED);
                CREATE TABLE pet_spell(guid INT UNSIGNED, spell INT UNSIGNED);
                CREATE TABLE pet_spell_cooldown(guid INT UNSIGNED, spell INT UNSIGNED);
                CREATE TABLE character_social(guid INT UNSIGNED, friend INT UNSIGNED);
                CREATE TABLE guild_eventlog(player_guid1 INT UNSIGNED, player_guid2 INT UNSIGNED);
                CREATE TABLE character_deleted_items(player_guid INT UNSIGNED, id INT UNSIGNED);
                CREATE TABLE character_aura(guid INT UNSIGNED, id INT UNSIGNED);
                CREATE TABLE character_battleground_data(guid INT UNSIGNED, id INT UNSIGNED);
                CREATE TABLE character_gifts(guid INT UNSIGNED, item_guid INT UNSIGNED);
                CREATE TABLE character_homebind(guid INT UNSIGNED, map INT UNSIGNED);
                CREATE TABLE character_instance(guid INT UNSIGNED, instance INT UNSIGNED);
                CREATE TABLE group_instance(leader_guid INT UNSIGNED, id INT UNSIGNED);
                CREATE TABLE character_inventory(guid INT UNSIGNED, item_guid INT UNSIGNED);
                CREATE TABLE character_queststatus(guid INT UNSIGNED, quest INT UNSIGNED);
                CREATE TABLE character_reputation(guid INT UNSIGNED, faction INT UNSIGNED);
                CREATE TABLE character_skills(guid INT UNSIGNED, skill INT UNSIGNED);
                CREATE TABLE character_forgotten_skills(guid INT UNSIGNED, skill INT UNSIGNED);
                CREATE TABLE character_spell(guid INT UNSIGNED, spell INT UNSIGNED);
                CREATE TABLE character_spell_cooldown(guid INT UNSIGNED, spell INT UNSIGNED);
                CREATE TABLE item_instance(owner_guid INT UNSIGNED, guid INT UNSIGNED) ENGINE=InnoDB;
            """, candidate)
        except Exception:
            # A partial fixture setup is still owned by this run: clean up only
            # the exact schema created above.
            self.drop()
            raise

    def drop(self):
        if not self.created or self._owned_schema is None:
            return
        target = self._require_owned()
        self._run(f"DROP DATABASE IF EXISTS `{target}`;")
        self.created = False
        self._owned_schema = None

    def sql(self, sql):
        target = self._require_owned()
        return self._run("SET SESSION sql_mode='STRICT_ALL_TABLES';\n" + sql,
                         target)

    def one(self, sql):
        target = self._require_owned()
        return self._run(sql, target).strip()

    def create_character(self, guid, account, name, generation=1):
        self.sql(f"""
            START TRANSACTION;
            INSERT INTO characters(guid,account,name,identity_generation)
                VALUES({guid},{account},'{name}',{generation});
            INSERT INTO character_name_ownership(canonical_name,guid)
                VALUES('{name}',{guid});
            COMMIT;
        """)
