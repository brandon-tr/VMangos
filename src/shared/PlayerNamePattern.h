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
 * along with this program; if not, write to the Free Software Foundation,
 * Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
 */

#ifndef MANGOS_PLAYER_NAME_PATTERN_H
#define MANGOS_PLAYER_NAME_PATTERN_H

#include <cstddef>
#include <regex>
#include <string>
#include <vector>

/// One immutable list of client-compatible restricted-name patterns.
///
/// The list is compiled once by the constructor. Unsupported grammar, invalid
/// UTF-8, non-BMP or control pattern content, and empty patterns are rejected;
/// no partial list is exposed after a failure.
class PlayerNamePatternList
{
public:
    explicit PlayerNamePatternList(std::vector<std::string> const& patterns);

    /// Whether any compiled pattern matches `subject`.
    ///
    /// The subject must be valid UTF-8, contain only BMP scalars, exclude NUL,
    /// CR, LF, U+2028 and U+2029, and contain at most 1,023 UTF-16 units.
    /// Invalid content throws std::invalid_argument; content above the unit
    /// bound throws std::length_error. The empty subject is permitted.
    ///
    /// Matching-time std::regex_error may propagate. The optional whole-subject
    /// prefilter has a bounded-pattern fallback only for error_complexity;
    /// callers must treat every exception as failed validation, never as a
    /// successful absence of restricted content.
    bool Matches(std::string const& subject) const;

private:
    struct Pattern
    {
        std::wstring core;
        bool leadingBoundary = false;
        bool trailingBoundary = false;
        std::wregex regex;
    };

    static Pattern compilePattern(std::string const& raw, std::size_t index);

    std::vector<Pattern> m_patterns;
};

#endif // MANGOS_PLAYER_NAME_PATTERN_H
