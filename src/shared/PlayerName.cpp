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

#include "PlayerName.h"

#include "PlayerNameHangul.h"
#include "Util.h"
#include "utf8cpp/utf8.h"

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>

namespace
{
// Two parts of at most 12 UTF-16 units each, one ASCII separator. The largest
// possible UTF-8 encoding is 12 three-byte BMP scalars per part plus the space:
// 12 * 3 + 1 + 12 * 3 = 73 bytes. Rejecting longer input early cannot reject a
// structurally valid name under the unit ceiling.
std::size_t const MAX_FULL_NAME_BYTES = 73;

// Absolute formatting capacity per part. This is not the later naming policy:
// allowed scripts, locale-specific caps, minimum length, repetition and the
// word lists all belong to the validator, not to this formatter.
std::size_t const MAX_PART_UTF16_UNITS = 12;

bool isForbiddenScalar(unsigned int scalar)
{
    if (scalar <= 0x1F)                                     // C0 controls, including NUL
        return true;
    if (scalar >= 0x7F && scalar <= 0x9F)                   // DEL and C1 controls
        return true;

    switch (scalar)
    {
        case 0x00A0:                                        // NO-BREAK SPACE
        case 0x1680:                                        // OGHAM SPACE MARK
        case 0x2028:                                        // LINE SEPARATOR
        case 0x2029:                                        // PARAGRAPH SEPARATOR
        case 0x202F:                                        // NARROW NO-BREAK SPACE
        case 0x205F:                                        // MEDIUM MATHEMATICAL SPACE
        case 0x3000:                                        // IDEOGRAPHIC SPACE
            return true;
        default:
            break;
    }

    return scalar >= 0x2000 && scalar <= 0x200A;            // EN QUAD..HAIR SPACE
}

// Complete the missing case counterpart locally without changing the shared
// mappings. This is only used for BMP scalars; supplementary scalars have no
// single-character casing in the existing tables.
unsigned int caseScalar(unsigned int scalar, bool first)
{
    if (first)
    {
        if (scalar == 0x00FF)                               // LATIN SMALL LETTER Y WITH DIAERESIS
            return 0x0178;                                  // LATIN CAPITAL LETTER Y WITH DIAERESIS
        if (scalar <= 0xFFFF)
            return static_cast<unsigned int>(wcharToUpper(static_cast<wchar_t>(scalar)));
    }
    else
    {
        if (scalar == 0x0178)                               // LATIN CAPITAL LETTER Y WITH DIAERESIS
            return 0x00FF;                                  // LATIN SMALL LETTER Y WITH DIAERESIS
        if (scalar <= 0xFFFF)
            return static_cast<unsigned int>(wcharToLower(static_cast<wchar_t>(scalar)));
    }

    return scalar;
}

bool normalizePart(std::string const& part, std::string& result)
{
    std::string normalized;
    normalized.reserve(part.size() + 1);

    std::string::const_iterator it = part.begin();
    std::string::const_iterator const end = part.end();
    std::size_t units = 0;
    bool first = true;

    while (it != end)
    {
        unsigned int const scalar = static_cast<unsigned int>(utf8::next(it, end));
        if (isForbiddenScalar(scalar))
            return false;

        units += scalar > 0xFFFF ? 2 : 1;
        if (units > MAX_PART_UTF16_UNITS)
            return false;

        utf8::append(caseScalar(scalar, first), normalized);
        first = false;
    }

    if (units == 0)
        return false;

    result.swap(normalized);
    return true;
}

std::uint8_t const NAME_NO_NAME = 0x45;
std::uint8_t const NAME_TOO_SHORT = 0x46;
std::uint8_t const NAME_TOO_LONG = 0x47;
std::uint8_t const NAME_INVALID_CHARACTER = 0x48;
std::uint8_t const NAME_RESERVED = 0x4b;
std::uint8_t const NAME_THREE_CONSECUTIVE = 0x4e;
std::uint8_t const NAME_FAILURE = 0x51;
std::uint8_t const NAME_SUCCESS = 0x52;

constexpr std::size_t MAX_CONVERSION_UNITS = 1023;

std::uint16_t foldAscii(std::uint16_t unit)
{
    return unit >= 0x41 && unit <= 0x5A
        ? static_cast<std::uint16_t>(unit + 0x20)
        : unit;
}

bool inLatin1(std::uint16_t unit)
{
    return (unit >= 0x41 && unit <= 0x5A) ||
           (unit >= 0x61 && unit <= 0x7A) ||
           ((unit >= 0xC0 && unit <= 0xFF) &&
            unit != 0xD7 && unit != 0xDE && unit != 0xF7);
}

bool inCjk(std::uint16_t unit)
{
    return (unit >= 0x3400 && unit <= 0x4DFF) ||
           (unit >= 0x4E00 && unit <= 0x9FFF) ||
           (unit >= 0xF900 && unit <= 0xFAFF);
}

bool inCharset(std::uint8_t charset, std::uint16_t unit)
{
    switch (charset)
    {
        case 1:
            return inLatin1(unit);
        case 2:
            return PlayerNameHangul::IsListed(unit);
        case 3:
            return inCjk(unit);
        case 4:
            return (unit >= 0x41 && unit <= 0x5A) ||
                   (unit >= 0x61 && unit <= 0x7A);
        default:
            return false;
    }
}

bool decodePart(std::string const& part,
                std::array<std::uint16_t, MAX_CONVERSION_UNITS>& units,
                std::size_t& count,
                bool& tooLong)
{
    count = 0;
    tooLong = false;

    std::string::const_iterator it = part.begin();
    std::string::const_iterator const end = part.end();
    try
    {
        while (it != end)
        {
            std::uint32_t const scalar = static_cast<std::uint32_t>(utf8::next(it, end));
            if (scalar <= 0xFFFF)
            {
                if (count < units.size())
                    units[count++] = static_cast<std::uint16_t>(scalar);
                else
                    tooLong = true;
            }
            else
            {
                std::uint32_t const offset = scalar - 0x10000;
                std::uint16_t const high =
                    static_cast<std::uint16_t>(0xD800 + (offset >> 10));
                std::uint16_t const low =
                    static_cast<std::uint16_t>(0xDC00 + (offset & 0x3FF));

                if (count < units.size())
                    units[count++] = high;
                else
                    tooLong = true;
                if (count < units.size())
                    units[count++] = low;
                else
                    tooLong = true;
            }
        }
    }
    catch (utf8::exception const&)
    {
        return false;
    }

    return true;
}

std::uint8_t checkPart(std::string const& part,
                       PlayerNameLists const& lists,
                       PlayerNameCharsets charsets)
{
    std::array<std::uint16_t, MAX_CONVERSION_UNITS> units{};
    std::size_t count = 0;
    bool tooLong = false;

    if (!decodePart(part, units, count, tooLong))
        return NAME_INVALID_CHARACTER;
    if (tooLong)
        return NAME_FAILURE;
    if (count == 0)
        return NAME_NO_NAME;

    std::uint8_t charset = 0;
    for (std::size_t i = 0; i < count; ++i)
    {
        std::uint16_t const unit = units[i];
        if (charset == 0)
        {
            if (inCharset(charsets.primary, unit))
                charset = charsets.primary;
            else if (inCharset(charsets.secondary, unit))
                charset = charsets.secondary;
        }

        if (charset != 0 && inCharset(charset, unit))
        {
            if (i >= 2)
            {
                std::uint16_t const a = units[i - 2];
                std::uint16_t const b = units[i - 1];
                bool const same = charset == 1
                    ? foldAscii(a) == foldAscii(b) && foldAscii(a) == foldAscii(unit)
                    : a == b && a == unit;
                if (same)
                    return NAME_THREE_CONSECUTIVE;
            }
            continue;
        }

        return NAME_INVALID_CHARACTER;
    }

    if (count == 1)
        return NAME_TOO_SHORT;

    if (lists.NamesProfanity(part) ||
        lists.ChatProfanity(part) ||
        lists.NamesReserved(part))
    {
        return NAME_RESERVED;
    }

    std::size_t const cap = charset == 1 ? 12 : charset == 3 ? 6 : 8;
    if (count > cap)
        return NAME_TOO_LONG;

    return NAME_SUCCESS;
}
}

