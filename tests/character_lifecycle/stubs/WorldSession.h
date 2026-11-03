#ifndef FN05H_TEST_WORLDSESSION_H
#define FN05H_TEST_WORLDSESSION_H

#include "Common.h"

class Player;
class CharacterPersistenceContext;

enum TutorialDataState
{
    TUTORIALDATA_UNCHANGED = 0,
    TUTORIALDATA_CHANGED = 1,
    TUTORIALDATA_NEW = 2
};

#define ACCOUNT_TUTORIALS_COUNT 8

class WorldSession
{
    public:
        Player* GetPlayer() const { return _player; }
        uint32 GetAccountId() const { return accountId; }
        bool AppendTutorialsDataToContext(CharacterPersistenceContext& context);

        Player* _player = nullptr;
        uint32 accountId = 0;
        uint32 m_tutorials[ACCOUNT_TUTORIALS_COUNT] = {0};
        TutorialDataState m_tutorialState = TUTORIALDATA_UNCHANGED;
};

#endif
