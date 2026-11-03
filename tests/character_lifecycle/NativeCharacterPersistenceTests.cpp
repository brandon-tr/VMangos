#include "Database/CharacterIdentityPersistence.h"
#include "Database/CharacterPersistenceContext.h"
#include "Database/SqlCheckedBatchExecution.h"
#include "Database/SqlPreparedWrite.h"
#include "Database/SqlWrite.h"
#include "Database/Database.h"
#include "NativeMailSqlGenerated.h"

#include <mysql.h>

#include <cstdlib>
#include <cstring>
#include <iostream>
#include <memory>
#include <string>
#include <vector>

namespace
{
int failures = 0;

void Check(bool value, std::string const& label)
{
    if (!value)
    {
        std::cerr << "FAIL " << label << "\n";
        ++failures;
    }
}

class NativeConnection
{
    public:
        explicit NativeConnection(MYSQL*& mysql) : m_mysql(mysql) {}

        SqlWriteResult ExecuteChecked(std::string const& sql)
        {
            if (!m_mysql)
            {
                SqlWriteResult result = {SqlWriteOutcome::OutcomeUnknown, 0, 0};
                return result;
            }
            return ExecuteMySQLWrite(m_mysql, sql);
        }

        SqlWriteResult ExecutePreparedChecked(
            std::string const& sql,
            std::vector<SqlStmtFieldData> const& parameters)
        {
            if (!m_mysql)
            {
                SqlWriteResult result = {SqlWriteOutcome::OutcomeUnknown, 0, 0};
                return result;
            }
            return ExecuteMySQLPreparedWrite(m_mysql, sql, parameters);
        }

        void DiscardConnection()
        {
            if (m_mysql)
            {
                mysql_close(m_mysql);
                m_mysql = nullptr;
            }
        }

    private:
        MYSQL*& m_mysql;
};

class NativeDatabase : public Database
{
    public:
        explicit NativeDatabase(MYSQL* mysql) : m_mysql(mysql) {}
        NativeDatabase(NativeDatabase const&) = delete;
        NativeDatabase& operator=(NativeDatabase const&) = delete;
        ~NativeDatabase() override
        {
            if (m_mysql)
                mysql_close(m_mysql);
        }

        SqlBatchResult DirectExecuteCheckedBatch(SqlCheckedBatch batch) override
        {
            NativeConnection connection(m_mysql);
            SqlBatchResult result = ExecuteSqlCheckedBatch(connection, batch.Writes());
            // The connection is intentionally not discarded on a successful rollback: the
            // runner owns this single session and later fixture assertions use it.
            return result;
        }

        MYSQL* Raw() const { return m_mysql; }

