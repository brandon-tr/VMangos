#include "Database/CharacterPersistenceRecovery.h"

#include <algorithm>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <utility>

struct AccountLifecycleRegistry::Impl
{
    struct Active
    {
        uint64 id;
        AccountLifecycleKind kind;
    };

    mutable std::mutex mutex;
    std::map<uint32, Active> active;
    std::set<uint32> retired;
    std::vector<CharacterRecoveryRecord> records;
    uint64 nextId = 1;

    bool allowsLocked(AccountLifecycleLease const& lease, uint32 accountId,
                      AccountLifecycleKind kind) const
    {
        if (lease.RegistryIdentity() != this ||
            lease.GetKind() != kind ||
            !lease.Covers(accountId) ||
            retired.find(accountId) != retired.end())
            return false;
        auto const itr = active.find(accountId);
        return itr != active.end() && itr->second.id == lease.LeaseId() &&
               itr->second.kind == kind;
    }

    void release(uint64 id, std::vector<uint32> const& accounts)
    {
        std::lock_guard<std::mutex> lock(mutex);
        for (uint32 accountId : accounts)
        {
            auto const itr = active.find(accountId);
            if (itr != active.end() && itr->second.id == id)
                active.erase(itr);
        }
    }
};

struct AccountLifecycleLease::State
{
    std::weak_ptr<AccountLifecycleRegistry::Impl> registry;
    void const* identity = nullptr;
    uint64 id = 0;
    std::vector<uint32> accounts;
    AccountLifecycleKind kind = AccountLifecycleKind::Mutation;

    ~State()
    {
        if (std::shared_ptr<AccountLifecycleRegistry::Impl> impl = registry.lock())
            impl->release(id, accounts);
    }
};

AccountLifecycleLease::AccountLifecycleLease(std::shared_ptr<State> state)
    : m_state(std::move(state)), m_kind(m_state->kind)
{
}

AccountLifecycleLease::~AccountLifecycleLease() = default;

void const* AccountLifecycleLease::RegistryIdentity() const
{
    return m_state->identity;
}

uint64 AccountLifecycleLease::LeaseId() const
{
    return m_state->id;
}

bool AccountLifecycleLease::Covers(uint32 accountId) const
{
    return std::find(m_state->accounts.begin(), m_state->accounts.end(),
                     accountId) != m_state->accounts.end();
}

bool AccountLifecycleLease::IsActive() const
{
    std::shared_ptr<AccountLifecycleRegistry::Impl> impl = m_state->registry.lock();
    if (!impl || !m_state->id)
        return false;
    std::lock_guard<std::mutex> lock(impl->mutex);
    for (uint32 accountId : m_state->accounts)
    {
        auto const itr = impl->active.find(accountId);
        if (itr == impl->active.end() || itr->second.id != m_state->id)
            return false;
    }
    return true;
}

AccountLifecycleRegistry::AccountLifecycleRegistry()
    : m_impl(std::make_shared<Impl>())
{
}

AccountLifecycleRegistry::~AccountLifecycleRegistry() = default;

AccountLifecycleRegistry& GetAccountLifecycleRegistry()
{
    static AccountLifecycleRegistry registry;
    return registry;
}

bool CharacterPersistenceAdmission::Admit(
    std::vector<uint32> const& accountIds, AccountLifecycleKind kind,
    AccountLifecycleLease const* borrowed)
{
    m_owned.reset();
    m_accounts.clear();
    m_admitted = false;

    std::vector<uint32> accounts = accountIds;
    std::sort(accounts.begin(), accounts.end());
    accounts.erase(std::unique(accounts.begin(), accounts.end()),
                   accounts.end());
    if (accounts.empty() ||
        std::find(accounts.begin(), accounts.end(), 0) != accounts.end())
        return false;

    for (uint32 accountId : accounts)
        if (IsAccountRecoveryBlocked(accountId))
            return false;

    if (borrowed && !borrowed->IsActive())
        return false;

    std::vector<uint32> needed;
    for (uint32 accountId : accounts)
    {
        if (borrowed && borrowed->Covers(accountId))
        {
            if (!GetAccountLifecycleRegistry().Allows(*borrowed, accountId,
                                                      kind))
                return false;
        }
        else
            needed.push_back(accountId);
    }

    if (!needed.empty())
    {
        m_owned = GetAccountLifecycleRegistry().TryAcquire(needed, kind);
        if (!m_owned || !m_owned->IsActive())
            return false;
        for (uint32 accountId : needed)
            if (!GetAccountLifecycleRegistry().Allows(*m_owned, accountId,
                                                      kind))
                return false;
    }

    m_accounts = accounts;
    m_admitted = true;
    return true;
}

