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

#include "PlayerNameLists.h"

#include <regex>
#include <stdexcept>
#include <string>
#include <vector>

PlayerNamePatternList DbcPlayerNameLists::compileList(
    char const* filename,
    std::vector<PlayerNamePatternRow> const& rows)
{
    if (rows.empty())
        throw std::invalid_argument(std::string(filename) + ": empty list");

    std::vector<std::string> patterns;
    patterns.reserve(rows.size());
    for (PlayerNamePatternRow const& row : rows)
        patterns.push_back(row.pattern);

    try
    {
        return PlayerNamePatternList(patterns);
    }
    catch (std::invalid_argument const& bulkError)
    {
        // Compile single rows only on the startup failure path to identify the
        // first bad row without parsing the matcher's diagnostic text.
        for (PlayerNamePatternRow const& row : rows)
        {
            try
            {
                PlayerNamePatternList single(std::vector<std::string>{row.pattern});
                (void)single;
            }
            catch (std::invalid_argument const& rowError)
            {
                throw std::invalid_argument(
                    std::string(filename) + " row " + std::to_string(row.id) +
                    ": " + rowError.what());
            }
        }

        throw std::invalid_argument(std::string(filename) + ": " + bulkError.what());
    }
}

DbcPlayerNameLists::DbcPlayerNameLists(
    std::vector<PlayerNamePatternRow> const& namesProfanity,
    std::vector<PlayerNamePatternRow> const& chatProfanity,
    std::vector<PlayerNamePatternRow> const& namesReserved)
    : m_namesProfanity(compileList("NamesProfanity.dbc", namesProfanity)),
      m_chatProfanity(compileList("ChatProfanity.dbc", chatProfanity)),
      m_namesReserved(compileList("NamesReserved.dbc", namesReserved))
{
}

bool DbcPlayerNameLists::matches(PlayerNamePatternList const& list,
                                 std::string const& name)
{
    try
    {
        return list.Matches(name);
    }
    catch (std::invalid_argument const&)
    {
        return true;
    }
    catch (std::length_error const&)
    {
        return true;
    }
    catch (std::regex_error const&)
    {
        return true;
    }
}

bool DbcPlayerNameLists::NamesProfanity(std::string const& name) const
{
    return matches(m_namesProfanity, name);
}

bool DbcPlayerNameLists::ChatProfanity(std::string const& name) const
{
    return matches(m_chatProfanity, name);
}

bool DbcPlayerNameLists::NamesReserved(std::string const& name) const
{
    return matches(m_namesReserved, name);
}