    private:
        MYSQL* m_mysql;
};

struct TargetConfig
{
    std::string host;
    std::string port;
    std::string user;
    std::string password;
    std::string database;
};

bool IsTestSchema(std::string const& schema)
{
    static char const prefix[] = "fn05h_v20_test_";
    std::size_t const prefixLength = sizeof(prefix) - 1;
    if (schema.size() != prefixLength + 12)
        return false;
    if (schema.compare(0, prefixLength, prefix) != 0)
        return false;
    for (std::size_t i = prefixLength; i < schema.size(); ++i)
    {
        char const c = schema[i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')))
            return false;
    }
    return true;
}

bool ValidateTarget(TargetConfig const& target, std::string& error)
{
    if (target.host != "127.0.0.1")
    {
        error = "host must be 127.0.0.1";
        return false;
    }
    if (target.port != "3306")
    {
        error = "port must be 3306";
        return false;
    }
    if (!IsTestSchema(target.database))
    {
        error = "database must match fn05h_v20_test_[0-9a-f]{12}";
        return false;
    }
    if (target.user.empty())
    {
        error = "user is required";
        return false;
    }
    if (target.password.empty())
    {
        error = "password is required";
        return false;
    }
    return true;
}

bool LoadTarget(TargetConfig& target, std::string& error)
{
    char const* host = std::getenv("FN05H_MYSQL_HOST");
    char const* portText = std::getenv("FN05H_MYSQL_PORT");
    char const* user = std::getenv("FN05H_MYSQL_USER");
    char const* password = std::getenv("FN05H_MYSQL_PASSWORD");
    char const* database = std::getenv("FN05H_MYSQL_DATABASE");
    if (!host || !portText || !user || !password || !database)
    {
        error = "FN05H_MYSQL_* target variables are incomplete";
        return false;
    }
    target = TargetConfig{host, portText, user, password, database};
    return ValidateTarget(target, error);
}

int GuardSelfTest()
{
    TargetConfig accepted{"127.0.0.1", "3306", "root", "secret",
                          "fn05h_v20_test_0123456789ab"};
    std::string error;
    Check(ValidateTarget(accepted, error), "guard self-test accepted target");

    TargetConfig rejectedHost = accepted;
    rejectedHost.host = "localhost";
    Check(!ValidateTarget(rejectedHost, error), "guard rejects localhost");
    TargetConfig rejectedPort = accepted;
    rejectedPort.port = "3307";
    Check(!ValidateTarget(rejectedPort, error), "guard rejects port");
    TargetConfig rejectedSchema = accepted;
    rejectedSchema.database = "characters";
    Check(!ValidateTarget(rejectedSchema, error), "guard rejects app schema");
    rejectedSchema.database = "fn05h_v20_test_0123456789ag";
    Check(!ValidateTarget(rejectedSchema, error), "guard rejects non-hex schema");
    rejectedSchema.database = "fn05h_v20_test_0123456789a";
    Check(!ValidateTarget(rejectedSchema, error), "guard rejects short schema");
    TargetConfig missingUser = accepted;
    missingUser.user.clear();
    Check(!ValidateTarget(missingUser, error), "guard rejects missing user");
    TargetConfig missingPassword = accepted;
    missingPassword.password.clear();
    Check(!ValidateTarget(missingPassword, error), "guard rejects missing password");

    if (failures)
    {
        std::cerr << "native target guard: FAIL\n";
        return 1;
    }
    std::cout << "native target guard: PASS\n";
    return 0;
}

MYSQL* Connect(TargetConfig const& target)
{
    MYSQL* mysql = mysql_init(nullptr);
    if (!mysql)
        return nullptr;
    if (mysql_options(mysql, MYSQL_SET_CHARSET_NAME, "utf8") != 0)
    {
        mysql_close(mysql);
        return nullptr;
    }
    unsigned int port = static_cast<unsigned int>(
        std::strtoul(target.port.c_str(), nullptr, 10));
    if (!mysql_real_connect(mysql, target.host.c_str(), target.user.c_str(),
                            target.password.c_str(), target.database.c_str(), port,
                            nullptr, 0))
    {
        std::cerr << "NATIVE_MYSQL_UNAVAILABLE: " << mysql_error(mysql) << "\n";
        mysql_close(mysql);
        return nullptr;
    }
    return mysql;
}

bool Raw(MYSQL* mysql, std::string const& sql)
{
    if (mysql_query(mysql, sql.c_str()) != 0)
    {
        std::cerr << "fixture SQL failed: " << mysql_error(mysql) << "\n";
        return false;
    }
    MYSQL_RES* result = mysql_store_result(mysql);
    if (result)
        mysql_free_result(result);
    return true;
}

std::string Scalar(MYSQL* mysql, std::string const& sql)
{
    if (mysql_query(mysql, sql.c_str()) != 0)
        return {};
    MYSQL_RES* result = mysql_store_result(mysql);
    if (!result)
        return {};
    MYSQL_ROW row = mysql_fetch_row(result);
    std::string value = row && row[0] ? row[0] : "";
    mysql_free_result(result);
    return value;
}

std::string Hex(std::string const& value)
{
    static char const digits[] = "0123456789ABCDEF";
    std::string out;
    for (unsigned char c : value)
    {
        out.push_back(digits[c >> 4]);
        out.push_back(digits[c & 0xF]);
    }
    return out;
}

void Reset(MYSQL* mysql)
{
    static char const* tables[] = {
        "character_asset", "character_action", "mail", "mail_items",
        "character_pet", "pet_aura", "pet_spell", "pet_spell_cooldown",
        "item_text",
        "character_social", "guild_eventlog", "character_deleted_items",
        "character_aura", "character_battleground_data", "character_gifts",
        "character_homebind", "character_instance", "group_instance",
        "character_inventory", "character_queststatus", "character_reputation",
        "character_skills", "character_forgotten_skills", "character_spell",
        "character_spell_cooldown", "item_instance",
        "character_name_ownership", "characters"
    };
    Raw(mysql, "SET FOREIGN_KEY_CHECKS=0");
    for (char const* table : tables)
        Raw(mysql, std::string("DELETE FROM `") + table + "`");
    Raw(mysql, "SET FOREIGN_KEY_CHECKS=1");
}

bool Seed(MYSQL* mysql, std::string const& name, uint64 generation = 3,
          uint32 guid = 1, uint32 account = 7)
{
    return Raw(mysql, "INSERT INTO characters(guid,account,name,identity_generation) "
                      "VALUES(" + std::to_string(guid) + "," +
                      std::to_string(account) + ",'" + name + "'," +
                      std::to_string(generation) + ")") &&
           Raw(mysql, "INSERT INTO character_name_ownership(canonical_name,guid) "
                      "VALUES('" + name + "','" + std::to_string(guid) + "')");
}

CharacterDeletionSnapshot Snapshot(std::string const& name, uint64 generation = 3,
                                   uint32 guid = 1, uint32 account = 7,
                                   uint64 deletedTime = 0)
{
    CharacterDeletionSnapshot snapshot;
    snapshot.identity.guid = guid;
    snapshot.identity.accountId = account;
    snapshot.identity.canonicalName = name;
    snapshot.identity.generation = generation;
    snapshot.deletedTime = deletedTime;
    return snapshot;
}

SqlBatchResult Delete(NativeDatabase& database, CharacterDeletionSnapshot snapshot,
                      uint32 serial, bool& acknowledged)
{
    CharacterPersistenceContext context(database, serial);
    if (!AppendPermanentCharacterDeletion(database, context, snapshot))
    {
        SqlBatchResult result = EmptySqlBatchResult();
        result.outcome = SqlWriteOutcome::Failed;
        return result;
    }
    context.OnCommit([&]() { acknowledged = true; });
    return context.Execute();
}

void TestActiveSuccess(NativeDatabase& database, MYSQL* mysql)
{
    Reset(mysql);
    Check(Seed(mysql, "Alice Smith"), "active seed");
    Raw(mysql, "INSERT INTO character_action VALUES(1,2)");
    bool acknowledged = false;
    SqlBatchResult result = Delete(database, Snapshot("Alice Smith"), 1, acknowledged);
    Check(result.outcome == SqlWriteOutcome::Succeeded &&
          result.stage == SqlBatchStage::Complete, "active native commit");
    Check(acknowledged, "active native acknowledgment");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM characters") == "0", "active row deleted");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM character_name_ownership") == "0",
          "active claim deleted");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM character_action") == "0",
          "active related deleted");
}

