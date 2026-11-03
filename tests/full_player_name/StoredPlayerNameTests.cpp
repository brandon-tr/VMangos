#include "PlayerName.h"

#include <cstdint>
#include <iostream>
#include <string>

namespace
{
int checks = 0;
int failures = 0;

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

void fail(std::string const& label, std::string const& message)
{
    ++failures;
    std::cerr << "FAIL " << label << ": " << message << '\n';
}

class NoRestrictions final : public PlayerNameLists
{
public:
    bool NamesProfanity(std::string const&) const override { return false; }
    bool ChatProfanity(std::string const&) const override { return false; }
    bool NamesReserved(std::string const&) const override { return false; }
};

class DenyAllRestrictions final : public PlayerNameLists
{
public:
    bool NamesProfanity(std::string const&) const override { return true; }
    bool ChatProfanity(std::string const&) const override { return true; }
    bool NamesReserved(std::string const&) const override { return true; }
};

void expectCanonical(std::string const& label, std::string const& name, bool expected)
{
    ++checks;
    try
    {
        bool const actual = isCanonicalFullPlayerName(name);
        if (actual != expected)
            fail(label, std::string("expected ") + (expected ? "true" : "false"));
    }
    catch (std::exception const& e)
    {
        fail(label, std::string("threw: ") + e.what());
    }
}

void expectLookup(std::string const& label, std::string input,
                  bool expected, std::string const& expectedText)
{
    ++checks;
    try
    {
        bool const actual = normalizeFullPlayerNameForLookup(input);
        if (actual != expected || input != expectedText)
            fail(label, "unexpected result/text: " + std::string(actual ? "true" : "false") +
                       " / " + input);
    }
    catch (std::exception const& e)
    {
        fail(label, std::string("threw: ") + e.what());
    }
}

void runTests()
{
    expectCanonical("canonical/latin", "Asha Brightvale", true);
    expectCanonical("canonical/max_latin", "Abcdefghijkl Mnopqrstuvwx", true);
    expectCanonical("canonical/john_sm", "John Sm", true);
    expectCanonical("canonical/latin1",
                    utf8Bmp(0x00C0) + "bcdefghijkl M" + utf8Bmp(0x00F1) + "opqrstuvwx", true);
    expectCanonical("canonical/hangul",
                    utf8Bmp(0xAC00) + utf8Bmp(0xB098) + utf8Bmp(0xB2E4) +
                    utf8Bmp(0xB77C) + utf8Bmp(0xB9C8) + utf8Bmp(0xBC14) +
                    utf8Bmp(0xC0AC) + utf8Bmp(0xC544) + " " +
                    utf8Bmp(0xC790) + utf8Bmp(0xCC28) + utf8Bmp(0xCE74) +
                    utf8Bmp(0xD0C0) + utf8Bmp(0xD30C) + utf8Bmp(0xD558) +
                    utf8Bmp(0xAC00) + utf8Bmp(0xB098), true);
    expectCanonical("canonical/cjk",
                    utf8Bmp(0x4E00) + utf8Bmp(0x4E8C) + utf8Bmp(0x4E09) +
                    utf8Bmp(0x56DB) + utf8Bmp(0x4E94) + utf8Bmp(0x516D) + " " +
                    utf8Bmp(0x4E03) + utf8Bmp(0x516B) + utf8Bmp(0x4E5D) +
                    utf8Bmp(0x5341) + utf8Bmp(0x767E) + utf8Bmp(0x5343), true);
    expectCanonical("canonical/cross_part",
                    utf8Bmp(0xAC00) + utf8Bmp(0xB098) + utf8Bmp(0xB2E4) +
                    utf8Bmp(0xB77C) + utf8Bmp(0xB9C8) + utf8Bmp(0xBC14) +
                    utf8Bmp(0xC0AC) + utf8Bmp(0xC544) + " Abcdefgh", true);

    expectCanonical("invalid/empty", "", false);
    expectCanonical("invalid/no_separator", "AshaBrightvale", false);
    expectCanonical("invalid/extra_space", "Asha  Brightvale", false);
    expectCanonical("invalid/short_first", "A Brightvale", false);
    expectCanonical("invalid/short_last", "Asha B", false);
    expectCanonical("invalid/lower_first", "asha Brightvale", false);
    expectCanonical("invalid/lower_last", "Asha brightvale", false);
    expectCanonical("invalid/latin_too_long", "Abcdefghijklm Brightvale", false);
    expectCanonical("invalid/cjk_too_long", utf8Bmp(0x4E00) + utf8Bmp(0x4E8C) +
                    utf8Bmp(0x4E09) + utf8Bmp(0x56DB) + utf8Bmp(0x4E94) +
                    utf8Bmp(0x516D) + utf8Bmp(0x4E03) + " " + "Abcdefg", false);
    expectCanonical("invalid/repetition", "Aaab Brightvale", false);
    expectCanonical("invalid/accents", utf8Bmp(0x00E9) + utf8Bmp(0x00C9) +
                    utf8Bmp(0x00E9) + utf8Bmp(0x00C9) + " Vale", false);
    expectCanonical("invalid/nul", std::string("Asha\0Brightvale", 15), false);
    expectCanonical("invalid/control", "Asha\tBrightvale", false);
    expectCanonical("invalid/malformed", std::string("\xC3 Brightvale", 12), false);
    expectCanonical("invalid/supplementary", std::string("\xF0\x9F\x98\x80 Brightvale", 15),
                    false);
    expectCanonical("invalid/initial_sharp_s", utf8Bmp(0x00DF) + "eta Brightvale", false);
    expectCanonical("invalid/initial_thorn", utf8Bmp(0x00FE) + "or Brightvale", false);
    expectCanonical("invalid/initial_y_diaeresis", utf8Bmp(0x00FF) + "ard Brightvale", false);

    expectLookup("lookup/mixed_case", "aSHA brightVALE", true, "Asha Brightvale");
    expectLookup("lookup/john_sm", "John Sm", true, "John Sm");
    expectLookup("lookup/canonical", "Asha Brightvale", true, "Asha Brightvale");
    expectLookup("lookup/failure", "John", false, "John");
    expectLookup("lookup/lowercase", "asha brightvale", true, "Asha Brightvale");
    expectLookup("lookup/no_expansion", "John Sm", true, "John Sm");

    ++checks;
    if (!isCanonicalFullPlayerName("John Smith") ||
        !isCanonicalFullPlayerName("John Smyth"))
        fail("identity/shared_first", "distinct canonical surnames rejected");

    expectLookup("failure/preserves_bytes", "Asha  Brightvale", false, "Asha  Brightvale");
    expectLookup("failure/quoted", "\"John Smith\"", false, "\"John Smith\"");
    expectLookup("failure/pipe", "John Smith|who", false, "John Smith|who");
    expectLookup("failure/extra_word", "John Smith extra", false, "John Smith extra");
    expectLookup("failure/control", "John\tSmith", false, "John\tSmith");

    // Stored identity is independent of the querying actor's profile and current lists.
    std::string const cjk =
        utf8Bmp(0x674E) + utf8Bmp(0x660E) + " " + utf8Bmp(0x738B) + utf8Bmp(0x6797);
    std::string cjkAdmission = cjk;
    ++checks;
    if (prepareFullPlayerName(cjkAdmission, NoRestrictions(),
                              PlayerNameCharsets::ForClient(1, 0)) != 0x48 ||
        cjkAdmission != cjk)
        fail("independence/locale_admission", "Latin-only admission accepted CJK or mutated it");
    expectCanonical("independence/locale_stored", cjk, true);
    expectLookup("independence/locale_lookup", cjk, true, cjk);

    std::string restricted = "Asha Brightvale";
    ++checks;
    if (prepareFullPlayerName(restricted, DenyAllRestrictions(),
                              PlayerNameCharsets::ForClient(0, 0)) != 0x4b ||
        restricted != "Asha Brightvale")
        fail("independence/restriction_admission", "deny-all admission did not reject/leak");
    expectCanonical("independence/restriction_stored", "Asha Brightvale", true);
    expectLookup("independence/restriction_lookup", "aSHA brightVALE", true,
                 "Asha Brightvale");

    std::cout << "Executed " << checks << " checks; " << failures << " failure(s)\n";
}
}

int main()
{
    runTests();
    return failures == 0 ? 0 : 1;
}
