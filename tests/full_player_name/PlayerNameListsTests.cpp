#include "Database/PlayerNameLists.h"

#include <cstdint>
#include <initializer_list>
#include <iostream>
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

using Rows = std::vector<PlayerNamePatternRow>;

Rows rows(std::initializer_list<std::pair<std::uint32_t, std::string>> values)
{
    Rows result;
    for (auto const& value : values)
        result.push_back(PlayerNamePatternRow{value.first, value.second});
    return result;
}

Rows placeholder()
{
    return rows({{9999, "zzzz"}});
}

enum class Which
{
    Names,
    Chat,
    Reserved
};

bool call(DbcPlayerNameLists const& lists, Which which, std::string const& subject)
{
    switch (which)
    {
        case Which::Names:
            return lists.NamesProfanity(subject);
        case Which::Chat:
            return lists.ChatProfanity(subject);
        case Which::Reserved:
            return lists.NamesReserved(subject);
    }
    return false;
}

void expectPredicate(std::string const& label,
                     DbcPlayerNameLists const& lists,
                     Which which,
                     std::string const& subject,
                     bool expected)
{
    ++checks;
    try
    {
        bool const actual = call(lists, which, subject);
        if (actual != expected)
            fail(label, std::string("expected ") + (expected ? "true" : "false") +
                       ", actual " + (actual ? "true" : "false"));
    }
    catch (std::exception const& e)
    {
        fail(label, std::string("threw: ") + e.what());
    }
}

void expectInvalid(std::string const& label,
                   Rows const& names,
                   Rows const& chat,
                   Rows const& reserved,
                   std::string const& needle)
{
    ++checks;
    try
    {
        DbcPlayerNameLists lists(names, chat, reserved);
        (void)lists;
        fail(label, "constructor accepted invalid rows");
    }
    catch (std::invalid_argument const& e)
    {
        if (std::string(e.what()).find(needle) == std::string::npos)
            fail(label, std::string("unexpected diagnostic: ") + e.what());
    }
    catch (std::exception const& e)
    {
        fail(label, std::string("wrong exception: ") + e.what());
    }
}

void expectPreparedWith(std::string const& label,
                        Rows const& names,
                        Rows const& chat,
                        Rows const& reserved,
                        std::string input,
                        std::int32_t region,
                        std::size_t locale,
                        std::uint8_t expectedStatus,
                        std::string const& expectedText)
{
    ++checks;
    try
    {
        DbcPlayerNameLists lists(names, chat, reserved);
        std::uint8_t const status = prepareFullPlayerName(
            input, lists, PlayerNameCharsets::ForClient(region, locale));
        if (status != expectedStatus || input != expectedText)
        {
            fail(label, "unexpected status/text: " + std::to_string(status) +
                       " / " + input);
        }
    }
    catch (std::exception const& e)
    {
        fail(label, std::string("threw: ") + e.what());
    }
}

void expectPrepared(std::string const& label,
                    Rows const& names,
                    Rows const& chat,
                    Rows const& reserved,
                    std::string input,
                    std::uint8_t expectedStatus,
                    std::string const& expectedText)
{
    expectPreparedWith(label, names, chat, reserved, input, 0, 0,
                       expectedStatus, expectedText);
}

