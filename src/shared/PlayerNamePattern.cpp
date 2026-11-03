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

#include "PlayerNamePattern.h"

#include "utf8cpp/utf8.h"

#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace
{
constexpr std::size_t MAX_SUBJECT_UNITS = 1023;

enum class DecodeResult
{
    Ok,
    Invalid,
    TooLong
};

std::string patternError(std::size_t index, std::string const& reason)
{
    return "pattern " + std::to_string(index) + ": " + reason;
}

bool isClientWord(wchar_t c)
{
    return (c >= L'0' && c <= L'9') ||
           (c >= L'A' && c <= L'Z') ||
           (c >= L'a' && c <= L'z') ||
           c == L'_' ||
           (c >= 0x00C0 && c <= 0x00D6) ||
           (c >= 0x00D8 && c <= 0x00DD) ||
           (c >= 0x00DF && c <= 0x00F6) ||
           (c >= 0x00F8 && c <= 0x00FF);
}

bool isForbiddenPatternScalar(wchar_t c)
{
    return c < 0x20 || (c >= 0x7F && c <= 0x9F) || c == 0x2028 || c == 0x2029;
}

bool isForbiddenSubjectScalar(wchar_t c)
{
    return c == 0 || c == L'\r' || c == L'\n' || c == 0x2028 || c == 0x2029;
}

std::wstring asciiLower(std::wstring text)
{
    for (wchar_t& c : text)
        if (c >= L'A' && c <= L'Z')
            c = static_cast<wchar_t>(c + 0x20);
    return text;
}

DecodeResult decodeUtf8(std::string const& text, bool pattern,
                        std::wstring& result, std::string& reason)
{
    result.clear();
    reason.clear();

    std::string::const_iterator it = text.begin();
    std::string::const_iterator const end = text.end();
    try
    {
        while (it != end)
        {
            std::uint32_t const scalar =
                static_cast<std::uint32_t>(utf8::next(it, end));
            if (scalar > 0xFFFF)
            {
                reason = pattern ? "non-BMP scalar" : "unsupported scalar";
                return DecodeResult::Invalid;
            }

            wchar_t const c = static_cast<wchar_t>(scalar);
            if (pattern ? isForbiddenPatternScalar(c) : isForbiddenSubjectScalar(c))
            {
                reason = pattern ? "control character" : "excluded character";
                return DecodeResult::Invalid;
            }

            if (!pattern && result.size() >= MAX_SUBJECT_UNITS)
            {
                reason = "subject exceeds 1023 UTF-16 units";
                return DecodeResult::TooLong;
            }

            result.push_back(c);
        }
    }
    catch (utf8::exception const& e)
    {
        reason = std::string("invalid UTF-8: ") + e.what();
        return DecodeResult::Invalid;
    }

    return DecodeResult::Ok;
}

bool boundaryAt(std::wstring const& subject, std::size_t position)
{
    bool const before = position > 0 && isClientWord(subject[position - 1]);
    bool const after = position < subject.size() && isClientWord(subject[position]);
    return before != after;
}

// shortcut: supports the verified DBC grammar; extend only with
// client-comparison tests when the corpus changes.
bool validateGrammar(std::wstring const& core, std::string& reason)
{
    if (core.empty())
    {
        reason = "empty core";
        return false;
    }

    bool canQuantify = false;
    for (std::size_t i = 0; i < core.size();)
    {
        wchar_t const c = core[i];
        if (c == L'[')
        {
            std::size_t const close = core.find(L']', i + 1);
            if (close == std::wstring::npos)
            {
                reason = "unclosed character class";
                return false;
            }
            std::wstring const body = core.substr(i + 1, close - i - 1);
            if (body.empty())
            {
                reason = "empty character class";
                return false;
            }
            if (body[0] == L'^')
            {
                reason = "negated character class";
                return false;
            }
            if (body.find(L'-') != std::wstring::npos)
            {
                reason = "ranged character class";
                return false;
            }
            if (body.find(L'\\') != std::wstring::npos)
            {
                reason = "escape inside character class";
                return false;
            }
            if (body.find(L'[') != std::wstring::npos)
            {
                reason = "nested character class";
                return false;
            }
            if (body.find(L'^') != std::wstring::npos)
            {
                reason = "caret inside character class";
                return false;
            }
            i = close + 1;
            canQuantify = true;
            continue;
        }
        if (c == L']')
        {
            reason = "stray class delimiter";
            return false;
        }
        if (c == L'\\')
        {
            reason = "unsupported escape";
            return false;
        }
        if (c == L'(' || c == L')' || c == L'|')
        {
            reason = "groups/alternation unsupported";
            return false;
        }
        if (c == L'{' || c == L'}')
        {
            reason = "brace quantifier unsupported";
            return false;
        }
        if (c == L'*' || c == L'+' || c == L'?')
        {
            if (!canQuantify)
            {
                reason = "quantifier without preceding atom";
                return false;
            }
            if (i + 1 < core.size() && (core[i + 1] == L'?' || core[i + 1] == L'+'))
            {
                reason = "lazy/possessive quantifier unsupported";
                return false;
            }
            canQuantify = false;
            ++i;
            continue;
        }
        if (c == L'^' || c == L'$')
        {
            canQuantify = false;
            ++i;
            continue;
        }
        if (c == L'.')
        {
            canQuantify = true;
            ++i;
            continue;
        }
        canQuantify = true;
        ++i;
    }
    return true;
}
}