void TestTombstoneSuccess(NativeDatabase& database, MYSQL* mysql)
{
    Reset(mysql);
    Seed(mysql, "Bob Jones");
    Raw(mysql, "DELETE FROM character_name_ownership");
    Raw(mysql, "UPDATE characters SET deleted_name=name,deleted_account=account,"
               "deleted_time=1700000000,name='',account=0,identity_generation=4");
    bool acknowledged = false;
    SqlBatchResult result = Delete(
        database, Snapshot("Bob Jones", 4, 1, 7, 1700000000), 2, acknowledged);
    Check(result.outcome == SqlWriteOutcome::Succeeded &&
          result.stage == SqlBatchStage::Complete, "tombstone native commit");
    Check(acknowledged, "tombstone native acknowledgment");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM characters") == "0",
          "tombstone row deleted");
}

void TestPredicateFailures(NativeDatabase& database, MYSQL* mysql)
{
    Raw(mysql, "DELETE FROM character_action");
    struct Case
    {
        char const* label;
        CharacterDeletionSnapshot snapshot;
    };
    Case cases[] = {
        {"wrong generation", Snapshot("Alice Smith", 2)},
        {"wrong account", Snapshot("Alice Smith", 3, 1, 8)},
        {"wrong name", Snapshot("Alice Jones", 3)},
    };
    for (Case const& item : cases)
    {
        Reset(mysql);
        Seed(mysql, "Alice Smith");
        Raw(mysql, "INSERT INTO character_action VALUES(1,2)");
        bool acknowledged = false;
        SqlBatchResult result = Delete(database, item.snapshot, 3, acknowledged);
        Check(result.outcome == SqlWriteOutcome::Failed &&
              result.stage == SqlBatchStage::RowCount,
              std::string(item.label) + " guard row count");
        Check(!acknowledged, std::string(item.label) + " no acknowledgment");
        Check(Scalar(mysql, "SELECT COUNT(*) FROM characters") == "1",
              std::string(item.label) + " row retained");
        Check(Scalar(mysql, "SELECT identity_generation FROM characters") == "3",
              std::string(item.label) + " generation rolled back");
        Check(Scalar(mysql, "SELECT COUNT(*) FROM character_action") == "1",
              std::string(item.label) + " no later cleanup");
    }

    Reset(mysql);
    Seed(mysql, "Alice Smith");
    Raw(mysql, "DELETE FROM character_name_ownership");
    bool acknowledged = false;
    SqlBatchResult result = Delete(database, Snapshot("Alice Smith"), 4, acknowledged);
    Check(result.outcome == SqlWriteOutcome::Failed && !acknowledged,
          "missing claim rejected");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM characters") == "1",
          "missing claim retains character");
}

