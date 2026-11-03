/*
 * Copyright (C) 2005-2011 MaNGOS <http://getmangos.com/>
 * Copyright (C) 2009-2011 MaNGOSZero <https://github.com/mangos/zero>
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
 */

#ifndef MANGOS_PLAYER_NAME_H
#define MANGOS_PLAYER_NAME_H

#include <cstddef>
#include <cstdint>
#include <string>

/// Formats a two-part character name as "First Last".
///
/// This is formatting only: it enforces exactly one ASCII separator, capitalizes
/// the first scalar of each part and lowercases the rest, and applies an absolute
/// 12-UTF-16-unit ceiling per part. It deliberately does not establish the final
/// naming policy: minimum length, allowed scripts, locale caps, repetition,
/// restricted words, availability, and permissions belong to later validators.
///
/// The input is replaced only after every check and conversion succeeds. On
/// failure the argument is left byte-for-byte unchanged.
bool normalizeFullPlayerName(std::string& name);

/// The two client charset ids a name is checked against. The ids are the client
/// validator's: 1 = Latin-1, 2 = the listed Hangul syllables, 3 = CJK
/// ideographs, 4 = ASCII letters only.
struct PlayerNameCharsets
{
    std::uint8_t primary;
    std::uint8_t secondary;

    /// The pair the client selects for its `Region` and locale index.
    static PlayerNameCharsets ForClient(std::int32_t region, std::size_t locale);
};

/// The three restricted-word lists a caller supplies for one validation pass.
/// The implementation is deliberately generic: this module does not load DBCs,
/// SQL reserved names, or global configuration.
class PlayerNameLists
{
public:
    virtual ~PlayerNameLists() = default;

    virtual bool NamesProfanity(std::string const& name) const = 0;
    virtual bool ChatProfanity(std::string const& name) const = 0;
    virtual bool NamesReserved(std::string const& name) const = 0;
};

/// The status of a raw "First Last" spelling under the client's per-part and
/// combined rules, without changing the input. Returns the client's CHAR_NAME_*
/// status bytes 0x45 through 0x52.
std::uint8_t checkFullPlayerName(
    std::string const& name,
    PlayerNameLists const& lists,
    PlayerNameCharsets charsets);

/// Validates the entered spelling, formats a canonical "First Last" temporary,
/// validates that canonical spelling, and only then replaces `name`. On every
/// failure the input is left byte-for-byte unchanged and the failing status is
/// returned. No alphabet is widened and no alternate spelling is attempted.
std::uint8_t prepareFullPlayerName(
    std::string& name,
    PlayerNameLists const& lists,
    PlayerNameCharsets charsets);

/// Whether `name` is an already-canonical, admitted two-part full name under
/// any of the client's effective charset pairs, with no restricted-word lists.
/// This is a stored-identity check only; it does not read session, realm, or
/// DBC-list state.
bool isCanonicalFullPlayerName(std::string const& name);

/// Canonicalizes a lookup key without repairing or admitting a stored identity.
/// Exactly one full-name formatting/canonical check must succeed; on failure
/// the input is unchanged.
bool normalizeFullPlayerNameForLookup(std::string& name);

/// Decodes the fixed six-byte custom world-auth name-profile header:
/// "EFNM", version 1, then Region 0..5. Returns false without changing
/// `region` for a null/truncated/oversized/malformed header.
bool decodePlayerNameHandshake(
    std::uint8_t const* data,
    std::size_t size,
    std::uint8_t& region);

#endif // MANGOS_PLAYER_NAME_H