void runTests()
{
    Rows const names = rows({{1, "alpha"}});
    Rows const chat = rows({{2, "beta"}});
    Rows const reserved = rows({{3, "gamma"}});
    DbcPlayerNameLists lists(names, chat, reserved);

    expectPredicate("routing/names", lists, Which::Names, "alpha", true);
    expectPredicate("routing/names_not_chat", lists, Which::Chat, "alpha", false);
    expectPredicate("routing/names_not_reserved", lists, Which::Reserved, "alpha", false);
    expectPredicate("routing/chat", lists, Which::Chat, "beta", true);
    expectPredicate("routing/chat_not_names", lists, Which::Names, "beta", false);
    expectPredicate("routing/reserved", lists, Which::Reserved, "gamma", true);
    expectPredicate("routing/reserved_not_chat", lists, Which::Chat, "gamma", false);

    expectPredicate("repeat/names_first", lists, Which::Names, "alpha", true);
    expectPredicate("repeat/names_second", lists, Which::Names, "alpha", true);

    // Evaluation failures reject rather than admit.
    expectPredicate("evaluation/names_invalid_utf8", lists, Which::Names, "\xC3", true);
    expectPredicate("evaluation/names_oversized", lists, Which::Names,
                    std::string(1024, 'a'), true);
    expectPredicate("evaluation/chat_invalid_utf8", lists, Which::Chat, "\xC3", true);
    expectPredicate("evaluation/chat_oversized", lists, Which::Chat,
                    std::string(1024, 'a'), true);
    expectPredicate("evaluation/reserved_invalid_utf8", lists, Which::Reserved,
                    "\xC3", true);
    expectPredicate("evaluation/reserved_oversized", lists, Which::Reserved,
                    std::string(1024, 'a'), true);
    expectPredicate("evaluation/reserved_line_separator", lists, Which::Reserved,
                    std::string("\xE2\x80\xA8", 3), true);

    expectInvalid("empty/names", {}, chat, reserved, "NamesProfanity.dbc");
    expectInvalid("empty/chat", names, {}, reserved, "ChatProfanity.dbc");
    expectInvalid("empty/reserved", names, chat, {}, "NamesReserved.dbc");

    expectInvalid("invalid/later_names_row",
                  rows({{7, "alpha"}, {42, "("}}), chat, reserved,
                  "NamesProfanity.dbc row 42");
    expectInvalid("invalid/chat_utf8",
                  names, rows({{8, "\xC3"}}), reserved,
                  "ChatProfanity.dbc row 8");
    expectInvalid("invalid/reserved_grammar",
                  names, chat, rows({{9, "[^a]"}}),
                  "NamesReserved.dbc row 9");
    expectInvalid("invalid/empty_pattern",
                  rows({{10, ""}}), chat, reserved,
                  "NamesProfanity.dbc row 10");

    // The adapter must own compiled data, not the caller's vectors.
    std::unique_ptr<DbcPlayerNameLists> owned;
    {
        Rows localNames = rows({{20, "alpha"}});
        Rows localChat = rows({{21, "beta"}});
        Rows localReserved = rows({{22, "gamma"}});
        owned.reset(new DbcPlayerNameLists(localNames, localChat, localReserved));
        localNames.clear();
        localChat.clear();
        localReserved.clear();
    }
    expectPredicate("ownership/after_vector_clear", *owned, Which::Names, "alpha", true);
    expectPredicate("ownership/after_vector_clear_chat", *owned, Which::Chat, "beta", true);

    expectPrepared("prepare/valid", placeholder(), placeholder(), placeholder(),
                   "aSHA brightVALE", 0x52, "Asha Brightvale");
    expectPrepared("prepare/names_hit",
                   rows({{30, "asha"}}), placeholder(), placeholder(),
                   "aSHA brightVALE", 0x4b, "aSHA brightVALE");
    expectPrepared("prepare/chat_hit",
                   placeholder(), rows({{31, "brightvale"}}), placeholder(),
                   "aSHA brightVALE", 0x4b, "aSHA brightVALE");
    expectPrepared("prepare/reserved_hit",
                   placeholder(), placeholder(), rows({{32, "asha brightvale"}}),
                   "aSHA brightVALE", 0x4b, "aSHA brightVALE");
    expectPrepared("prepare/spaced_combined",
                   placeholder(), placeholder(), rows({{33, "john smith"}}),
                   "John Smith", 0x4b, "John Smith");
    expectPrepared("prepare/joined_combined",
                   placeholder(), placeholder(), rows({{34, "johnsmith"}}),
                   "John Smith", 0x4b, "John Smith");
    expectPrepared("prepare/max_latin",
                   placeholder(), placeholder(), placeholder(),
                   "Abcdefghijkl Mnopqrstuvwx", 0x52,
                   "Abcdefghijkl Mnopqrstuvwx");
    expectPrepared("prepare/short",
                   placeholder(), placeholder(), placeholder(),
                   "A Brightvale", 0x46, "A Brightvale");
    expectPrepared("prepare/repetition",
                   placeholder(), placeholder(), placeholder(),
                   "Aaab Brightvale", 0x4e, "Aaab Brightvale");
    expectPrepared("prepare/prohibited_initial",
                   placeholder(), placeholder(), placeholder(),
                   "\xC3\x9F" "eta Brightvale", 0x48, "\xC3\x9F" "eta Brightvale");
    expectPreparedWith("prepare/region2_secondary_cap",
                       placeholder(), placeholder(), placeholder(),
                       "Abcdefghi Brightvale", 2, 0, 0x47,
                       "Abcdefghi Brightvale");

    std::cout << "Executed " << checks << " checks; " << failures << " failure(s)\n";
}
}

int main()
{
    runTests();
    return failures == 0 ? 0 : 1;
}