void TestCod(NativeDatabase& database, MYSQL* mysql)
{
    Reset(mysql);
    Seed(mysql, "Alice Smith");
    Raw(mysql, "INSERT INTO mail(receiver_guid,id,has_items,cod) VALUES(1,9,1,100)");
    bool acknowledged = false;
    SqlBatchResult result = Delete(database, Snapshot("Alice Smith"), 5, acknowledged);
    Check(result.outcome == SqlWriteOutcome::Failed,
          "COD guard rejects");
    Check(!acknowledged, "COD no acknowledgment");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM characters") == "1",
          "COD retains character");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM mail") == "1",
          "COD retains mail");
}

void TestFinalForeignKeyFailure(NativeDatabase& database, MYSQL* mysql)
{
    Reset(mysql);
    Seed(mysql, "Alice Smith");
    Raw(mysql, "INSERT INTO character_action VALUES(1,2)");
    Raw(mysql, "INSERT INTO character_asset VALUES(1,'retained')");
    bool acknowledged = false;
    SqlBatchResult result = Delete(database, Snapshot("Alice Smith"), 6, acknowledged);
    Check(result.outcome == SqlWriteOutcome::Failed &&
          result.stage == SqlBatchStage::Statement,
          "final FK failure");
    Check(!acknowledged, "final FK no acknowledgment");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM characters") == "1",
          "final FK retains character");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM character_name_ownership") == "1",
          "final FK restores claim");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM character_action") == "1",
          "final FK restores related");
}

bool AddMailOwnership(NativeDatabase& database,
                      CharacterPersistenceContext& context,
                      uint32 itemGuid, uint32 fromOwner, uint32 toOwner)
{
    static SqlStatementID ownerId;
    SqlStatement statement = database.CreateStatement(
        ownerId, NativeMailSql::ItemOwnershipUpdate);
    statement.addUInt32(toOwner);
    statement.addUInt32(itemGuid);
    statement.addUInt32(fromOwner);
    return statement.AppendToCheckedBatch(
        context.Batch(), SqlAffectedRowsPolicy::ExactlyOne);
}

bool AddMailLink(NativeDatabase& database,
                 CharacterPersistenceContext& context,
                 uint32 mailId, uint32 itemGuid, uint32 itemId, uint32 receiver)
{
    static SqlStatementID linkId;
    SqlStatement statement = database.CreateStatement(
        linkId, NativeMailSql::MailItemLinkInsert);
    statement.addUInt32(mailId);
    statement.addUInt32(itemGuid);
    statement.addUInt32(itemId);
    statement.addUInt32(receiver);
    return statement.AppendToCheckedBatch(
        context.Batch(), SqlAffectedRowsPolicy::ExactlyOne);
}

bool AddMailBody(NativeDatabase& database,
                 CharacterPersistenceContext& context,
                 uint32 bodyId, std::string const& body)
{
    static SqlStatementID textId;
    SqlStatement statement = database.CreateStatement(
        textId, NativeMailSql::ItemTextInsert);
    statement.addUInt32(bodyId);
    statement.addString(body);
    return statement.AppendToCheckedBatch(
        context.Batch(), SqlAffectedRowsPolicy::ExactlyOne);
}

