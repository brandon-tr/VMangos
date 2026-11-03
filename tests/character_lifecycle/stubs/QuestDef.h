#ifndef FN05H_TEST_QUESTDEF_H
#define FN05H_TEST_QUESTDEF_H

#include "Common.h"
#include <map>

enum QuestStatus
{
    QUEST_STATUS_NONE = 0,
    QUEST_STATUS_COMPLETE = 1,
    QUEST_STATUS_INCOMPLETE = 3
};

enum QuestUpdateState
{
    QUEST_UNCHANGED = 0,
    QUEST_CHANGED = 1,
    QUEST_NEW = 2,
    QUEST_DELETED = 3
};

#define QUEST_OBJECTIVES_COUNT 4

struct QuestStatusData
{
    QuestStatusData()
        : m_status(QUEST_STATUS_NONE), m_rewarded(false),
          m_explored(false), m_timer(0), uState(QUEST_NEW),
          m_reward_choice(0), m_itemcount{}, m_creatureOrGOcount{}
    {
    }

    QuestStatus m_status;
    bool m_rewarded;
    bool m_explored;
    uint32 m_timer;
    QuestUpdateState uState;
    uint32 m_reward_choice;
    uint32 m_itemcount[QUEST_OBJECTIVES_COUNT];
    uint32 m_creatureOrGOcount[QUEST_OBJECTIVES_COUNT];
};

typedef std::map<uint32, QuestStatusData> QuestStatusMap;

#endif