AccountLifecycleLeasePtr AccountLifecycleRegistry::TryAcquire(
    std::vector<uint32> const& accounts, AccountLifecycleKind kind)
{
    if (accounts.empty())
        return nullptr;

    std::vector<uint32> normalized = accounts;
    std::sort(normalized.begin(), normalized.end());
    normalized.erase(std::unique(normalized.begin(), normalized.end()),
                     normalized.end());
    if (normalized.empty() ||
        std::find(normalized.begin(), normalized.end(), 0) != normalized.end())
        return nullptr;

    std::lock_guard<std::mutex> lock(m_impl->mutex);
    for (uint32 accountId : normalized)
    {
        if (m_impl->active.find(accountId) != m_impl->active.end() ||
            m_impl->retired.find(accountId) != m_impl->retired.end())
            return nullptr;
        for (CharacterRecoveryRecord const& record : m_impl->records)
        {
            if (std::find(record.accountIds.begin(), record.accountIds.end(),
                          accountId) != record.accountIds.end())
                return nullptr;
        }
    }

    std::shared_ptr<AccountLifecycleLease::State> state =
        std::make_shared<AccountLifecycleLease::State>();
    state->registry = m_impl;
    state->identity = m_impl.get();
    state->id = m_impl->nextId++;
    state->accounts = normalized;
    state->kind = kind;

    for (uint32 accountId : normalized)
        m_impl->active[accountId] = Impl::Active{state->id, kind};

    AccountLifecycleLeasePtr lease(
        new AccountLifecycleLease(std::move(state)));
    return lease;
}

bool AccountLifecycleRegistry::Allows(
    AccountLifecycleLease const& lease, uint32 accountId,
    AccountLifecycleKind kind) const
{
    if (accountId == 0)
        return false;
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    return m_impl->allowsLocked(lease, accountId, kind);
}

bool AccountLifecycleRegistry::MarkDeleted(
    AccountLifecycleLease const& lease, uint32 accountId)
{
    if (accountId == 0)
        return false;
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    if (!m_impl->allowsLocked(lease, accountId,
                              AccountLifecycleKind::Deletion))
        return false;
    m_impl->retired.insert(accountId);
    return true;
}

bool AccountLifecycleRegistry::BlockRecovery(
    CharacterRecoveryRecord const& record)
{
    std::vector<uint32> guids = record.guids;
    std::vector<uint32> accounts = record.accountIds;
    if (guids.empty() && accounts.empty())
        return false;
    if (std::find(guids.begin(), guids.end(), 0) != guids.end() ||
        std::find(accounts.begin(), accounts.end(), 0) != accounts.end())
        return false;

    std::sort(guids.begin(), guids.end());
    guids.erase(std::unique(guids.begin(), guids.end()), guids.end());
    std::sort(accounts.begin(), accounts.end());
    accounts.erase(std::unique(accounts.begin(), accounts.end()),
                   accounts.end());

    CharacterRecoveryRecord normalized = record;
    normalized.guids = std::move(guids);
    normalized.accountIds = std::move(accounts);

    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto const same = std::find_if(
        m_impl->records.begin(), m_impl->records.end(),
        [&](CharacterRecoveryRecord const& existing) {
            return existing.kind == normalized.kind &&
                   existing.guids == normalized.guids &&
                   existing.accountIds == normalized.accountIds &&
                   existing.expectedGeneration == normalized.expectedGeneration &&
                   existing.detail == normalized.detail;
        });
    if (same == m_impl->records.end())
        m_impl->records.push_back(std::move(normalized));
    return true;
}

bool AccountLifecycleRegistry::IsCharacterBlocked(uint32 guid) const
{
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    for (CharacterRecoveryRecord const& record : m_impl->records)
        if (std::find(record.guids.begin(), record.guids.end(), guid) !=
            record.guids.end())
            return true;
    return false;
}

bool AccountLifecycleRegistry::IsAccountBlocked(uint32 accountId) const
{
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    if (m_impl->retired.find(accountId) != m_impl->retired.end())
        return true;
    for (CharacterRecoveryRecord const& record : m_impl->records)
        if (std::find(record.accountIds.begin(), record.accountIds.end(),
                      accountId) != record.accountIds.end())
            return true;
    return false;
}

bool AccountLifecycleRegistry::ClearCharacterBlock(uint32 guid)
{
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    bool removed = false;
    for (auto itr = m_impl->records.begin(); itr != m_impl->records.end();)
    {
        std::vector<uint32>& guids = itr->guids;
        auto const found = std::find(guids.begin(), guids.end(), guid);
        if (found == guids.end())
        {
            ++itr;
            continue;
        }
        guids.erase(found);
        removed = true;
        if (guids.empty() && itr->accountIds.empty())
            itr = m_impl->records.erase(itr);
        else
            ++itr;
    }
    return removed;
}

std::vector<CharacterRecoveryRecord> AccountLifecycleRegistry::Records() const
{
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    return m_impl->records;
}

bool BlockCharacterRecovery(CharacterRecoveryRecord const& record)
{
    return GetAccountLifecycleRegistry().BlockRecovery(record);
}

bool IsCharacterRecoveryBlocked(uint32 guid)
{
    return GetAccountLifecycleRegistry().IsCharacterBlocked(guid);
}

bool IsAccountRecoveryBlocked(uint32 accountId)
{
    return GetAccountLifecycleRegistry().IsAccountBlocked(accountId);
}

bool ClearCharacterRecovery(uint32 guid)
{
    return GetAccountLifecycleRegistry().ClearCharacterBlock(guid);
}

std::vector<CharacterRecoveryRecord> GetCharacterRecoveryRecords()
{
    return GetAccountLifecycleRegistry().Records();
}
