#include "Database/CharacterPersistenceContext.h"

#include "Database/Database.h"
#include "Database/SqlCheckedBatchExecution.h"
#include "Database/SqlPreparedStatement.h"
#include "PlayerName.h"

#include <algorithm>
#include <limits>
#include <set>
#include <utility>

CharacterPersistenceContext::CharacterPersistenceContext(
    Database& database, uint32 serialId)
    : m_database(database),
      m_batch(database.CreateCheckedBatch(serialId)),
      m_invalid(false),
      m_submitted(false)
{
}

SqlCheckedBatch& CharacterPersistenceContext::Batch()
{
    return m_batch;
}

void CharacterPersistenceContext::Invalidate()
{
    m_invalid = true;
    m_acknowledgments.clear();
    m_stagedIdentities.clear();
}

bool CharacterPersistenceContext::HasIdentityGuard(
    CharacterIdentitySnapshot const& identity) const
{
    if (m_invalid || m_submitted)
        return false;
    for (CharacterIdentitySnapshot const& staged : m_stagedIdentities)
    {
        if (staged.guid == identity.guid &&
            staged.accountId == identity.accountId &&
            staged.canonicalName == identity.canonicalName &&
            staged.generation == identity.generation)
            return true;
    }
    return false;
}

void CharacterPersistenceContext::OnCommit(std::function<void()> action)
{
    if (!m_submitted && !m_invalid && action)
        m_acknowledgments.push_back(std::move(action));
}

bool CharacterPersistenceContext::AppendIdentityGuards(
    std::vector<CharacterIdentitySnapshot> const& identities)
{
    if (m_invalid || m_submitted || !m_batch.Empty() || identities.empty())
    {
        Invalidate();
        return false;
    }

    std::set<uint32> guids;
    for (CharacterIdentitySnapshot const& identity : identities)
    {
        if (identity.guid == 0 || identity.accountId == 0 ||
            identity.generation == 0 ||
            identity.generation == std::numeric_limits<uint64>::max() ||
            !isCanonicalFullPlayerName(identity.canonicalName) ||
            !guids.insert(identity.guid).second)
        {
            Invalidate();
            return false;
        }
    }

    std::vector<CharacterIdentitySnapshot> ordered = identities;
    std::sort(ordered.begin(), ordered.end(),
              [](CharacterIdentitySnapshot const& left,
                 CharacterIdentitySnapshot const& right) {
                  return left.guid < right.guid;
              });

    static SqlStatementID guardIdentity;
    for (CharacterIdentitySnapshot const& identity : ordered)
    {
        SqlStatement guard = m_database.CreateStatement(
            guardIdentity,
            "UPDATE `characters` SET "
            "`identity_generation`=`identity_generation`+1 "
            "WHERE `guid`=? AND `account`=? AND BINARY `name`=BINARY ? "
            "AND `identity_generation`=? AND `deleted_time` IS NULL "
            "AND `identity_generation`<18446744073709551615 "
            "AND EXISTS (SELECT 1 FROM `character_name_ownership` "
            "WHERE `guid`=? AND BINARY `canonical_name`=BINARY ?)");
        guard.addUInt32(identity.guid);
        guard.addUInt32(identity.accountId);
        guard.addString(identity.canonicalName);
        guard.addUInt64(identity.generation);
        guard.addUInt32(identity.guid);
        guard.addString(identity.canonicalName);
        if (!guard.AppendToCheckedBatch(
                m_batch, SqlAffectedRowsPolicy::ExactlyOne))
        {
            Invalidate();
            return false;
        }
    }

    m_stagedIdentities = ordered;
    return true;
}

SqlBatchResult CharacterPersistenceContext::Execute()
{
    if (m_invalid || m_submitted)
        return EmptySqlBatchResult();

    m_submitted = true;
    if (!m_batch.IsValid())
    {
        m_acknowledgments.clear();
        return EmptySqlBatchResult();
    }

    SqlBatchResult result = m_database.DirectExecuteCheckedBatch(
        std::move(m_batch));
    if (result.outcome != SqlWriteOutcome::Succeeded ||
        result.stage != SqlBatchStage::Complete)
    {
        m_acknowledgments.clear();
        return result;
    }

    std::vector<std::function<void()>> acknowledgments =
        std::move(m_acknowledgments);
    m_acknowledgments.clear();
    for (std::function<void()>& acknowledgment : acknowledgments)
        acknowledgment();
    return result;
}
