#ifndef CHARACTER_IDENTITY_PERSISTENCE_H
#define CHARACTER_IDENTITY_PERSISTENCE_H

#include "Database/CharacterPersistenceContext.h"

#include <cstddef>
#include <limits>
#include <string>

struct MailMoneyWithdrawal
{
    CharacterIdentitySnapshot identity;
    uint32 mailId = 0;
    uint32 amount = 0;
    uint32 currentBalance = 0;
    uint32 maximumBalance = 0;
};

/// Assembles the guarded mail-money transaction without executing it.
/// projectedBalance is a proposal and changes only after successful assembly.
bool AppendMailMoneyWithdrawal(
    Database& database,
    CharacterPersistenceContext& context,
    MailMoneyWithdrawal const& request,
    uint32& projectedBalance);

struct SoftCharacterDeletion
{
    CharacterIdentitySnapshot identity;
    uint64 deletionTime = 0;
};

bool AppendSoftCharacterDeletion(
    Database& database,
    CharacterPersistenceContext& context,
    SoftCharacterDeletion const& request);

struct CharacterRestoration
{
    uint32 guid = 0;
    uint64 expectedGeneration = 0;
    std::string deletedName;
    uint32 deletedAccountId = 0;
    uint64 deletedTime = 0;
    std::string targetCanonicalName;
    uint32 targetAccountId = 0;
};

bool AppendCharacterRestoration(
    Database& database,
    CharacterPersistenceContext& context,
    CharacterRestoration const& request);

struct CharacterDeletionSnapshot
{
    CharacterIdentitySnapshot identity;
    uint64 deletedTime = 0;
};

/// Appends the permanent-deletion cleanup for one exact identity instance.
/// Active rows and tombstones use distinct first guards. Ownership release,
/// related rows and the final row delete are in the same checked batch.
bool AppendPermanentCharacterDeletion(
    Database& database,
    CharacterPersistenceContext& context,
    CharacterDeletionSnapshot const& expected);

enum class CharacterNameWriteStatus
{
    Committed,
    NameInUse,
    Rejected,
    OutcomeUnknown
};

CharacterNameWriteStatus ClassifyCharacterNameClaim(
    SqlBatchResult const& result,
    std::size_t claimStatementIndex);

#endif