bool DeleteMailLinks(NativeDatabase& database,
                     CharacterPersistenceContext& context, uint32 mailId)
{
    static SqlStatementID deleteLinksId;
    SqlStatement statement = database.CreateStatement(
        deleteLinksId, NativeMailSql::MailItemLinkDelete);
    statement.addUInt32(mailId);
    return statement.AppendToCheckedBatch(
        context.Batch(), SqlAffectedRowsPolicy::Any);
}

bool DeleteMailBody(NativeDatabase& database,
                    CharacterPersistenceContext& context, uint32 bodyId)
{
    static SqlStatementID deleteTextId;
    SqlStatement statement = database.CreateStatement(
        deleteTextId, NativeMailSql::ItemTextDelete);
    statement.addUInt32(bodyId);
    return statement.AppendToCheckedBatch(
        context.Batch(), SqlAffectedRowsPolicy::Any);
}

bool DeleteMailItem(NativeDatabase& database,
                    CharacterPersistenceContext& context,
                    uint32 itemGuid, uint32 owner)
{
    static SqlStatementID deleteItemId;
    SqlStatement statement = database.CreateStatement(
        deleteItemId, NativeMailSql::ItemDisposalDelete);
    statement.addUInt32(itemGuid);
    statement.addUInt32(owner);
    return statement.AppendToCheckedBatch(
        context.Batch(), SqlAffectedRowsPolicy::Any);
}

