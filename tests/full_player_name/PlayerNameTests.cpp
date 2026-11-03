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

#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <iostream>
#include <limits>
#include <string>
#include <vector>

namespace
{
int checks = 0;
int failures = 0;

std::string hexEncode(std::string const& value)
{
    static char const digits[] = "0123456789abcdef";
    std::string result;
    result.reserve(value.size() * 2);
    for (std::string::size_type i = 0; i < value.size(); ++i)
    {
        unsigned char const byte = static_cast<unsigned char>(value[i]);
        result.push_back(digits[byte >> 4]);
        result.push_back(digits[byte & 0x0F]);
    }
    return result;
}

void fail(char const* label, std::string const& original, std::string const& expected,
          std::string const& actual, bool returned)
{
    ++failures;
    std::cerr << "FAIL " << label << '\n'
              << "  input:    " << hexEncode(original) << '\n'
              << "  expected: " << hexEncode(expected) << '\n'
              << "  actual:   " << hexEncode(actual) << '\n'
              << "  returned: " << (returned ? "true" : "false") << '\n';
}

void expectFormatted(char const* label, std::string input, std::string const& expected)
{
    ++checks;
    std::string const original = input;
    bool const returned = normalizeFullPlayerName(input);
    if (!returned || input != expected)
        fail(label, original, expected, input, returned);
}

void expectRejected(char const* label, std::string input)
{
    ++checks;
    std::string const original = input;
    bool const returned = normalizeFullPlayerName(input);
    if (returned || input != original)
        fail(label, original, original, input, returned);
}

std::string repeat(std::string const& unit, std::string::size_type count)
{
    std::string result;
    for (std::string::size_type i = 0; i < count; ++i)
        result += unit;
    return result;
}

std::string malformedFirst(std::string const& bytes)
{
    std::string input = "A";
    input += bytes;
    input += " Brightvale";
    return input;
}

std::string malformedLast(std::string const& bytes)
{
    std::string input = "Asha Bright";
    input += bytes;
    return input;
}

std::string insideLast(std::string const& bytes)
{
    std::string input = "Asha Bright";
    input += bytes;
    input += "vale";
    return input;
}

void runShapeTests()
{
    expectFormatted("shape/accept_normal", "Asha Brightvale", "Asha Brightvale");

    expectRejected("shape/reject_empty", "");
    expectRejected("shape/reject_one_part", "Asha");
    expectRejected("shape/reject_empty_first", " Brightvale");
    expectRejected("shape/reject_empty_last", "Asha ");
    expectRejected("shape/reject_only_space", " ");
    expectRejected("shape/reject_leading_space", " Asha Brightvale");
    expectRejected("shape/reject_trailing_space", "Asha Brightvale ");
    expectRejected("shape/reject_doubled_space", "Asha  Brightvale");
    expectRejected("shape/reject_third_component", "Asha Bright vale");
    expectRejected("shape/reject_fourth_component", "Asha Brightvale Extra");
}

void runCaseTests()
{
    expectFormatted("case/both_parts", "aSHA brightVALE", "Asha Brightvale");
    expectFormatted("case/accents", u8"\u00E9CLAIR bRAVO", u8"\u00C9clair Bravo");

    expectFormatted("counterparts/eszett_and_y_diaeresis",
                    u8"\u00DFETA \u00FFARD",
                    u8"\u1E9Eeta \u0178ard");
    expectFormatted("counterparts/thorn", u8"\u00FEOR bRAVO", u8"\u00DEor Bravo");
    expectFormatted("counterparts/internal_upper_forms",
                    u8"A\u1E9E B\u0178",
                    u8"A\u00DF B\u00FF");
    expectFormatted("counterparts/reversed",
                    u8"\u00FFARD \u00DFETA",
                    u8"\u0178ard \u1E9Eeta");

    std::string eszett = u8"\u00DFETA \u00FFARD";
    ++checks;
    bool const returned = normalizeFullPlayerName(eszett);
    if (!returned || eszett.find("SS") != std::string::npos ||
        eszett != u8"\u1E9Eeta \u0178ard")
    {
        fail("counterparts/no_ss_expansion", u8"\u00DFETA \u00FFARD",
             u8"\u1E9Eeta \u0178ard", eszett, returned);
    }
}

void runUncasedTests()
{
    expectFormatted("uncased/hangul_and_cjk",
                    u8"\uAC00\uB098 \u4E00\u4E8C",
                    u8"\uAC00\uB098 \u4E00\u4E8C");
    expectFormatted("uncased/mixed_ascii_hangul",
                    u8"aB\uAC00 cD\u4E00",
                    u8"Ab\uAC00 Cd\u4E00");
}

void runCapacityTests()
{
    expectFormatted("capacity/two_twelve_unit_latin",
                    "Abcdefghijkl Mnopqrstuvwx",
                    "Abcdefghijkl Mnopqrstuvwx");
    expectRejected("capacity/thirteen_first", "Abcdefghijklm Brightvale");
    expectRejected("capacity/thirteen_last", "Asha Abcdefghijklm");

    std::string const accentedUpper = u8"\u00C9";
    std::string const accentedLower = u8"\u00E9";
    std::string const accentedInput =
        repeat(accentedUpper, 12) + " " + repeat(accentedUpper, 12);
    std::string const accentedExpected =
        accentedUpper + repeat(accentedLower, 11) + " " +
        accentedUpper + repeat(accentedLower, 11);

    ++checks;
    if (accentedInput.size() <= 25)
    {
        ++failures;
        std::cerr << "FAIL capacity/accented_precondition: input is not over 25 bytes\n";
    }
    expectFormatted("capacity/twelve_accented_each", accentedInput, accentedExpected);

    std::string const cjk = "\xE4\xB8\x80"; // U+4E00, three UTF-8 bytes
    std::string const full73 = repeat(cjk, 12) + " " + repeat(cjk, 12);
    ++checks;
    if (full73.size() != 73)
    {
        ++failures;
        std::cerr << "FAIL capacity/73_byte_precondition: size is " << full73.size() << '\n';
    }
    expectFormatted("capacity/exactly_73_bytes", full73, full73);

    std::string const over73 = repeat(cjk, 12) + " " + repeat(cjk, 12) + "A";
    ++checks;
    if (over73.size() <= 73)
    {
        ++failures;
        std::cerr << "FAIL capacity/over_73_precondition\n";
    }
    expectRejected("capacity/over_73_bytes", over73);
}

void runUtf16UnitTests()
{
    // Unit-counting tests only. The formatter does not decide whether emoji or
    // any other supplementary scalar is admissible under the naming policy.
    std::string const emoji = "\xF0\x9F\x98\x80"; // U+1F600, two UTF-16 units

    expectFormatted("utf16/six_supplementary_first",
                    repeat(emoji, 6) + " Ab",
                    repeat(emoji, 6) + " Ab");
    expectRejected("utf16/seven_supplementary_first",
                   repeat(emoji, 7) + " Ab");

    expectFormatted("utf16/six_supplementary_last",
                    "Ab " + repeat(emoji, 6),
                    "Ab " + repeat(emoji, 6));
    expectRejected("utf16/seven_supplementary_last",
                   "Ab " + repeat(emoji, 7));
}

void runMalformedTests()
{
    std::string const truncated("\xC3", 1);
    std::string const isolated("\x80", 1);
    std::string const overlong("\xC0\xAF", 2);
    std::string const surrogate("\xED\xA0\x80", 3);
    std::string const outOfRange("\xF4\x90\x80\x80", 4);

    expectRejected("malformed/truncated_first", malformedFirst(truncated));
    expectRejected("malformed/truncated_last", malformedLast(truncated));
    expectRejected("malformed/isolated_first", malformedFirst(isolated));
    expectRejected("malformed/isolated_last", malformedLast(isolated));
    expectRejected("malformed/overlong_first", malformedFirst(overlong));
    expectRejected("malformed/overlong_last", malformedLast(overlong));
    expectRejected("malformed/surrogate_first", malformedFirst(surrogate));
    expectRejected("malformed/surrogate_last", malformedLast(surrogate));
    expectRejected("malformed/out_of_range_first", malformedFirst(outOfRange));
    expectRejected("malformed/out_of_range_last", malformedLast(outOfRange));
}

void runControlAndWhitespaceTests()
{
    std::string nulFirst = "As";
    nulFirst.push_back('\0');
    nulFirst += "ha Brightvale";
    expectRejected("controls/nul_first", nulFirst);

    std::string nulLast = "Asha Bright";
    nulLast.push_back('\0');
    nulLast += "vale";
    expectRejected("controls/nul_last", nulLast);

    expectRejected("controls/tab_first", malformedFirst(std::string("\t")));
    expectRejected("controls/tab_last", insideLast(std::string("\t")));
    expectRejected("controls/cr_last", insideLast(std::string("\r")));
    expectRejected("controls/lf_last", insideLast(std::string("\n")));
    expectRejected("controls/c0_unit_separator_last", insideLast(std::string(1, '\x1F')));
    expectRejected("controls/delete_last", insideLast(std::string(1, '\x7F')));
    expectRejected("controls/c1_last", insideLast(std::string("\xC2\x9F", 2)));

    for (unsigned int scalar = 0x2000; scalar <= 0x200A; ++scalar)
    {
        std::string whitespace;
        whitespace.push_back(static_cast<char>(0xE2));
        whitespace.push_back(static_cast<char>(0x80));
        whitespace.push_back(static_cast<char>(0x80 + (scalar - 0x2000)));
        std::string label = "controls/unicode_2000_range_";
        label += static_cast<char>('0' + (scalar - 0x2000));
        expectRejected(label.c_str(), insideLast(whitespace));
    }

    char const* const whitespace[] =
    {
        "\xC2\xA0",         // U+00A0 NO-BREAK SPACE
        "\xE1\x9A\x80",     // U+1680 OGHAM SPACE MARK
        "\xE2\x80\x80",     // U+2000 EN QUAD
        "\xE2\x80\x8A",     // U+200A HAIR SPACE
        "\xE2\x80\xA8",     // U+2028 LINE SEPARATOR
        "\xE2\x80\xA9",     // U+2029 PARAGRAPH SEPARATOR
        "\xE2\x80\xAF",     // U+202F NARROW NO-BREAK SPACE
        "\xE2\x81\x9F",     // U+205F MEDIUM MATHEMATICAL SPACE
        "\xE3\x80\x80"      // U+3000 IDEOGRAPHIC SPACE
    };

    for (std::size_t i = 0; i < sizeof(whitespace) / sizeof(whitespace[0]); ++i)
    {
        std::string label = "controls/unicode_whitespace_last_";
        label += static_cast<char>('0' + i);
        expectRejected(label.c_str(), insideLast(whitespace[i]));

        label = "controls/unicode_whitespace_first_";
        label += static_cast<char>('0' + i);
        std::string input = "As";
        input += whitespace[i];
        input += "ha Brightvale";
        expectRejected(label.c_str(), input);
    }
}

void runFailurePreservationTests()
{
    std::string lateMalformed = "aSHA bRIGH";
    lateMalformed += "\xC3";
    expectRejected("preservation/late_malformed", lateMalformed);

    std::string lateWhitespace = "aSHA bRIGH";
    lateWhitespace += "\xC2\xA0";
    lateWhitespace += "T";
    expectRejected("preservation/late_nbsp", lateWhitespace);

    std::string lateNul = "aSHA bRIGH";
    lateNul.push_back('\0');
    lateNul += "T";
    expectRejected("preservation/late_nul", lateNul);

    std::string lateSpace = "aSHA bRIGH T";
    expectRejected("preservation/late_extra_space", lateSpace);
}

void runIdempotenceAndStatelessTests()
{
    std::string const original = u8"\u00DFETA \u00FFARD";
    std::string const expected = u8"\u1E9Eeta \u0178ard";
    expectFormatted("idempotence/first_call", original, expected);

    std::string again = expected;
    ++checks;
    bool const returned = normalizeFullPlayerName(again);
    if (!returned || again != expected)
        fail("idempotence/second_call", original, expected, again, returned);

    std::string first = "aSHA brightVALE";
    std::string second = "zED yON";
    expectFormatted("stateless/first_input", first, "Asha Brightvale");
    expectFormatted("stateless/second_input", second, "Zed Yon");
    expectFormatted("stateless/first_again", first, "Asha Brightvale");
}

void runAdmissionBoundaryTests()
{
    // The formatter deliberately does not enforce the later naming policy.
    // These inputs are structurally valid here; the eventual validator rejects
    // one-letter parts, digits, and other policy failures.
    expectFormatted("admission/one_letter_pair", "A B", "A B");
    expectFormatted("admission/digits_format", "A1 B2", "A1 B2");
    expectFormatted("admission/digits_only", "1 2", "1 2");
}

struct TestLists : PlayerNameLists
{
    std::vector<std::string> namesProfanity;
    std::vector<std::string> chatProfanity;
    std::vector<std::string> namesReserved;

