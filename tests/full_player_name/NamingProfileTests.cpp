#include "PlayerName.h"

#include <cstddef>
#include <cstdint>
#include <iostream>
#include <string>

namespace
{
int checks = 0;
int failures = 0;

void fail(std::string const& label, std::string const& message)
{
    ++failures;
    std::cerr << "FAIL " << label << ": " << message << '\n';
}

void expectCharsets(std::string const& label, std::int32_t region, std::size_t locale,
                    std::uint8_t primary, std::uint8_t secondary)
{
    ++checks;
    PlayerNameCharsets const actual = PlayerNameCharsets::ForClient(region, locale);
    if (actual.primary != primary || actual.secondary != secondary)
        fail(label, "unexpected charset pair");
}

void expectHandshake(std::string const& label, std::uint8_t const* data, std::size_t size,
                     bool expected, std::uint8_t expectedRegion,
                     std::uint8_t initialRegion = 0xA5)
{
    ++checks;
    std::uint8_t region = initialRegion;
    bool const actual = decodePlayerNameHandshake(data, size, region);
    if (actual != expected || (expected && region != expectedRegion) ||
        (!expected && region != initialRegion))
    {
        fail(label, "unexpected decode result/region");
    }
}

void runTests()
{
    expectCharsets("locale/enUS", 0, 0, 1, 1);
    expectCharsets("locale/koKR", 0, 1, 2, 4);
    expectCharsets("locale/frFR", 0, 2, 1, 1);
    expectCharsets("locale/deDE", 0, 3, 1, 1);
    expectCharsets("locale/zhCN", 0, 4, 3, 1);
    expectCharsets("locale/zhTW", 0, 5, 3, 1);
    expectCharsets("locale/esES", 0, 6, 1, 1);
    expectCharsets("locale/xxYY", 0, 7, 1, 1);
    expectCharsets("locale/ruRU", 0, 8, 1, 1);
    expectCharsets("region/1", 1, 0, 1, 1);
    expectCharsets("region/2", 2, 0, 2, 4);
    expectCharsets("region/3", 3, 0, 1, 1);
    expectCharsets("region/4", 4, 0, 3, 1);
    expectCharsets("region/5", 5, 0, 3, 1);
    expectCharsets("region/0_locale_koKR", 0, 1, 2, 4);
    expectCharsets("region/0_locale_zhCN", 0, 4, 3, 1);
    expectCharsets("region/6_fallback", 6, 1, 2, 4);
    expectCharsets("region/negative_fallback", -1, 4, 3, 1);
    expectCharsets("region/255_fallback", 255, 0, 1, 1);

    // Complete explicit 54-combination table: Regions 0..5 x locale indices 0..8.
    std::uint8_t const localePrimary[9] = {1, 2, 1, 1, 3, 3, 1, 1, 1};
    std::uint8_t const localeSecondary[9] = {1, 4, 1, 1, 1, 1, 1, 1, 1};
    std::uint8_t const regionPrimary[6] = {0, 1, 2, 1, 3, 3};
    std::uint8_t const regionSecondary[6] = {0, 1, 4, 1, 1, 1};
    for (std::int32_t region = 0; region <= 5; ++region)
    {
        for (std::size_t locale = 0; locale <= 8; ++locale)
        {
            std::uint8_t const primary =
                region == 0 ? localePrimary[locale] : regionPrimary[region];
            std::uint8_t const secondary =
                region == 0 ? localeSecondary[locale] : regionSecondary[region];
            expectCharsets("matrix/region_" + std::to_string(region) +
                               "/locale_" + std::to_string(locale),
                           region, locale, primary, secondary);
        }
    }

    std::uint8_t const valid[] = {0x45, 0x46, 0x4E, 0x4D, 0x01, 0x00};
    for (std::uint8_t region = 0; region <= 5; ++region)
    {
        std::uint8_t data[6] = {0x45, 0x46, 0x4E, 0x4D, 0x01, region};
        expectHandshake("handshake/valid_" + std::to_string(region),
                        data, 6, true, region);
    }
    expectHandshake("handshake/null", nullptr, 6, false, 0);
    expectHandshake("handshake/truncated_0", valid, 0, false, 0);
    expectHandshake("handshake/truncated_1", valid, 1, false, 0);
    expectHandshake("handshake/truncated_2", valid, 2, false, 0);
    expectHandshake("handshake/truncated_3", valid, 3, false, 0);
    expectHandshake("handshake/truncated_4", valid, 4, false, 0);
    expectHandshake("handshake/truncated_5", valid, 5, false, 0);
    expectHandshake("handshake/extra_7", valid, 7, false, 0);

    std::uint8_t badMagic[6] = {0x45, 0x46, 0x4E, 0x4E, 0x01, 0x00};
    expectHandshake("handshake/bad_magic", badMagic, 6, false, 0);

    std::uint8_t badVersion0[6] = {0x45, 0x46, 0x4E, 0x4D, 0x00, 0x00};
    std::uint8_t badVersion2[6] = {0x45, 0x46, 0x4E, 0x4D, 0x02, 0x00};
    expectHandshake("handshake/version_0", badVersion0, 6, false, 0);
    expectHandshake("handshake/version_2", badVersion2, 6, false, 0);

    std::uint8_t badRegion6[6] = {0x45, 0x46, 0x4E, 0x4D, 0x01, 0x06};
    std::uint8_t badRegion255[6] = {0x45, 0x46, 0x4E, 0x4D, 0x01, 0xFF};
    expectHandshake("handshake/region_6", badRegion6, 6, false, 0);
    expectHandshake("handshake/region_255", badRegion255, 6, false, 0);

    std::cout << "Executed " << checks << " checks; " << failures << " failure(s)\n";
}
}

int main()
{
    runTests();
    return failures == 0 ? 0 : 1;
}