PlayerNamePatternList::Pattern PlayerNamePatternList::compilePattern(
    std::string const& raw, std::size_t index)
{
    if (raw.empty())
        throw std::invalid_argument(patternError(index, "empty pattern"));

    std::wstring text;
    std::string reason;
    if (decodeUtf8(raw, true, text, reason) != DecodeResult::Ok)
        throw std::invalid_argument(patternError(index, reason));

    PlayerNamePatternList::Pattern pattern;
    while (text.size() >= 2 && text.compare(0, 2, L"\\<") == 0)
    {
        pattern.leadingBoundary = true;
        text.erase(0, 2);
    }
    while (text.size() >= 2 && text.compare(text.size() - 2, 2, L"\\>") == 0)
    {
        pattern.trailingBoundary = true;
        text.erase(text.size() - 2);
    }

    if (text.find(L"\\<") != std::wstring::npos ||
        text.find(L"\\>") != std::wstring::npos)
    {
        throw std::invalid_argument(
            patternError(index, "boundary marker not at outer edge"));
    }
    if (!validateGrammar(text, reason))
        throw std::invalid_argument(patternError(index, reason));

    pattern.core = asciiLower(text);
    try
    {
        pattern.regex.assign(pattern.core,
                             std::regex::ECMAScript | std::regex::optimize);
    }
    catch (std::regex_error const& e)
    {
        throw std::invalid_argument(
            patternError(index, std::string("regex compilation failed: ") + e.what()));
    }
    return pattern;
}

PlayerNamePatternList::PlayerNamePatternList(std::vector<std::string> const& patterns)
{
    if (patterns.empty())
        throw std::invalid_argument("empty pattern list");

    std::vector<Pattern> compiled;
    compiled.reserve(patterns.size());
    for (std::size_t i = 0; i < patterns.size(); ++i)
        compiled.push_back(compilePattern(patterns[i], i));

    m_patterns.swap(compiled);
}

bool PlayerNamePatternList::Matches(std::string const& subject) const
{
    std::wstring text;
    std::string reason;
    DecodeResult const decoded = decodeUtf8(subject, false, text, reason);
    if (decoded == DecodeResult::Invalid)
        throw std::invalid_argument("subject: " + reason);
    if (decoded == DecodeResult::TooLong)
        throw std::length_error("subject: " + reason);

    text = asciiLower(text);

    std::vector<std::size_t> boundaries;
    for (std::size_t position = 0; position <= text.size(); ++position)
        if (boundaryAt(text, position))
            boundaries.push_back(position);

    for (Pattern const& pattern : m_patterns)
    {
        // Removing outer boundaries produces a superset of the bounded match.
        // The prefilter is optional: only error_complexity on a bounded pattern
        // falls through to exact spans. Every other engine error, and every
        // error from the actual matching calls below, propagates.
        bool prefiltered = true;
        try
        {
            if (!std::regex_search(text, pattern.regex))
                prefiltered = false;
        }
        catch (std::regex_error const& e)
        {
            if (e.code() != std::regex_constants::error_complexity ||
                (!pattern.leadingBoundary && !pattern.trailingBoundary))
            {
                throw;
            }
        }
        if (!prefiltered)
            continue;

        if (!pattern.leadingBoundary && !pattern.trailingBoundary)
            return true;

        if (pattern.leadingBoundary && !pattern.trailingBoundary)
        {
            for (std::size_t start : boundaries)
            {
                std::regex_constants::match_flag_type flags =
                    std::regex_constants::match_continuous;
                if (start > 0)
                    flags |= std::regex_constants::match_not_bol;
                std::wsmatch match;
                if (std::regex_search(
                        text.cbegin() + static_cast<std::ptrdiff_t>(start),
                        text.cend(), match, pattern.regex, flags))
                {
                    return true;
                }
            }
            continue;
        }

        if (!pattern.leadingBoundary && pattern.trailingBoundary)
        {
            for (std::size_t end : boundaries)
            {
                for (std::size_t start = 0; start <= end; ++start)
                {
                    std::regex_constants::match_flag_type flags =
                        std::regex_constants::match_default;
                    if (start > 0)
                        flags |= std::regex_constants::match_not_bol;
                    if (end < text.size())
                        flags |= std::regex_constants::match_not_eol;
                    std::wsmatch match;
                    if (std::regex_match(
                            text.cbegin() + static_cast<std::ptrdiff_t>(start),
                            text.cbegin() + static_cast<std::ptrdiff_t>(end),
                            match, pattern.regex, flags))
                    {
                        return true;
                    }
                }
            }
            continue;
        }

        for (std::size_t start : boundaries)
        {
            for (std::size_t end : boundaries)
            {
                if (end < start)
                    continue;
                std::regex_constants::match_flag_type flags =
                    std::regex_constants::match_default;
                if (start > 0)
                    flags |= std::regex_constants::match_not_bol;
                if (end < text.size())
                    flags |= std::regex_constants::match_not_eol;
                std::wsmatch match;
                if (std::regex_match(
                        text.cbegin() + static_cast<std::ptrdiff_t>(start),
                        text.cbegin() + static_cast<std::ptrdiff_t>(end),
                        match, pattern.regex, flags))
                {
                    return true;
                }
            }
        }
    }
    return false;
}
