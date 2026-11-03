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

#ifndef MANGOS_DBC_PLAYER_NAME_LISTS_H
#define MANGOS_DBC_PLAYER_NAME_LISTS_H

#include "PlayerName.h"
#include "PlayerNamePattern.h"

#include <cstdint>
#include <string>
#include <vector>

struct PlayerNamePatternRow
{
    std::uint32_t id;
    std::string pattern;
};

/// Immutable adapter over the three DBC restriction lists.
///
/// Each predicate returns true for a match and also for a restriction
/// evaluation failure (invalid subject, length error, or regex engine error),
/// so callers reject the name rather than admitting it.
class DbcPlayerNameLists final : public PlayerNameLists
{
public:
    DbcPlayerNameLists(
        std::vector<PlayerNamePatternRow> const& namesProfanity,
        std::vector<PlayerNamePatternRow> const& chatProfanity,
        std::vector<PlayerNamePatternRow> const& namesReserved);

    bool NamesProfanity(std::string const& name) const override;
    bool ChatProfanity(std::string const& name) const override;
    bool NamesReserved(std::string const& name) const override;

private:
    static PlayerNamePatternList compileList(
        char const* filename,
        std::vector<PlayerNamePatternRow> const& rows);

    static bool matches(PlayerNamePatternList const& list,
                        std::string const& name);

    PlayerNamePatternList m_namesProfanity;
    PlayerNamePatternList m_chatProfanity;
    PlayerNamePatternList m_namesReserved;
};

#endif // MANGOS_DBC_PLAYER_NAME_LISTS_H
