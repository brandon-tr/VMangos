#ifndef FN05H_TEST_REPUTATION_H
#define FN05H_TEST_REPUTATION_H

#include "Objects/Player.h"

#include <map>

class CharacterPersistenceContext;

struct FactionState
{
    uint32 ID = 0;
    uint32 ReputationListID = 0;
    uint32 Flags = 0;
    int32 Standing = 0;
    bool needSend = false;
    bool needSave = false;
};

typedef std::map<uint32, FactionState> FactionStateList;

class ReputationMgr
{
    public:
        explicit ReputationMgr(Player* owner) : m_player(owner) {}
        bool AppendSaveToDB(CharacterPersistenceContext& context);

        Player* m_player = nullptr;
        FactionStateList m_factions;
};

#endif
