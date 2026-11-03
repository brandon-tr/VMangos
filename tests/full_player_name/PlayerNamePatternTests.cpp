#include "PlayerNamePattern.h"

#include <chrono>
#include <cctype>
#include <cstddef>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace
{
int checks = 0;
int failures = 0;

void fail(std::string const& label, std::string const& message)
{
    ++failures;
    std::cerr << "FAIL " << label << ": " << message << '\n';
}

std::vector<std::string> one(std::string pattern)
{
    return std::vector<std::string>{pattern};
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

void expectMatch(std::string const& label,
                 std::string const& pattern,
                 std::string const& subject,
                 bool expected)
{
    ++checks;
    try
    {
        PlayerNamePatternList list(one(pattern));
        bool const actual = list.Matches(subject);
        if (actual != expected)
        {
            fail(label, std::string("expected ") + (expected ? "true" : "false") +
                       ", actual " + (actual ? "true" : "false"));
        }
    }
    catch (std::exception const& e)
    {
        fail(label, std::string("threw: ") + e.what());
    }
}

void expectAnyMatch(std::string const& label,
                    std::vector<std::string> const& patterns,
                    std::string const& subject,
                    bool expected)
{
    ++checks;
    try
    {
        PlayerNamePatternList list(patterns);
        bool const actual = list.Matches(subject);
        if (actual != expected)
            fail(label, std::string("expected ") + (expected ? "true" : "false") +
                       ", actual " + (actual ? "true" : "false"));
    }
    catch (std::exception const& e)
    {
        fail(label, std::string("threw: ") + e.what());
    }
}

void expectPatternRejected(std::string const& label,
                           std::string const& pattern,
                           std::string const& reason)
{
    ++checks;
    try
    {
        PlayerNamePatternList list(one(pattern));
        (void)list;
        fail(label, "constructor accepted an unsupported pattern");
    }
    catch (std::invalid_argument const& e)
    {
        std::string const message = e.what();
        if (message.find("pattern 0") == std::string::npos ||
            message.find(reason) == std::string::npos)
        {
            fail(label, std::string("unexpected diagnostic: ") + message);
        }
    }
    catch (std::exception const& e)
    {
        fail(label, std::string("wrong exception: ") + e.what());
    }
}

void expectListRejected(std::string const& label,
                        std::vector<std::string> const& patterns,
                        std::string const& reason)
{
    ++checks;
    try
    {
        PlayerNamePatternList list(patterns);
        (void)list;
        fail(label, "constructor accepted an invalid list");
    }
    catch (std::invalid_argument const& e)
    {
        std::string const message = e.what();
        if (message.find(reason) == std::string::npos)
            fail(label, std::string("unexpected diagnostic: ") + message);
    }
    catch (std::exception const& e)
    {
        fail(label, std::string("wrong exception: ") + e.what());
    }
}

void expectSubjectRejected(std::string const& label,
                           std::string const& subject,
                           std::string const& reason)
{
    ++checks;
    try
    {
        PlayerNamePatternList list(one("a*"));
        bool const actual = list.Matches(subject);
        fail(label, std::string("subject accepted with result ") + (actual ? "true" : "false"));
    }
    catch (std::invalid_argument const& e)
    {
        if (std::string(e.what()).find(reason) == std::string::npos)
            fail(label, std::string("unexpected diagnostic: ") + e.what());
    }
    catch (std::exception const& e)
    {
        fail(label, std::string("wrong exception: ") + e.what());
    }
}

void expectSubjectTooLong(std::string const& label, std::string const& subject)
{
    ++checks;
    try
    {
        PlayerNamePatternList list(one("a*"));
        bool const actual = list.Matches(subject);
        fail(label, std::string("subject accepted with result ") + (actual ? "true" : "false"));
    }
    catch (std::length_error const&)
    {
    }
    catch (std::exception const& e)
    {
        fail(label, std::string("wrong exception: ") + e.what());
    }
}

bool hexNibble(char c, unsigned& value)
{
    if (c >= '0' && c <= '9') { value = static_cast<unsigned>(c - '0'); return true; }
    if (c >= 'a' && c <= 'f') { value = static_cast<unsigned>(c - 'a' + 10); return true; }
    if (c >= 'A' && c <= 'F') { value = static_cast<unsigned>(c - 'A' + 10); return true; }
    return false;
}

bool hexDecode(std::string const& hex, std::string& out)
{
    if (hex.size() % 2 != 0)
        return false;
    out.clear();
    out.reserve(hex.size() / 2);
    for (std::size_t i = 0; i < hex.size(); i += 2)
    {
        unsigned hi = 0, lo = 0;
        if (!hexNibble(hex[i], hi) || !hexNibble(hex[i + 1], lo))
            return false;
        out.push_back(static_cast<char>((hi << 4) | lo));
    }
    return true;
}

int batch()
{
    std::string line;
    std::map<std::string, std::unique_ptr<PlayerNamePatternList>> cache;
    while (std::getline(std::cin, line))
    {
        std::size_t const tab = line.find('\t');
        if (tab == std::string::npos)
        {
            std::cerr << "batch: missing tab\n";
            return 2;
        }
        std::string pattern;
        std::string subject;
        if (!hexDecode(line.substr(0, tab), pattern) ||
            !hexDecode(line.substr(tab + 1), subject))
        {
            std::cerr << "batch: malformed hex\n";
            return 2;
        }
        try
        {
            auto it = cache.find(pattern);
            if (it == cache.end())
            {
                it = cache.emplace(pattern, std::unique_ptr<PlayerNamePatternList>(
                    new PlayerNamePatternList(one(pattern)))).first;
            }
            std::cout << (it->second->Matches(subject) ? '1' : '0') << '\n';
        }
        catch (std::exception const& e)
        {
            std::cerr << "batch: pattern " << line.substr(0, tab)
                      << ": " << e.what() << '\n';
            return 2;
        }
    }
    return 0;
}

int timing()
{
    std::vector<std::string> patterns;
    std::string line;
    while (std::getline(std::cin, line))
    {
        std::string pattern;
        if (!hexDecode(line, pattern) || pattern.empty())
        {
            std::cerr << "timing: malformed pattern\n";
            return 2;
        }
        patterns.push_back(pattern);
    }
    if (patterns.empty())
    {
        std::cerr << "timing: no patterns\n";
        return 2;
    }

    try
    {
        PlayerNamePatternList list(patterns);
        auto measure = [&](std::string const& subject)
        {
            auto const begin = std::chrono::steady_clock::now();
            bool const matched = list.Matches(subject);
            auto const end = std::chrono::steady_clock::now();
            double const ms = std::chrono::duration<double, std::milli>(end - begin).count();
            std::cout << "TIMING patterns=" << patterns.size()
                      << " units=" << subject.size()
                      << " matched=" << (matched ? 1 : 0)
                      << " elapsed_ms=" << std::fixed << std::setprecision(3) << ms << '\n';
        };
        measure(std::string("Abcdefghijkl Mnopqrstuvwx"));
        measure(std::string(1023, 'a'));
    }
    catch (std::exception const& e)
    {
        std::cerr << "timing: " << e.what() << '\n';
        return 2;
    }
    return 0;
}

void runTests()
{
    expectMatch("regression/backtrack_shorter_endpoint",
                "\\<a.*\\>", "ab" + utf8Bmp(0x4E2D), true);
    expectMatch("regression/backtrack_later_word",
                "\\<a.*b\\>", "Ab Bx", true);
    expectMatch("regression/absolute_bol",
                "\\<^.*b\\>", utf8Bmp(0x4E2D) + utf8Bmp(0x4E2D) + "Ab", false);
    expectMatch("regression/absolute_eol",
                "\\<a$", "ab", false);
    expectMatch("regression/later_valid_start",
                "\\<bar\\>", "foobar bar", true);
    expectMatch("synthetic/leading_only_greedy",
                "\\<a.*", "ab" + utf8Bmp(0x4E2D), true);
    expectMatch("synthetic/trailing_only_shorter_endpoint",
                "a.*\\>", "ab" + utf8Bmp(0x4E2D), true);
    expectMatch("synthetic/zero_length_span",
                "\\<a*\\>", "a", true);
    expectMatch("synthetic/empty_subject_no_boundary",
                "\\<a*\\>", "", false);

    expectMatch("synthetic/bar_start_end", "\\<bar\\>", "Bar", true);
    expectMatch("synthetic/bar_after_space", "\\<bar\\>", "Foo Bar", true);
    expectMatch("synthetic/bar_inside_word", "\\<bar\\>", "Foobar", false);
    expectMatch("synthetic/bar_prefix", "\\<bar\\>", "Barista", false);
    expectMatch("synthetic/bar_after_latin1", "\\<bar\\>", utf8Bmp(0x00C9) + "bar", false);
    expectMatch("synthetic/bar_before_latin1", "\\<bar\\>", "bar" + utf8Bmp(0x00C9), false);
    expectMatch("synthetic/bar_after_cjk", "\\<bar\\>", utf8Bmp(0x4E2D) + "bar", true);
    expectMatch("synthetic/bar_before_cjk", "\\<bar\\>", "bar" + utf8Bmp(0x4E2D), true);
    expectMatch("synthetic/cjk_alone", "\\<" + utf8Bmp(0x4E2D) + "\\>",
                utf8Bmp(0x4E2D), false);
    expectMatch("synthetic/cjk_between", "\\<" + utf8Bmp(0x4E2D) + "\\>",
                "a" + utf8Bmp(0x4E2D) + "a", true);
    expectMatch("synthetic/ascii_case", "aBc", "AbC", true);
    expectMatch("synthetic/non_ascii_case", utf8Bmp(0x00C9) + "clair",
                utf8Bmp(0x00E9) + "clair", false);
    expectMatch("synthetic/unanchored", "bar", "Foobar", true);
    expectMatch("synthetic/repeated_outer", "\\<\\<bar\\>\\>", "Bar", true);
    expectMatch("synthetic/dot", "a.*b", "axxxb", true);
    expectMatch("synthetic/question", "colou?r", "color", true);
    expectMatch("synthetic/empty_subject_star", "a*", "", true);
    expectMatch("synthetic/actual_class_quantifier", "\\<titt[iey]*s\\>", "titties", true);
    expectMatch("synthetic/actual_dot", "\\<r.*nad[ae]r\\>", "rxxxxnadar", true);
    expectMatch("synthetic/long_complex_nonmatch", "\\<a.*jolie\\>",
                std::string(1023, 'a'), false);

    expectAnyMatch("multiple/second_matches",
                   {"zzz", "bar"}, "bar", true);
    expectAnyMatch("multiple/none_match",
                   {"zzz", "qux"}, "bar", false);

    ++checks;
    try
    {
        PlayerNamePatternList list(one("\\<bar\\>"));
        bool const first = list.Matches("Bar");
        bool const second = list.Matches("Bar");
        if (!first || !second)
            fail("repeat/same_result", "repeated calls differed");
    }
    catch (std::exception const& e)
    {
        fail("repeat/same_result", std::string("threw: ") + e.what());
    }

    expectListRejected("list/empty", {}, "empty pattern list");
    expectListRejected("list/empty_pattern", {""}, "empty pattern");
    expectListRejected("list/bad_later_index", {"a*", "("}, "pattern 1");
    expectListRejected("list/invalid_utf8", {"\xC3"}, "invalid UTF-8");
    expectListRejected("list/non_bmp", {std::string("\xF0\x9F\x98\x80", 4)}, "non-BMP");
    expectListRejected("list/control", {std::string("\x01", 1)}, "control");

    expectPatternRejected("grammar/group", "\\<foo(bar)\\>", "group");
    expectPatternRejected("grammar/alternation", "\\<foo|bar\\>", "alternation");
    expectPatternRejected("grammar/negated_class", "\\<[^a]\\>", "negated");
    expectPatternRejected("grammar/ranged_class", "\\<[a-z]\\>", "range");
    expectPatternRejected("grammar/unknown_escape", "\\<foo\\d\\>", "escape");
    expectPatternRejected("grammar/brace", "\\<foo{2}\\>", "brace");
    expectPatternRejected("grammar/mid_boundary", "\\<foo.*\\<bar\\>", "outer edge");
    expectPatternRejected("grammar/unclosed_class", "\\<foo[bar\\>", "unclosed");
    expectPatternRejected("grammar/empty_core", "\\<\\>", "empty core");
    expectPatternRejected("grammar/duplicate_quantifier", "\\<foo**\\>", "quantifier");
    expectPatternRejected("grammar/lazy_quantifier", "\\<foo*?\\>", "lazy");
    expectPatternRejected("grammar/lookaround", "\\<foo(?=bar)\\>", "group");
    expectPatternRejected("grammar/stray_class_delimiter", "\\<foo]bar\\>", "class delimiter");
    expectPatternRejected("grammar/empty_class", "\\<foo[]\\>", "empty character class");

    expectSubjectRejected("subject/invalid_utf8", "\xC3", "invalid UTF-8");
    expectSubjectRejected("subject/non_bmp", std::string("\xF0\x9F\x98\x80", 4),
                          "unsupported scalar");
    expectSubjectRejected("subject/nul", std::string("\0", 1), "excluded");
    expectSubjectRejected("subject/cr", "\r", "excluded");
    expectSubjectRejected("subject/lf", "\n", "excluded");
    expectSubjectRejected("subject/line_separator", std::string("\xE2\x80\xA8", 3),
                          "excluded");
    expectSubjectRejected("subject/paragraph_separator", std::string("\xE2\x80\xA9", 3),
                          "excluded");

    ++checks;
    try
    {
        PlayerNamePatternList list(one("a*"));
        if (!list.Matches(std::string(1023, 'a')))
            fail("subject/1023", "1023-unit subject did not match");
    }
    catch (std::exception const& e)
    {
        fail("subject/1023", std::string("threw: ") + e.what());
    }
    expectSubjectTooLong("subject/1024", std::string(1024, 'a'));

    std::cout << "Executed " << checks << " checks; " << failures << " failure(s)\n";
}
}

int main(int argc, char** argv)
{
    if (argc == 2 && std::string(argv[1]) == "--batch")
        return batch();
    if (argc == 2 && std::string(argv[1]) == "--timing")
        return timing();
    if (argc != 1)
    {
        std::cerr << "usage: player_name_pattern_tests [--batch|--timing]\n";
        return 2;
    }
    runTests();
    return failures == 0 ? 0 : 1;
}
