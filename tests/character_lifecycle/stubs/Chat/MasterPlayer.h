#ifndef FN05H_TEST_MASTER_PLAYER_H
#define FN05H_TEST_MASTER_PLAYER_H

#include "Common.h"
#include "Objects/Player.h"
#include "WorldSession.h"

#include <map>
#include <string>

class CharacterPersistenceContext;

enum ActionButtonType
{
    ACTION_BUTTON_SPELL = 0x00,
    ACTION_BUTTON_C = 0x01,
    ACTION_BUTTON_MACRO = 0x40,
    ACTION_BUTTON_CMACRO = 0x41,
    ACTION_BUTTON_ITEM = 0x80
};

enum ActionButtonUpdateState
{
    ACTIONBUTTON_UNCHANGED = 0,
    ACTIONBUTTON_CHANGED = 1,
    ACTIONBUTTON_NEW = 2,
    ACTIONBUTTON_DELETED = 3
};

struct ActionButton
{
    uint32 packedData = 0;
    ActionButtonUpdateState uState = ACTIONBUTTON_NEW;

    ActionButtonType GetType() const
    {
        return ActionButtonType((packedData >> 24) & 0xFF);
    }
    uint32 GetAction() const { return packedData & 0x00FFFFFF; }
};

typedef std::map<uint8, ActionButton> ActionButtonList;

class MasterPlayer
{
    public:
        explicit MasterPlayer(WorldSession* s) : m_session(s) {}
        uint32 GetGUIDLow() const { return guid; }
        WorldSession* GetSession() const { return m_session; }
        bool AppendActionsToDB(CharacterPersistenceContext& context);

        uint32 guid = 0;
        std::string name;
        uint64 identityGeneration = 0;
        WorldSession* m_session = nullptr;
        ActionButtonList m_actionButtons;
};

#endif