void TestMailAttachmentAndBodySql(NativeDatabase& database, MYSQL* mysql)
{
    // Two items start under owner 2 and are transferred to receiver 1.  The
    // body ID is absent before the checked unit, so its INSERT must succeed.
    Reset(mysql);
    Check(Raw(mysql, "INSERT INTO item_instance(owner_guid,guid) VALUES(2,101)"),
          "seed attachment 101");
    Check(Raw(mysql, "INSERT INTO item_instance(owner_guid,guid) VALUES(2,102)"),
          "seed attachment 102");
    Check(Raw(mysql, "INSERT INTO mail(receiver_guid,id,has_items,item_text_id) "
                     "VALUES(1,9,1,77)"),
          "seed mail 9");

    CharacterPersistenceContext insert(database, 20);
    Check(AddMailBody(database, insert, 77, "body-text"), "body assembles");
    Check(AddMailOwnership(database, insert, 101, 2, 1),
          "first ownership transfer assembles");
    Check(AddMailOwnership(database, insert, 102, 2, 1),
          "second ownership transfer assembles");
    Check(AddMailLink(database, insert, 9, 101, 501, 1),
          "first link assembles");
    Check(AddMailLink(database, insert, 9, 102, 502, 1),
          "second link assembles");

    bool acknowledged = false;
    insert.OnCommit([&]() { acknowledged = true; });
    SqlBatchResult result = insert.Execute();
    Check(result.outcome == SqlWriteOutcome::Succeeded &&
          result.stage == SqlBatchStage::Complete,
          "multi-attachment/body commit");
    Check(acknowledged, "multi-attachment/body acknowledgment");
    Check(result.statementResults.size() == 5,
          "success unit has five statements");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM item_instance "
                        "WHERE guid IN(101,102) AND owner_guid=1") == "2",
          "both items transferred to receiver");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM mail_items "
                        "WHERE mail_id=9 AND receiver_guid=1") == "2",
          "two attachment links persisted for receiver");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM mail_items WHERE mail_id=9") == "2",
          "exactly two attachment links");
    Check(Scalar(mysql, "SELECT text FROM item_text WHERE id=77") == "body-text",
          "body text persisted");

    // Bulk cleanup follows the production Any policies; the counts prove both
    // links are removed rather than a single-row assertion on an Exact policy.
    CharacterPersistenceContext cleanup(database, 21);
    Check(DeleteMailLinks(database, cleanup, 9), "bulk link cleanup assembles");
    Check(DeleteMailBody(database, cleanup, 77), "body cleanup assembles");
    Check(DeleteMailItem(database, cleanup, 101, 1), "item 101 cleanup assembles");
    Check(DeleteMailItem(database, cleanup, 102, 1), "item 102 cleanup assembles");
    bool cleanupAcknowledged = false;
    cleanup.OnCommit([&]() { cleanupAcknowledged = true; });
    result = cleanup.Execute();
    Check(result.outcome == SqlWriteOutcome::Succeeded &&
          result.stage == SqlBatchStage::Complete && cleanupAcknowledged,
          "bulk attachment/body cleanup commit");
    Check(result.statementResults.size() == 4 &&
          result.statementResults[0].affectedRows == 2 &&
          result.statementResults[1].affectedRows == 1 &&
          result.statementResults[2].affectedRows == 1 &&
          result.statementResults[3].affectedRows == 1,
          "bulk cleanup row counts");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM mail_items WHERE mail_id=9") == "0",
          "attachment links cleaned");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM item_text WHERE id=77") == "0",
          "body text cleaned");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM item_instance "
                        "WHERE guid IN(101,102)") == "0",
          "attachment item rows cleaned");

    // A current-owner setup with a deliberately wrong old-owner predicate must
    // fail at the ownership statement after body/prior-ownership writes.
    Reset(mysql);
    Check(Raw(mysql, "INSERT INTO item_instance(owner_guid,guid) VALUES(2,101)"),
          "stale-owner seed 101");
    Check(Raw(mysql, "INSERT INTO item_instance(owner_guid,guid) VALUES(2,102)"),
          "stale-owner seed 102");
    Check(Raw(mysql, "INSERT INTO mail(receiver_guid,id,has_items,item_text_id) "
                     "VALUES(1,9,1,77)"),
          "stale-owner mail");
    CharacterPersistenceContext staleOwner(database, 22);
    Check(AddMailBody(database, staleOwner, 77, "stale-owner-body"),
          "stale-owner body assembles");
    Check(AddMailOwnership(database, staleOwner, 102, 2, 1),
          "stale-owner prior ownership assembles");
    Check(AddMailOwnership(database, staleOwner, 101, 3, 1),
          "stale-owner wrong predicate assembles");
    Check(AddMailLink(database, staleOwner, 9, 101, 501, 1),
          "stale-owner link assembles");
    bool staleOwnerAcknowledged = false;
    staleOwner.OnCommit([&]() { staleOwnerAcknowledged = true; });
    result = staleOwner.Execute();
    Check(result.outcome == SqlWriteOutcome::Failed &&
          result.stage == SqlBatchStage::RowCount &&
          result.statementIndex == 2 && !staleOwnerAcknowledged,
          "stale owner rejected at ownership statement");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM item_instance "
                        "WHERE guid IN(101,102) AND owner_guid=2") == "2",
          "stale owner rollback retains original owners");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM mail_items WHERE mail_id=9") == "0" &&
          Scalar(mysql, "SELECT COUNT(*) FROM item_text WHERE id=77") == "0",
          "stale owner rollback removes earlier body/link writes");

    // A duplicate link fails after body, ownership and the first link; the
    // earlier ownership write must roll back too.
    Reset(mysql);
    Check(Raw(mysql, "INSERT INTO item_instance(owner_guid,guid) VALUES(2,101)"),
          "late-failure seed 101");
    Check(Raw(mysql, "INSERT INTO mail(receiver_guid,id,has_items,item_text_id) "
                     "VALUES(1,10,1,88)"),
          "late-failure mail");
    CharacterPersistenceContext failing(database, 23);
    Check(AddMailBody(database, failing, 88, "rolled-back-body"),
          "late-failure body assembles");
    Check(AddMailOwnership(database, failing, 101, 2, 1),
          "late-failure ownership assembles");
    Check(AddMailLink(database, failing, 10, 101, 503, 1),
          "late-failure first link assembles");
    Check(AddMailLink(database, failing, 10, 101, 503, 1),
          "late-failure duplicate link assembles");
    bool failedAcknowledged = false;
    failing.OnCommit([&]() { failedAcknowledged = true; });
    result = failing.Execute();
    // MySQL reports ER_DUP_ENTRY (1062) as DuplicateKey; it is a known
    // statement rejection, not an uncertain connection outcome.
    Check(result.outcome == SqlWriteOutcome::DuplicateKey &&
          result.stage == SqlBatchStage::Statement &&
          result.statementIndex == 3 && result.cause.nativeError == 1062 &&
          result.statementResults.size() == 4 && !failedAcknowledged,
          "duplicate link fails with DuplicateKey/1062 after prior writes");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM item_instance "
                        "WHERE guid=101 AND owner_guid=2") == "1",
          "late failure rolls ownership back");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM mail_items WHERE mail_id=10") == "0" &&
          Scalar(mysql, "SELECT COUNT(*) FROM item_text WHERE id=88") == "0",
          "late failure rolls links and body back");

    // Same-owner is faithfully represented by staging no ownership update:
    // the production helper returns early for an identical owner.
    Reset(mysql);
    Check(Raw(mysql, "INSERT INTO item_instance(owner_guid,guid) VALUES(1,101)"),
          "same-owner seed 101");
    Check(Raw(mysql, "INSERT INTO item_instance(owner_guid,guid) VALUES(1,102)"),
          "same-owner seed 102");
    Check(Raw(mysql, "INSERT INTO mail(receiver_guid,id,has_items,item_text_id) "
                     "VALUES(1,11,1,77)"),
          "same-owner mail");
    CharacterPersistenceContext sameOwner(database, 24);
    Check(AddMailBody(database, sameOwner, 77, "same-owner-body"),
          "same-owner body assembles");
    Check(AddMailLink(database, sameOwner, 11, 101, 501, 1),
          "same-owner first link assembles");
    Check(AddMailLink(database, sameOwner, 11, 102, 502, 1),
          "same-owner second link assembles");
    bool sameOwnerAcknowledged = false;
    sameOwner.OnCommit([&]() { sameOwnerAcknowledged = true; });
    result = sameOwner.Execute();
    Check(result.outcome == SqlWriteOutcome::Succeeded &&
          result.statementResults.size() == 3 && sameOwnerAcknowledged,
          "same-owner unit has no ownership statement and commits");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM item_instance "
                        "WHERE guid IN(101,102) AND owner_guid=1") == "2",
          "same-owner items remain with the receiver");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM mail_items WHERE mail_id=11") == "2",
          "same-owner links persisted");
}