bool normalizeFullPlayerName(std::string& name)
{
    if (name.size() > MAX_FULL_NAME_BYTES)
        return false;

    std::string::size_type const separator = name.find(' ');
    if (separator == std::string::npos)
        return false;
    if (name.find(' ', separator + 1) != std::string::npos)
        return false;
    if (separator == 0 || separator + 1 == name.size())
        return false;

    try
    {
        std::string first;
        std::string last;
        if (!normalizePart(name.substr(0, separator), first))
            return false;
        if (!normalizePart(name.substr(separator + 1), last))
            return false;

        std::string normalized;
        normalized.reserve(first.size() + 1 + last.size());
        normalized.append(first);
        normalized.push_back(' ');
        normalized.append(last);

        // Publish only after both parts and the final assembly succeeded. Every
        // failure above leaves the caller's string byte-for-byte unchanged.
        name.swap(normalized);
        return true;
    }
    catch (utf8::exception const&)
    {
        // Malformed UTF-8 is a normal rejection. Allocation failures are not
        // caught here and are not reported as a valid formatting result.
        return false;
    }
}

PlayerNameCharsets PlayerNameCharsets::ForClient(std::int32_t region, std::size_t locale)
{
    std::uint8_t const regions[5][2] = {
        {1, 1}, {2, 4}, {1, 1}, {3, 1}, {3, 1}
    };
    std::uint8_t const locales[8] = {1, 2, 1, 1, 3, 3, 1, 0};

    if (region >= 1 && region <= 5)
        return PlayerNameCharsets{regions[region - 1][0], regions[region - 1][1]};

    std::size_t index = 0;
    if (locale < 8 && locales[locale] != 0)
        index = locale;

    return PlayerNameCharsets{
        locales[index],
        static_cast<std::uint8_t>(index == 1 ? 4 : 1)
    };
}

