#ifndef CHARACTER_PERSISTENCE_CONTEXT_H
#define CHARACTER_PERSISTENCE_CONTEXT_H

#include "Common.h"
#include "Database/SqlCheckedBatch.h"
#include "Database/SqlWrite.h"

#include <functional>
#include <string>
#include <vector>

class Database;

struct CharacterIdentitySnapshot
{
    uint32 guid = 0;
    uint32 accountId = 0;
    std::string canonicalName;
    uint64 generation = 0;
};

/// Owns one checked batch and defers local publication until the batch has
/// completed successfully. It is single-use and never retries or acknowledges
/// on failure/uncertainty/destruction.
class CharacterPersistenceContext
{
    public:
        CharacterPersistenceContext(Database& database, uint32 serialId);

        SqlCheckedBatch& Batch();
        void Invalidate();
        void OnCommit(std::function<void()> action);
        bool AppendIdentityGuards(
            std::vector<CharacterIdentitySnapshot> const& identities);
        bool HasIdentityGuard(
            CharacterIdentitySnapshot const& identity) const;
        /// Identities staged by a successful AppendIdentityGuards call, in
        /// guard order.  Completion callbacks publish the increment to the
        /// surviving in-memory representations from these exact snapshots.
        std::vector<CharacterIdentitySnapshot> const& StagedIdentities() const
        {
            return m_stagedIdentities;
        }

        SqlBatchResult Execute();
        bool WasSubmitted() const { return m_submitted; }
        bool IsInvalid() const { return m_invalid; }

    private:
        Database& m_database;
        SqlCheckedBatch m_batch;
        std::vector<std::function<void()>> m_acknowledgments;
        std::vector<CharacterIdentitySnapshot> m_stagedIdentities;
        bool m_invalid;
        bool m_submitted;
};

#endif