void TestUnchangedBalanceAndStaleClaims(NativeDatabase& database, MYSQL* mysql)
{
    Reset(mysql);
    Check(Seed(mysql, "Alice Smith"), "money seed");
    Raw(mysql, "UPDATE characters SET money=1000");
    Raw(mysql, "INSERT INTO mail(receiver_guid,id,money) VALUES(1,9,100)");

    CharacterIdentitySnapshot identity;
    identity.guid = 1;
    identity.accountId = 7;
    identity.canonicalName = "Alice Smith";
    identity.generation = 3;

    // The projected balance equals the stored balance, so MySQL reports zero
    // changed rows for the Any-policy balance update.
    MailMoneyWithdrawal unchanged{identity, 9, 100, 900, 1000};
    CharacterPersistenceContext context(database, 10);
    uint32 projected = 0;
    Check(AppendMailMoneyWithdrawal(database, context, unchanged, projected),
          "unchanged balance assembles");
    bool acknowledged = false;
    context.OnCommit([&]() { acknowledged = true; });
    SqlBatchResult result = context.Execute();
    Check(result.outcome == SqlWriteOutcome::Succeeded &&
          result.stage == SqlBatchStage::Complete,
          "unchanged balance native commit");
    Check(acknowledged, "unchanged balance acknowledgment");
    Check(result.statementResults.size() == 3 &&
          result.statementResults.back().affectedRows == 0,
          "unchanged balance reports zero changed rows");
    Check(Scalar(mysql, "SELECT money FROM mail WHERE id=9") == "0",
          "mail money cleared");
    Check(Scalar(mysql, "SELECT money FROM characters WHERE guid=1") == "1000",
          "unchanged balance retained");
    Check(Scalar(mysql, "SELECT identity_generation FROM characters WHERE guid=1") == "4",
          "first withdrawal advances generation");

    // The generation is now 4; the mail amount is deliberately stale for a
    // current identity, so the mail claim—not the identity guard—must fail.
    Raw(mysql, "UPDATE mail SET money=100 WHERE id=9");
    MailMoneyWithdrawal staleAmount{identity, 9, 50, 950, 1000};
    staleAmount.identity.generation = 4;
    CharacterPersistenceContext staleContext(database, 11);
    bool staleAcknowledged = false;
    Check(AppendMailMoneyWithdrawal(database, staleContext, staleAmount, projected),
          "stale amount assembles");
    staleContext.OnCommit([&]() { staleAcknowledged = true; });
    result = staleContext.Execute();
    Check(result.outcome == SqlWriteOutcome::Failed &&
          result.stage == SqlBatchStage::RowCount &&
          result.statementIndex == 1 && !staleAcknowledged,
          "stale amount rejected at the mail claim");
    Check(Scalar(mysql, "SELECT money FROM characters WHERE guid=1") == "1000" &&
          Scalar(mysql, "SELECT money FROM mail WHERE id=9") == "100" &&
          Scalar(mysql, "SELECT identity_generation FROM characters WHERE guid=1") == "4",
          "stale amount rollback retains balance, mail and generation");

    // A stale generation must fail the identity guard before the mail claim.
    MailMoneyWithdrawal staleIdentity = unchanged;
    staleIdentity.identity.generation = 3;
    CharacterPersistenceContext identityContext(database, 12);
    bool identityAcknowledged = false;
    Check(AppendMailMoneyWithdrawal(database, identityContext, staleIdentity, projected),
          "stale identity assembles");
    identityContext.OnCommit([&]() { identityAcknowledged = true; });
    result = identityContext.Execute();
    Check(result.outcome == SqlWriteOutcome::Failed &&
          result.stage == SqlBatchStage::RowCount &&
          result.statementIndex == 0 && !identityAcknowledged,
          "stale generation rejected at the identity guard");
    Check(Scalar(mysql, "SELECT identity_generation FROM characters WHERE guid=1") == "4",
          "stale identity leaves generation");
}

