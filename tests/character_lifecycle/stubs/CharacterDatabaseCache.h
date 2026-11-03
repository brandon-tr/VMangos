#ifndef FN05H_TEST_CHARACTER_DATABASE_CACHE_H
#define FN05H_TEST_CHARACTER_DATABASE_CACHE_H

#include "Common.h"

#define MAX_EFFECT_INDEX 3

struct ObjectGuid
{
    ObjectGuid(uint64 value = 0) : raw(value) {}
    uint64 GetRawValue() const { return raw; }
    uint64 raw;
};

struct AuraSaveStruct
{
    ObjectGuid casterGuid;
    uint32 itemLowGuid = 0;
    uint32 spellId = 0;
    uint32 stacks = 0;
    uint32 charges = 0;
    float damage[MAX_EFFECT_INDEX] = {0};
    uint32 periodicTime[MAX_EFFECT_INDEX] = {0};
    int32 maxDuration = 0;
    int32 duration = 0;
    uint8 effIndexMask = 0;
};

#endif
