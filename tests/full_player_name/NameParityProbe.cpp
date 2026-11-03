/*
 * Offline name-parity probe.
 *
 * Reads commands from stdin:
 *   P <region> <locale>
 *   N <id> <region> <locale> <mask> <hex-utf8>
 * and writes a stable tab-separated line per command.  It uses the real
 * PlayerName.cpp implementation and a synthetic list service.
 */

#include "PlayerName.h"

#include <cctype>
#include <cstdio>
#include <cstdint>
#include <iostream>
#include <string>

namespace
{
class Lists final : public PlayerNameLists
{
public:
    explicit Lists(unsigned mask) : m_mask(mask) {}

    bool NamesProfanity(std::string const& name) const override
    {
        return (m_mask & 1) && contains(name, "badname");
    }
    bool ChatProfanity(std::string const& name) const override
    {
        return (m_mask & 2) && contains(name, "badchat");
    }
    bool NamesReserved(std::string const& name) const override
    {
        return (m_mask & 4) && contains(name, "badres");
    }

private:
    static std::string asciiLower(std::string const& value)
    {
        std::string result = value;
        for (char& c : result)
        {
            unsigned char const byte = static_cast<unsigned char>(c);
            if (byte >= 'A' && byte <= 'Z')
                c = static_cast<char>(byte - 'A' + 'a');
        }
        return result;
    }

    static bool contains(std::string const& name, char const* word)
    {
        return asciiLower(name).find(word) != std::string::npos;
    }

    unsigned m_mask;
};

std::string hexEncode(std::string const& value)
{
    static char const digits[] = "0123456789abcdef";
    std::string result;
    result.reserve(value.size() * 2);
    for (unsigned char byte : value)
    {
        result.push_back(digits[byte >> 4]);
        result.push_back(digits[byte & 0x0F]);
    }
    return result;
}

bool hexDecode(std::string const& hex, std::string& out)
{
    if (hex.size() % 2 != 0)
        return false;
    out.clear();
    out.reserve(hex.size() / 2);
    auto nibble = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    };
    for (std::size_t i = 0; i < hex.size(); i += 2)
    {
        int const high = nibble(hex[i]);
        int const low = nibble(hex[i + 1]);
        if (high < 0 || low < 0)
            return false;
        out.push_back(static_cast<char>((high << 4) | low));
    }
    return true;
}
}

int main()
{
    std::string line;
    while (std::getline(std::cin, line))
    {
        if (line.empty())
            continue;

        char kind = line[0];
        if (kind == 'P')
        {
            int region = 0;
            unsigned locale = 0;
            if (std::sscanf(line.c_str(), "P %d %u", &region, &locale) != 2)
                return 2;
            PlayerNameCharsets const pair =
                PlayerNameCharsets::ForClient(region, locale);
            std::cout << "P\t" << region << '\t' << locale << '\t'
                      << unsigned(pair.primary) << '\t'
                      << unsigned(pair.secondary) << '\n';
            continue;
        }
        if (kind != 'N')
            return 3;

        std::string id;
        int region = 0;
        unsigned locale = 0;
        unsigned mask = 0;
        std::string hex;
        std::size_t const p1 = line.find('\t');
        if (p1 == std::string::npos)
            return 4;
        std::size_t const p2 = line.find('\t', p1 + 1);
        std::size_t const p3 = line.find('\t', p2 + 1);
        std::size_t const p4 = line.find('\t', p3 + 1);
        std::size_t const p5 = line.find('\t', p4 + 1);
        if (p2 == std::string::npos || p3 == std::string::npos ||
            p4 == std::string::npos || p5 == std::string::npos)
            return 5;
        id = line.substr(p1 + 1, p2 - p1 - 1);
        region = std::stoi(line.substr(p2 + 1, p3 - p2 - 1));
        locale = unsigned(std::stoul(line.substr(p3 + 1, p4 - p3 - 1)));
        mask = unsigned(std::stoul(line.substr(p4 + 1, p5 - p4 - 1)));
        hex = line.substr(p5 + 1);

        std::string name;
        if (!hexDecode(hex, name))
            return 6;

        PlayerNameCharsets const pair =
            PlayerNameCharsets::ForClient(region, locale);
        Lists const lists(mask);

        std::string prepared = name;
        std::uint8_t const raw =
            checkFullPlayerName(name, lists, pair);
        std::uint8_t const status =
            prepareFullPlayerName(prepared, lists, pair);
        bool const unchanged = prepared == name;

        std::cout << "N\t" << id << '\t' << unsigned(raw) << '\t'
                  << unsigned(status) << '\t' << (unchanged ? 1 : 0) << '\t'
                  << unsigned(pair.primary) << '\t'
                  << unsigned(pair.secondary) << '\t'
                  << (status == 0x52 ? hexEncode(prepared) : "-") << '\n';
    }
    return 0;
}