void TestNonAscii(NativeDatabase& database, MYSQL* mysql)
{
    std::string name = "Éowynéowynéo Jacobsmith";
    Reset(mysql);
    Check(Seed(mysql, name), "non-ASCII seed");
    bool acknowledged = false;
    SqlBatchResult result = Delete(database, Snapshot(name), 7, acknowledged);
    Check(result.outcome == SqlWriteOutcome::Succeeded && acknowledged,
          "non-ASCII native delete");
    Check(Scalar(mysql, "SELECT COUNT(*) FROM characters") == "0",
          "non-ASCII row deleted");
}
}

struct ReadConnection
{
    explicit ReadConnection(TargetConfig const& target) : mysql(Connect(target)) {}
    MYSQL* mysql = nullptr;
    ~ReadConnection() { if (mysql) mysql_close(mysql); }
    ReadConnection(ReadConnection const&) = delete;
    ReadConnection& operator=(ReadConnection const&) = delete;
};

int main(int argc, char** argv)
{
    if (argc > 1 && std::strcmp(argv[1], "--guard-self-test") == 0)
        return GuardSelfTest();

    TargetConfig target;
    std::string targetError;
    if (!LoadTarget(target, targetError))
    {
        std::cerr << "NATIVE_TARGET_REJECTED: " << targetError << "\n";
        return 3;
    }

    mysql_library_init(0, nullptr, nullptr);
    int code = 0;
    {
        ReadConnection read(target);
        if (!read.mysql)
            code = 2;
        else
        {
            MYSQL* write = Connect(target);
            if (!write)
                code = 2;
            else
            {
                NativeDatabase database(write);
                TestActiveSuccess(database, read.mysql);
                TestTombstoneSuccess(database, read.mysql);
                TestPredicateFailures(database, read.mysql);
                TestCod(database, read.mysql);
                TestFinalForeignKeyFailure(database, read.mysql);
                TestMailAttachmentAndBodySql(database, read.mysql);
                TestUnchangedBalanceAndStaleClaims(database, read.mysql);
                TestNonAscii(database, read.mysql);
                if (failures)
                {
                    std::cerr << "native C++ failures=" << failures << "\n";
                    code = 1;
                }
            }
        }
    }
    mysql_library_end();
    if (code == 0)
        std::cout << "native checked/prepared pipeline: PASS\n";
    return code;
}