    static bool contains(std::vector<std::string> const& words, std::string const& name)
    {
        std::string folded = name;
        for (std::size_t i = 0; i < folded.size(); ++i)
        {
            char const c = folded[i];
            if (c >= 'A' && c <= 'Z')
                folded[i] = static_cast<char>(c - 'A' + 'a');
        }

        for (std::size_t i = 0; i < words.size(); ++i)
            if (folded.find(words[i]) != std::string::npos)
                return true;
        return false;
    }

    bool NamesProfanity(std::string const& name) const override
    {
        return contains(namesProfanity, name);
    }

    bool ChatProfanity(std::string const& name) const override
    {
        return contains(chatProfanity, name);
    }

    bool NamesReserved(std::string const& name) const override
    {
        return contains(namesReserved, name);
    }
};

TestLists makeLists(std::initializer_list<char const*> names = {},
                    std::initializer_list<char const*> chat = {},
                    std::initializer_list<char const*> reserved = {})
{
    TestLists lists;
    for (char const* word : names)
        lists.namesProfanity.push_back(word);
    for (char const* word : chat)
        lists.chatProfanity.push_back(word);
    for (char const* word : reserved)
        lists.namesReserved.push_back(word);
    return lists;
}

std::string utf8Bmp(std::uint32_t cp)
{
    std::string result;
    if (cp <= 0x7F)
    {
        result.push_back(static_cast<char>(cp));
    }
    else if (cp <= 0x7FF)
    {
        result.push_back(static_cast<char>(0xC0 | (cp >> 6)));
        result.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    }
    else
    {
        result.push_back(static_cast<char>(0xE0 | (cp >> 12)));
        result.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
        result.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    }
    return result;
}

void expectStatus(char const* label, std::uint8_t actual, std::uint8_t expected)
{
    ++checks;
    if (actual != expected)
    {
        ++failures;
        std::cerr << "FAIL " << label << '\n'
                  << "  expected status: " << static_cast<int>(expected) << '\n'
                  << "  actual status:   " << static_cast<int>(actual) << '\n';
    }
}

void expectBool(char const* label, bool actual, bool expected)
{
    ++checks;
    if (actual != expected)
    {
        ++failures;
        std::cerr << "FAIL " << label << '\n'
                  << "  expected: " << (expected ? "true" : "false") << '\n'
                  << "  actual:   " << (actual ? "true" : "false") << '\n';
    }
}

void expectCheck(char const* label, std::string const& name,
                 PlayerNameLists const& lists, PlayerNameCharsets charsets,
                 std::uint8_t expected)
{
    expectStatus(label, checkFullPlayerName(name, lists, charsets), expected);
}

void expectCheck(char const* label, std::string const& name, std::uint8_t expected)
{
    TestLists lists;
    expectCheck(label, name, lists, PlayerNameCharsets::ForClient(0, 0), expected);
}

void expectCheckOn(char const* label, std::int32_t region, std::size_t locale,
                   std::string const& name, std::uint8_t expected)
{
    TestLists lists;
    expectCheck(label, name, lists, PlayerNameCharsets::ForClient(region, locale), expected);
}

std::uint8_t checkFull(std::string const& name, PlayerNameLists const& lists,
                       PlayerNameCharsets charsets)
{
    return checkFullPlayerName(name, lists, charsets);
}

std::uint8_t checkFullOn(std::int32_t region, std::size_t locale, std::string const& name)
{
    TestLists lists;
    return checkFullPlayerName(name, lists, PlayerNameCharsets::ForClient(region, locale));
}

void expectCharset(char const* label, std::int32_t region, std::size_t locale,
                   std::uint8_t primary, std::uint8_t secondary)
{
    ++checks;
    PlayerNameCharsets const actual = PlayerNameCharsets::ForClient(region, locale);
    if (actual.primary != primary || actual.secondary != secondary)
    {
        ++failures;
        std::cerr << "FAIL " << label << '\n'
                  << "  expected: (" << static_cast<int>(primary) << ", "
                  << static_cast<int>(secondary) << ")\n"
                  << "  actual:   (" << static_cast<int>(actual.primary) << ", "
                  << static_cast<int>(actual.secondary) << ")\n";
    }
}

void expectPrepared(char const* label, std::string input, PlayerNameLists const& lists,
                    PlayerNameCharsets charsets, std::uint8_t expected_status,
                    std::string const& expected_text)
{
    ++checks;
    std::string const original = input;
    std::uint8_t const status = prepareFullPlayerName(input, lists, charsets);
    bool const ok = status == expected_status &&
        (expected_status == 0x52 ? input == expected_text : input == original);
    if (!ok)
    {
        ++failures;
        std::cerr << "FAIL " << label << '\n'
                  << "  expected status: " << static_cast<int>(expected_status) << '\n'
                  << "  actual status:   " << static_cast<int>(status) << '\n'
                  << "  input:           " << hexEncode(original) << '\n'
                  << "  expected:        " << hexEncode(expected_text) << '\n'
                  << "  actual:          " << hexEncode(input) << '\n';
    }
}

void expectPreparedOk(char const* label, std::string input, std::string const& expected)
{
    TestLists lists;
    expectPrepared(label, input, lists, PlayerNameCharsets::ForClient(0, 0), 0x52, expected);
}

void expectPreparedErr(char const* label, std::string input, std::uint8_t expected_status)
{
    TestLists lists;
    expectPrepared(label, input, lists, PlayerNameCharsets::ForClient(0, 0),
                   expected_status, input);
}

void expectPreparedOn(char const* label, std::string input, std::int32_t region,
                      std::size_t locale, std::uint8_t expected_status,
                      std::string const& expected_text)
{
    TestLists lists;
    expectPrepared(label, input, lists, PlayerNameCharsets::ForClient(region, locale),
                   expected_status, expected_text);
}

void runCharsetProfileTests()
{
    std::uint8_t const regions[5][2] = {{1, 1}, {2, 4}, {1, 1}, {3, 1}, {3, 1}};
    for (int region = 1; region <= 5; ++region)
        for (std::size_t locale = 0; locale <= 8; ++locale)
            expectCharset("profile/region_pair", region, locale,
                          regions[region - 1][0], regions[region - 1][1]);

    std::uint8_t const locales[8] = {1, 2, 1, 1, 3, 3, 1, 0};
    for (std::size_t locale = 0; locale <= 6; ++locale)
        expectCharset("profile/locale_pair", 0, locale, locales[locale],
                      locale == 1 ? 4 : 1);

    expectCharset("profile/locale_7_fallback", 0, 7, 1, 1);
    expectCharset("profile/locale_8_fallback", 0, 8, 1, 1);
    expectCharset("profile/locale_max_fallback", 0,
                  std::numeric_limits<std::size_t>::max(), 1, 1);

    std::int32_t const otherRegions[] = {
        -1, 0, 6, 1000,
        std::numeric_limits<std::int32_t>::min(),
        std::numeric_limits<std::int32_t>::max()
    };
    for (std::size_t i = 0; i < sizeof(otherRegions) / sizeof(otherRegions[0]); ++i)
    {
        expectCharset("profile/other_region_ko", otherRegions[i], 1, 2, 4);
        expectCharset("profile/other_region_zh", otherRegions[i], 4, 3, 1);
        expectCharset("profile/other_region_fallback", otherRegions[i], 7, 1, 1);
    }

    TestLists lists;
    expectCheck("profile/kokr_ascii_eight", "가나다라마바사아 Abcdefgh", lists,
                PlayerNameCharsets::ForClient(0, 1), 0x52);
    expectCheck("profile/kokr_ascii_nine", "가나다라마바사아 Abcdefghi", lists,
                PlayerNameCharsets::ForClient(0, 1), 0x47);
    expectCheck("profile/zhcn_latin_twelve", "Abcdefghijkl 李小龙", lists,
                PlayerNameCharsets::ForClient(0, 4), 0x52);
    expectCheck("profile/zhcn_latin_thirteen", "Abcdefghijklm 李小龙", lists,
                PlayerNameCharsets::ForClient(0, 4), 0x47);
}

void runRawAdmissionTests()
{
    expectCheck("raw/normal", "Asha Brightvale", 0x52);
    expectCheck("raw/max_latin", "Abcdefghijkl Mnopqrstuvwx", 0x52);
    expectCheck("raw/boundary_pair", "Abb Bc", 0x52);
    {
        TestLists restricted = makeLists({}, {}, {"blizz"});
        expectCheck("raw/restricted_status", "Blizz Brightvale", restricted,
                    PlayerNameCharsets::ForClient(0, 0), 0x4b);
    }

    expectCheck("raw/empty", "", 0x45);
    expectCheck("raw/no_separator", "Asha", 0x48);
    expectCheck("raw/empty_last", "Asha ", 0x45);
    expectCheck("raw/empty_first", " Brightvale", 0x45);
    expectCheck("raw/only_space", " ", 0x45);
    expectCheck("raw/doubled", "Asha  Brightvale", 0x48);
    expectCheck("raw/third_component", "Asha Bright vale", 0x48);
    expectCheck("raw/fourth_component", "Asha Brightvale Extra", 0x48);
    expectCheck("raw/tab_separator", "Asha\tBrightvale", 0x48);
    expectCheck("raw/nbsp_separator", "Asha\xC2\xA0" "Brightvale", 0x48);
    expectCheck("raw/nbsp_inside", "Asha Bright\xC2\xA0vale", 0x48);
    expectCheck("raw/punctuation", "As-ha Brightvale", 0x48);
    expectCheck("raw/digit", "As1a Brightvale", 0x48);

    expectCheck("raw/short_first", "A Brightvale", 0x46);
    expectCheck("raw/short_last", "Asha B", 0x46);
    expectCheck("raw/triple_first", "Aaab Brightvale", 0x4e);
    expectCheck("raw/triple_last", "Asha Bbbright", 0x4e);
    expectCheck("raw/long_first", "Abcdefghijklm Brightvale", 0x47);
    expectCheck("raw/long_last", "Asha Abcdefghijklm", 0x47);
    expectCheck("raw/invalid_before_cap", "Abcdefghijkl1 Brightvale", 0x48);
    expectCheck("raw/triple_before_digit", "Aaab1 Brightvale", 0x4e);
    expectCheck("raw/digit_before_triple", "A1aaa Brightvale", 0x48);

    expectCheck("raw/first_before_last", "A 1", 0x46);
    expectCheck("raw/triple_before_last", "Aaab 1", 0x4e);
    expectCheck("raw/invalid_before_last", "A1aaa 1", 0x48);
    expectCheck("raw/first_before_malformed_last", "A \xC3", 0x46);
    expectCheck("raw/triple_before_malformed_last", "Aaab \xC3", 0x4e);
    expectCheck("raw/invalid_before_malformed_last", "A1aaa \xC3", 0x48);
    expectCheck("raw/malformed_first", "\xC3 Brightvale", 0x48);
    expectCheck("raw/malformed_last", "Asha \xC3", 0x48);
    expectCheck("raw/supplementary", "\xF0\x9F\x98\x80 Brightvale", 0x48);

    expectCheck("raw/ascii_triple_fold", "aAa Brightvale", 0x4e);
    expectCheck("raw/accented_no_fold", u8"\u00E9\u00C9\u00E9 Brightvale", 0x52);
    expectCheck("raw/accented_triple", u8"\u00E9\u00E9\u00E9 Brightvale", 0x4e);
    expectCheck("raw/accented_mixed", u8"\u00C9\u00E9\u00C9 Brightvale", 0x52);

    std::string nulFirst = "As";
    nulFirst.push_back('\0');
    nulFirst += "ha Brightvale";
    expectCheck("raw/nul_first", nulFirst, 0x48);

    std::string nulLast = "Asha Bright";
    nulLast.push_back('\0');
    nulLast += "vale";
    expectCheck("raw/nul_last", nulLast, 0x48);

    std::string digits1024(1024, '1');
    digits1024 += " Brightvale";
    expectCheck("raw/1024_units", digits1024, 0x51);

    std::string digits1023(1023, '1');
    digits1023 += " Brightvale";
    expectCheck("raw/1023_units", digits1023, 0x48);
}

void runCharsetMembershipTests()
{
    TestLists lists;
    PlayerNameCharsets const latin = PlayerNameCharsets::ForClient(0, 0);

    expectCheck("membership/latin_ascii", "Aa Brightvale", lists, latin, 0x52);
    expectCheck("membership/latin_agrave", u8"\u00C0a Brightvale", lists, latin, 0x52);
    expectCheck("membership/latin_odieresis", u8"\u00D6a Brightvale", lists, latin, 0x52);
    expectCheck("membership/latin_oslash", u8"\u00D8a Brightvale", lists, latin, 0x52);
    expectCheck("membership/latin_eszett", u8"\u00DFa Brightvale", lists, latin, 0x52);
    expectCheck("membership/latin_thorn", u8"\u00FEa Brightvale", lists, latin, 0x52);
    expectCheck("membership/latin_ydiaeresis", u8"\u00FFa Brightvale", lists, latin, 0x52);
    expectCheck("membership/latin_ymacron", u8"a\u00FF Brightvale", lists, latin, 0x52);

    expectCheck("membership/latin_times", u8"a\u00D7 Brightvale", lists, latin, 0x48);
    expectCheck("membership/latin_thorn_upper", u8"a\u00DE Brightvale", lists, latin, 0x48);
    expectCheck("membership/latin_divide", u8"a\u00F7 Brightvale", lists, latin, 0x48);
    expectCheck("membership/latin_yupper", u8"a\u0178 Brightvale", lists, latin, 0x48);
    expectCheck("membership/latin_eszett_upper", u8"a\u1E9E Brightvale", lists, latin, 0x48);
    expectCheck("membership/latin_cyrillic", u8"a\u0410 Brightvale", lists, latin, 0x48);
    expectCheck("membership/latin_thorn_initial", u8"\u00DEor Brightvale", lists, latin, 0x48);
    expectCheck("membership/latin_eszett_initial", u8"\u1E9Eeta Brightvale", lists, latin, 0x48);
    expectCheck("membership/latin_yupper_initial", u8"\u0178ard Brightvale", lists, latin, 0x48);

    expectCheckOn("membership/kokr_eight_hangul", 0, 1,
                  "가나다라마바사아 Abcdefgh", 0x52);
    expectCheckOn("membership/kokr_nine_hangul", 0, 1,
                  "가나다라마바사아자 Abcdefgh", 0x47);
    expectCheckOn("membership/kokr_nine_ascii", 0, 1,
                  "가나다라마바사아 Abcdefghi", 0x47);
    expectCheckOn("membership/kokr_nine_hangul_last", 0, 1,
                  "Abcdefgh 가나다라마바사아자", 0x47);
    expectCheckOn("membership/kokr_unlisted_ac02", 0, 1,
                  u8"\uAC02\uAC00 Abcdefgh", 0x48);
    expectCheckOn("membership/kokr_unlisted_d79e", 0, 1,
                  u8"\uD79E\uAC00 Abcdefgh", 0x48);
    expectCheckOn("membership/kokr_block_last_d7a3", 0, 1,
                  u8"\uD7A3\uAC00 Abcdefgh", 0x48);
    expectCheckOn("membership/kokr_listed_ac00", 0, 1,
                  u8"\uAC00\uAC00 Abcdefgh", 0x52);
    expectCheckOn("membership/kokr_listed_d79d", 0, 1,
                  u8"\uD79D\uAC00 Abcdefgh", 0x52);

    int admitted = 0;
    int wrong = 0;
    for (std::uint32_t cp = 0xAC00; cp <= 0xD7A3; ++cp)
    {
        std::string const name = utf8Bmp(cp) + utf8Bmp(0xAC00) + " Ab";
        std::uint8_t const status = checkFullOn(0, 1, name);
        if (status == 0x52)
            ++admitted;
        else if (status != 0x48)
            ++wrong;
    }
    ++checks;
    if (admitted != 2350 || wrong != 0)
    {
        ++failures;
        std::cerr << "FAIL membership/kokr_all_scalars: admitted=" << admitted
                  << " wrong_status=" << wrong << '\n';
    }

    expectCheckOn("membership/zhcn_cjk_3400", 0, 4,
                  utf8Bmp(0x3400) + utf8Bmp(0x4E00) + " Ab", 0x52);
    expectCheckOn("membership/zhcn_cjk_4dff", 0, 4,
                  utf8Bmp(0x4DFF) + utf8Bmp(0x4E00) + " Ab", 0x52);
    expectCheckOn("membership/zhcn_cjk_4e00", 0, 4,
                  utf8Bmp(0x4E00) + utf8Bmp(0x4E00) + " Ab", 0x52);
    expectCheckOn("membership/zhcn_cjk_9fff", 0, 4,
                  utf8Bmp(0x9FFF) + utf8Bmp(0x4E00) + " Ab", 0x52);
    expectCheckOn("membership/zhcn_cjk_f900", 0, 4,
                  utf8Bmp(0xF900) + utf8Bmp(0x4E00) + " Ab", 0x52);
    expectCheckOn("membership/zhcn_cjk_faff", 0, 4,
                  utf8Bmp(0xFAFF) + utf8Bmp(0x4E00) + " Ab", 0x52);
    expectCheckOn("membership/zhcn_cjk_33ff", 0, 4,
                  utf8Bmp(0x33FF) + utf8Bmp(0x4E00) + " Ab", 0x48);
    expectCheckOn("membership/zhcn_cjk_a000", 0, 4,
                  utf8Bmp(0xA000) + utf8Bmp(0x4E00) + " Ab", 0x48);
    expectCheckOn("membership/zhcn_cjk_f8ff", 0, 4,
                  utf8Bmp(0xF8FF) + utf8Bmp(0x4E00) + " Ab", 0x48);
    expectCheckOn("membership/zhcn_cjk_fb00", 0, 4,
                  utf8Bmp(0xFB00) + utf8Bmp(0x4E00) + " Ab", 0x48);

    expectCheckOn("membership/zhcn_six_cjk", 0, 4, "一二三四五六 Ab", 0x52);
    expectCheckOn("membership/zhcn_seven_cjk", 0, 4, "一二三四五六七 Ab", 0x47);
    expectCheckOn("membership/zhcn_latin_secondary_12", 0, 4,
                  "Abcdefghijkl 李小龙", 0x52);
    expectCheckOn("membership/zhcn_latin_secondary_13", 0, 4,
                  "Abcdefghijklm 李小龙", 0x47);
    expectCheckOn("membership/kokr_mixed_primary_secondary", 0, 1,
                  "Abcdefgh 가나다라", 0x52);
    expectCheckOn("membership/zhcn_mixed_primary_secondary", 0, 4,
                  "Abcdefgh 李小龙", 0x52);
    expectCheckOn("membership/enus_rejects_hangul", 0, 0,
                  "가나다라마바사아 Abcdefgh", 0x48);
    expectCheck("membership/cyrillic_rejected", u8"\u0410\u0431\u0432 Brightvale",
                lists, latin, 0x48);
    expectCheck("membership/unknown_charset", "Aa Brightvale", lists,
                PlayerNameCharsets{9, 9}, 0x48);
}

void runRestrictedListTests()
{
    PlayerNameCharsets const latin = PlayerNameCharsets::ForClient(0, 0);
    TestLists const namesBlizz = makeLists({"blizz"});
    TestLists const chatBlizz = makeLists({}, {"blizz"});
    TestLists const reservedBlizz = makeLists({}, {}, {"blizz"});
    TestLists const* const blizzLists[] = {&namesBlizz, &chatBlizz, &reservedBlizz};
    for (std::size_t i = 0; i < 3; ++i)
    {
        expectCheck("restricted/first_part", "Blizz Brightvale", *blizzLists[i], latin, 0x4b);
        expectCheck("restricted/last_part", "Asha Blizz", *blizzLists[i], latin, 0x4b);
    }

    TestLists const namesSpaced = makeLists({"asha bright"});
    TestLists const chatSpaced = makeLists({}, {"asha bright"});
    TestLists const reservedSpaced = makeLists({}, {}, {"asha bright"});
    TestLists const* const spacedLists[] = {&namesSpaced, &chatSpaced, &reservedSpaced};
    for (std::size_t i = 0; i < 3; ++i)
        expectCheck("restricted/spaced_full", "Asha Brightvale", *spacedLists[i], latin, 0x4b);

    TestLists const namesJoined = makeLists({"ashabright"});
    TestLists const chatJoined = makeLists({}, {"ashabright"});
    TestLists const reservedJoined = makeLists({}, {}, {"ashabright"});
    TestLists const* const joinedLists[] = {&namesJoined, &chatJoined, &reservedJoined};
    for (std::size_t i = 0; i < 3; ++i)
        expectCheck("restricted/joined_full", "Asha Brightvale", *joinedLists[i], latin, 0x4b);

    for (std::size_t i = 0; i < 3; ++i)
        expectCheck("restricted/bl_izza", "Bl Izza", *blizzLists[i], latin, 0x4b);

    TestLists empty;
    expectCheck("restricted/control", "Asha Brightvale", empty, latin, 0x52);
    expectCheck("restricted/control_boundary", "Abb Bc", empty, latin, 0x52);
    expectCheck("restricted/control_combined_length", "Abcdefghijkl Mnopqrstuvwx",
                empty, latin, 0x52);

    TestLists const accentSpaced = makeLists({}, {}, {"Éclair brightvale"});
    expectCheck("restricted/accent_raw", "éclair brightVALE", accentSpaced, latin, 0x52);
    expectCheck("restricted/accent_canonical", "Éclair Brightvale", accentSpaced, latin, 0x4b);
    TestLists const accentJoined = makeLists({}, {}, {"Éclairbrightvale"});
    expectCheck("restricted/accent_joined_canonical", "Éclair Brightvale",
                accentJoined, latin, 0x4b);
}

void runPreparedAdmissionTests()
{
    PlayerNameCharsets const latin = PlayerNameCharsets::ForClient(0, 0);
    expectPreparedOk("prepared/ascii", "aSHA brightVALE", "Asha Brightvale");
    expectPreparedOk("prepared/accent", u8"\u00E9clair bravo", u8"\u00C9clair Bravo");
    expectPreparedOk("prepared/max_latin", "Abcdefghijkl Mnopqrstuvwx",
                     "Abcdefghijkl Mnopqrstuvwx");

    char const* const rawInitials[] = {
        u8"\u00DFeta Brightvale", u8"\u00FEor Brightvale", u8"\u00FFard Brightvale",
        u8"Asha \u00DFeta", u8"Asha \u00FEor", u8"Asha \u00FFard"
    };
    for (std::size_t i = 0; i < sizeof(rawInitials) / sizeof(rawInitials[0]); ++i)
    {
        expectCheck("prepared/raw_initial", rawInitials[i], 0x52);
        expectPreparedErr("prepared/canonical_initial", rawInitials[i], 0x48);
    }

    expectPreparedOk("prepared/internal_eszett_thorn",
                     u8"a\u00DFa a\u00FEa", u8"A\u00DFa A\u00FEa");
    expectPreparedOk("prepared/internal_y",
                     u8"a\u00FFa brightvale", u8"A\u00FFa Brightvale");
    expectPreparedErr("prepared/raw_short_wins", u8"\u00DFeta A", 0x46);

    expectCheck("prepared/raw_repetition", u8"\u00E9\u00C9\u00E9\u00C9 Vale", 0x52);
    expectPreparedErr("prepared/canonical_repetition",
                      u8"\u00E9\u00C9\u00E9\u00C9 Vale", 0x4e);
    expectPreparedOk("prepared/repetition_control",
                     u8"\u00E9\u00C9\u00E9 Vale", u8"\u00C9\u00E9\u00E9 Vale");

    TestLists const namesSpaced = makeLists({"Éclair brightvale"});
    expectBool("fixture/names_spaced_profanity_canonical",
               namesSpaced.NamesProfanity("Éclair Brightvale"), true);
    expectBool("fixture/names_spaced_chat_canonical",
               namesSpaced.ChatProfanity("Éclair Brightvale"), false);
    expectBool("fixture/names_spaced_reserved_canonical",
               namesSpaced.NamesReserved("Éclair Brightvale"), false);
    expectBool("fixture/names_spaced_profanity_raw",
               namesSpaced.NamesProfanity("éclair brightVALE"), false);
    expectCheck("prepared/list_spaced_raw", "éclair brightVALE", namesSpaced, latin, 0x52);
    expectPrepared("prepared/list_spaced_canonical", "éclair brightVALE", namesSpaced,
                   latin, 0x4b, "éclair brightVALE");
    TestLists const namesJoined = makeLists({"Éclairbrightvale"});
    expectBool("fixture/names_joined_profanity_canonical",
               namesJoined.NamesProfanity("ÉclairBrightvale"), true);
    expectBool("fixture/names_joined_chat_canonical",
               namesJoined.ChatProfanity("ÉclairBrightvale"), false);
    expectBool("fixture/names_joined_reserved_canonical",
               namesJoined.NamesReserved("ÉclairBrightvale"), false);
    expectBool("fixture/names_joined_profanity_raw",
               namesJoined.NamesProfanity("éclairbrightVALE"), false);
    expectBool("fixture/names_joined_spaced_not_matching",
               namesJoined.NamesProfanity("Éclair Brightvale"), false);
    expectCheck("prepared/list_joined_raw", "éclair brightVALE", namesJoined, latin, 0x52);
    expectPrepared("prepared/list_joined_canonical", "éclair brightVALE", namesJoined,
                   latin, 0x4b, "éclair brightVALE");

    TestLists const chatSpaced = makeLists({}, {"Éclair brightvale"});
    expectCheck("prepared/chat_spaced_raw", "éclair brightVALE", chatSpaced, latin, 0x52);
    expectPrepared("prepared/chat_spaced_canonical", "éclair brightVALE", chatSpaced,
                   latin, 0x4b, "éclair brightVALE");
    TestLists const reservedSpaced = makeLists({}, {}, {"Éclair brightvale"});
    expectCheck("prepared/reserved_spaced_raw", "éclair brightVALE",
                reservedSpaced, latin, 0x52);
    expectPrepared("prepared/reserved_spaced_canonical", "éclair brightVALE",
                   reservedSpaced, latin, 0x4b, "éclair brightVALE");
    TestLists const chatJoined = makeLists({}, {"Éclairbrightvale"});
    expectCheck("prepared/chat_joined_raw", "éclair brightVALE", chatJoined, latin, 0x52);
    expectPrepared("prepared/chat_joined_canonical", "éclair brightVALE", chatJoined,
                   latin, 0x4b, "éclair brightVALE");
    TestLists const reservedJoined = makeLists({}, {}, {"Éclairbrightvale"});
    expectCheck("prepared/reserved_joined_raw", "éclair brightVALE",
                reservedJoined, latin, 0x52);
    expectPrepared("prepared/reserved_joined_canonical", "éclair brightVALE",
                   reservedJoined, latin, 0x4b, "éclair brightVALE");

    expectPreparedOk("prepared/canonical_idempotent", "Asha Brightvale", "Asha Brightvale");
    expectPreparedOk("prepared/idempotent_first", "aSHA brightVALE", "Asha Brightvale");
    expectPreparedOk("prepared/idempotent_second", "Asha Brightvale", "Asha Brightvale");
    expectPreparedOn("prepared/zhcn_secondary_12", "Abcdefghijkl 李小龙", 0, 4, 0x52,
                     "Abcdefghijkl 李小龙");
    expectPreparedOn("prepared/kokr_eight", "가나다라마바사아 Abcdefgh", 0, 1, 0x52,
                     "가나다라마바사아 Abcdefgh");
    expectPreparedOn("prepared/kokr_nine", "가나다라마바사아자 Abcdefgh", 0, 1, 0x47,
                     "가나다라마바사아자 Abcdefgh");
    expectPreparedOn("prepared/zhcn_six", "一二三四五六 李小龙歐陽修", 0, 4, 0x52,
                     "一二三四五六 李小龙歐陽修");
    expectPreparedOn("prepared/zhcn_seven", "一二三四五六七 李小龙歐陽修", 0, 4, 0x47,
                     "一二三四五六七 李小龙歐陽修");
    expectPreparedOn("prepared/enus_hangul", "가나다라마바사아 Abcdefgh", 0, 0, 0x48,
                     "가나다라마바사아 Abcdefgh");

    expectPreparedErr("prepared/empty", "", 0x45);
    expectPreparedErr("prepared/no_separator", "Asha", 0x48);
    expectPreparedErr("prepared/short", "A Brightvale", 0x46);
    expectPreparedErr("prepared/long", "Abcdefghijklm Brightvale", 0x47);
    expectPreparedErr("prepared/long_last", "Asha Abcdefghijklm", 0x47);
    expectPreparedErr("prepared/triple", "Aaab Brightvale", 0x4e);
    expectPreparedErr("prepared/malformed_first", "\xC3 Brightvale", 0x48);
    expectPreparedErr("prepared/malformed_last", "Asha \xC3", 0x48);
}
}

int main()
{
    runShapeTests();
    runCaseTests();
    runUncasedTests();
    runCapacityTests();
    runUtf16UnitTests();
    runMalformedTests();
    runControlAndWhitespaceTests();
    runFailurePreservationTests();
    runIdempotenceAndStatelessTests();
    runAdmissionBoundaryTests();
    runCharsetProfileTests();
    runRawAdmissionTests();
    runCharsetMembershipTests();
    runRestrictedListTests();
    runPreparedAdmissionTests();

    std::cout << "Executed " << checks << " checks; " << failures << " failure(s)\n";
    return failures == 0 ? 0 : 1;
}
