#ifndef CHARACTER_PERSISTENCE_RECOVERY_H
#define CHARACTER_PERSISTENCE_RECOVERY_H

#include "Common.h"

#include <memory>
#include <string>
#include <vector>

enum class CharacterRecoveryKind
{
    WholeSave,
    Rename,
    Restoration,
    SoftDeletion,
    PermanentDeletion,
    AccountDeletion
};

struct CharacterRecoveryRecord
{
    CharacterRecoveryKind kind = CharacterRecoveryKind::WholeSave;
    std::vector<uint32> guids;
    std::vector<uint32> accountIds;
    uint64 expectedGeneration = 0;
    std::string detail;
};

enum class AccountLifecycleKind
{
    Mutation,
    Deletion
};

class AccountLifecycleLease
{
    public:
        ~AccountLifecycleLease();

        AccountLifecycleLease(AccountLifecycleLease const&) = delete;
        AccountLifecycleLease& operator=(AccountLifecycleLease const&) = delete;

        bool Covers(uint32 accountId) const;
        bool IsActive() const;
        AccountLifecycleKind GetKind() const { return m_kind; }
        void const* RegistryIdentity() const;
        uint64 LeaseId() const;

    private:
        friend class AccountLifecycleRegistry;
        struct State;
        explicit AccountLifecycleLease(std::shared_ptr<State> state);
        std::shared_ptr<State> m_state;
        AccountLifecycleKind m_kind;
};

using AccountLifecycleLeasePtr = std::shared_ptr<AccountLifecycleLease>;

/// Admission for one composed persistence operation.  A caller-owned lease is
/// validated but never released here; any remaining affected accounts are
/// acquired as one additional real-registry lease and held until destruction.
class CharacterPersistenceAdmission
{
    public:
        CharacterPersistenceAdmission() = default;
        CharacterPersistenceAdmission(CharacterPersistenceAdmission const&) = delete;
        CharacterPersistenceAdmission& operator=(CharacterPersistenceAdmission const&) = delete;

        bool Admit(std::vector<uint32> const& accountIds,
                   AccountLifecycleKind kind,
                   AccountLifecycleLease const* borrowed = nullptr);
        bool IsAdmitted() const { return m_admitted; }
        std::vector<uint32> const& Accounts() const { return m_accounts; }

    private:
        AccountLifecycleLeasePtr m_owned;
        std::vector<uint32> m_accounts;
        bool m_admitted = false;
};

/// Process-local exclusive admission and unresolved-outcome registry. Recovery
/// blocks and admission decisions share one mutex; no external work is done
/// under it.
class AccountLifecycleRegistry
{
    public:
        AccountLifecycleRegistry();
        ~AccountLifecycleRegistry();

        AccountLifecycleRegistry(AccountLifecycleRegistry const&) = delete;
        AccountLifecycleRegistry& operator=(AccountLifecycleRegistry const&) = delete;

        AccountLifecycleLeasePtr TryAcquire(
            std::vector<uint32> const& accounts, AccountLifecycleKind kind);
        bool Allows(AccountLifecycleLease const& lease, uint32 accountId,
                    AccountLifecycleKind kind) const;
        bool MarkDeleted(AccountLifecycleLease const& lease, uint32 accountId);

        bool BlockRecovery(CharacterRecoveryRecord const& record);
        bool IsCharacterBlocked(uint32 guid) const;
        bool IsAccountBlocked(uint32 accountId) const;
        bool ClearCharacterBlock(uint32 guid);
        std::vector<CharacterRecoveryRecord> Records() const;

    private:
        struct Impl;
        std::shared_ptr<Impl> m_impl;
        friend class AccountLifecycleLease;
};

AccountLifecycleRegistry& GetAccountLifecycleRegistry();

/// Records an unresolved database outcome for the affected identities. The
/// record owns only identifiers and expected generation; no Player/session
/// pointer may be stored here.
bool BlockCharacterRecovery(CharacterRecoveryRecord const& record);

/// Prevents login, mutation and ordinary saves for an unresolved identity.
bool IsCharacterRecoveryBlocked(uint32 guid);
bool IsAccountRecoveryBlocked(uint32 accountId);

/// Clears a block only after an authoritative reconciliation has proven the
/// identity's persisted state. This does not retry the original operation.
bool ClearCharacterRecovery(uint32 guid);
std::vector<CharacterRecoveryRecord> GetCharacterRecoveryRecords();

#endif
