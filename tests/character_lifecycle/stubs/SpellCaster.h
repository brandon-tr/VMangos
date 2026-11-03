#ifndef FN05H_TEST_SPELLCASTER_H
#define FN05H_TEST_SPELLCASTER_H

#include "Common.h"

#include <memory>
#include <map>

struct SpellEntry
{
    uint32 Id = 0;
};

class CooldownData
{
    public:
        CooldownData(SpellEntry const* spell, uint32 category,
                     uint32 itemId, TimePoint spellExpiry,
                     TimePoint categoryExpiry, bool permanent)
            : m_spell(spell), m_category(category), m_itemId(itemId),
              m_spellExpiry(spellExpiry), m_categoryExpiry(categoryExpiry),
              m_permanent(permanent)
        {
        }

        bool IsPermanent() const { return m_permanent; }
        SpellEntry const* GetSpellEntry() const { return m_spell; }
        uint32 GetCategory() const { return m_category; }
        uint32 GetItemId() const { return m_itemId; }
        bool GetSpellCDExpireTime(TimePoint& value) const
        {
            if (m_permanent)
                return false;
            value = m_spellExpiry;
            return true;
        }
        bool GetCatCDExpireTime(TimePoint& value) const
        {
            if (m_permanent)
                return false;
            value = m_categoryExpiry;
            return true;
        }

    private:
        SpellEntry const* m_spell;
        uint32 m_category;
        uint32 m_itemId;
        TimePoint m_spellExpiry;
        TimePoint m_categoryExpiry;
        bool m_permanent;
};

typedef std::map<uint32, std::unique_ptr<CooldownData>> CooldownContainer;

#endif