std::uint8_t checkFullPlayerName(
    std::string const& name,
    PlayerNameLists const& lists,
    PlayerNameCharsets charsets)
{
    if (name.empty())
        return NAME_NO_NAME;

    std::string::size_type const separator = name.find(' ');
    if (separator == std::string::npos)
        return NAME_INVALID_CHARACTER;

    std::string const first = name.substr(0, separator);
    std::string const last = name.substr(separator + 1);

    std::uint8_t status = checkPart(first, lists, charsets);
    if (status != NAME_SUCCESS)
        return status;

    status = checkPart(last, lists, charsets);
    if (status != NAME_SUCCESS)
        return status;

    std::string const joined = first + last;
    if (lists.NamesProfanity(name) ||
        lists.ChatProfanity(name) ||
        lists.NamesReserved(name) ||
        lists.NamesProfanity(joined) ||
        lists.ChatProfanity(joined) ||
        lists.NamesReserved(joined))
    {
        return NAME_RESERVED;
    }

    return NAME_SUCCESS;
}

std::uint8_t prepareFullPlayerName(
    std::string& name,
    PlayerNameLists const& lists,
    PlayerNameCharsets charsets)
{
    std::uint8_t status = checkFullPlayerName(name, lists, charsets);
    if (status != NAME_SUCCESS)
        return status;

    std::string canonical = name;
    if (!normalizeFullPlayerName(canonical))
        return NAME_INVALID_CHARACTER;

    status = checkFullPlayerName(canonical, lists, charsets);
    if (status != NAME_SUCCESS)
        return status;

    name.swap(canonical);
    return NAME_SUCCESS;
}

namespace
{
class NoRestrictions final : public PlayerNameLists
{
public:
    bool NamesProfanity(std::string const&) const override { return false; }
    bool ChatProfanity(std::string const&) const override { return false; }
    bool NamesReserved(std::string const&) const override { return false; }
};
}

bool isCanonicalFullPlayerName(std::string const& name)
{
    if (name.empty() || name.size() > MAX_FULL_NAME_BYTES)
        return false;

    NoRestrictions const none;
    PlayerNameCharsets const pairs[] = {
        PlayerNameCharsets{1, 1},
        PlayerNameCharsets{2, 4},
        PlayerNameCharsets{3, 1},
    };
    for (PlayerNameCharsets const& pair : pairs)
    {
        std::string candidate = name;
        if (prepareFullPlayerName(candidate, none, pair) == NAME_SUCCESS &&
            candidate == name)
        {
            return true;
        }
    }
    return false;
}

bool normalizeFullPlayerNameForLookup(std::string& name)
{
    if (name.empty() || name.size() > MAX_FULL_NAME_BYTES)
        return false;

    std::string candidate = name;
    if (!normalizeFullPlayerName(candidate))
        return false;
    if (!isCanonicalFullPlayerName(candidate))
        return false;

    name.swap(candidate);
    return true;
}

bool decodePlayerNameHandshake(
    std::uint8_t const* data,
    std::size_t size,
    std::uint8_t& region)
{
    if (!data || size != 6)
        return false;
    if (data[0] != 0x45 || data[1] != 0x46 ||
        data[2] != 0x4E || data[3] != 0x4D)
    {
        return false;
    }
    if (data[4] != 1 || data[5] > 5)
        return false;

    region = data[5];
    return true;
}
