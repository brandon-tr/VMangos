#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

namespace
{
int checks = 0;
int failures = 0;

void check(bool condition, std::string const& label)
{
    ++checks;
    if (!condition)
    {
        ++failures;
        std::cerr << "FAIL " << label << "\n";
    }
}

constexpr int LOG_BASIC = 0;
constexpr int LOG_LVL_ERROR = 1;
constexpr int LOG_LVL_MINIMAL = 2;

struct Scenario
{
    int realm = 1;
    std::string failStartDB;
    bool schemaFailure = false;
    bool ownershipFailure = false;
    std::string schemaReason = "schema rejected";
    std::string ownershipReason = "ownership rejected";
    std::vector<std::string> initCalls;
    std::vector<std::string> validationCalls;
    std::vector<std::string> writes;
    std::vector<std::string> logs;
    int haltWorld = 0;
    int haltCharacter = 0;
    int haltLogin = 0;
    int haltLogs = 0;
    int clearCalls = 0;

    void Reset()
    {
        realm = 1;
        failStartDB.clear();
        schemaFailure = false;
        ownershipFailure = false;
        schemaReason = "schema rejected";
        ownershipReason = "ownership rejected";
        initCalls.clear();
        validationCalls.clear();
        writes.clear();
        logs.clear();
        haltWorld = haltCharacter = haltLogin = haltLogs = 0;
        clearCalls = 0;
    }
};

Scenario scenario;

struct FakeConfig
{
    int GetIntDefault(char const* name, int fallback)
    {
        return std::strcmp(name, "RealmID") == 0 ? scenario.realm : fallback;
    }
};

FakeConfig sConfig;

struct FakeLog
{
    void Out(int, int, char const* format, ...)
    {
        char buffer[512];
        va_list args;
        va_start(args, format);
        std::vsnprintf(buffer, sizeof(buffer), format, args);
        va_end(args);
        scenario.logs.push_back(buffer);
    }
};

FakeLog sLog;

struct FakeDatabase
{
    std::string name;
    int halt = 0;

    bool Initialize(char const*, int, int)
    {
        scenario.initCalls.push_back(name);
        return scenario.failStartDB != name;
    }

    bool CheckRequiredMigrations(char const**)
    {
        return scenario.failStartDB != name;
    }

    void HaltDelayThread()
    {
        ++halt;
        if (name == "World")
            ++scenario.haltWorld;
        else if (name == "Character")
            ++scenario.haltCharacter;
        else if (name == "Login")
            ++scenario.haltLogin;
        else if (name == "Logs")
            ++scenario.haltLogs;
    }

    void Execute(char const* sql) { scenario.writes.push_back(sql); }

    template<class... Args>
    void PExecute(char const* sql, Args...)
    {
        scenario.writes.push_back(sql);
    }
};

FakeDatabase WorldDatabase{"World"};
FakeDatabase CharacterDatabase{"Character"};
FakeDatabase LoginDatabase{"Login"};
FakeDatabase LogsDatabase{"Logs"};

char const* MIGRATIONS_WORLD[] = {"world", nullptr};
char const* MIGRATIONS_CHARACTERS[] = {"characters", nullptr};
char const* MIGRATIONS_LOGON[] = {"logon", nullptr};
char const* MIGRATIONS_LOGS[] = {"logs", nullptr};

bool StartDB(std::string const& name, FakeDatabase& database,
             char const** migrations)
{
    return database.Initialize("", 1, 1) &&
           database.CheckRequiredMigrations(migrations);
}

bool CheckCharacterNameSchema(FakeDatabase&, std::string& reason)
{
    scenario.validationCalls.push_back("schema");
    if (scenario.schemaFailure)
    {
        reason = scenario.schemaReason;
        return false;
    }
    return true;
}

bool CheckCharacterNameOwnership(FakeDatabase&, std::string& reason)
{
    scenario.validationCalls.push_back("ownership");
    if (scenario.ownershipFailure)
    {
        reason = scenario.ownershipReason;
        return false;
    }
    return true;
}
}

struct StartupFixture
{
#include "StartupGateBody.inc"
};

namespace
{
bool hasLog(std::string const& needle)
{
    for (std::string const& line : scenario.logs)
        if (line.find(needle) != std::string::npos)
            return true;
    return false;
}

void testInvalidRealm()
{
    scenario.Reset();
    scenario.realm = 0;
    StartupFixture fixture;
    check(!fixture._StartDB(), "invalid realm returns false");
    check(scenario.initCalls.empty(), "invalid realm does not initialize");
    check(scenario.validationCalls.empty(), "invalid realm does not validate");
    check(scenario.writes.empty(), "invalid realm does not write");
    check(scenario.haltWorld + scenario.haltCharacter + scenario.haltLogin +
          scenario.haltLogs == 0, "invalid realm does not halt delay threads");
}

void testInitFailure()
{
    scenario.Reset();
    scenario.failStartDB = "Character";
    StartupFixture fixture;
    check(!fixture._StartDB(), "init failure returns false");
    check(scenario.initCalls.size() == 2 &&
          scenario.initCalls[0] == "World" &&
          scenario.initCalls[1] == "Character", "init short circuits");
    check(scenario.validationCalls.empty(), "init failure does not validate");
    check(scenario.writes.empty(), "init failure does not write");
    check(scenario.haltWorld == 1 && scenario.haltCharacter == 1 &&
          scenario.haltLogin == 1 && scenario.haltLogs == 1,
          "init failure halts all four delay threads");
}

void testSchemaFailure()
{
    scenario.Reset();
    scenario.schemaFailure = true;
    StartupFixture fixture;
    check(!fixture._StartDB(), "schema failure returns false");
    check(scenario.validationCalls.size() == 1 &&
          scenario.validationCalls[0] == "schema",
          "schema failure stops before ownership");
    check(scenario.writes.empty(), "schema failure does not write");
    check(scenario.haltWorld == 1 && scenario.haltCharacter == 1 &&
          scenario.haltLogin == 1 && scenario.haltLogs == 1,
          "schema failure halts all four delay threads");
    check(hasLog(scenario.schemaReason), "schema reason reaches diagnostic");
}

void testOwnershipFailure()
{
    scenario.Reset();
    scenario.ownershipFailure = true;
    StartupFixture fixture;
    check(!fixture._StartDB(), "ownership failure returns false");
    check(scenario.validationCalls.size() == 2 &&
          scenario.validationCalls[0] == "schema" &&
          scenario.validationCalls[1] == "ownership",
          "schema precedes ownership");
    check(scenario.writes.empty(), "ownership failure does not write");
    check(scenario.haltWorld == 1 && scenario.haltCharacter == 1 &&
          scenario.haltLogin == 1 && scenario.haltLogs == 1,
          "ownership failure halts all four delay threads");
    check(hasLog(scenario.ownershipReason), "ownership reason reaches diagnostic");
}

void testSuccess()
{
    scenario.Reset();
    StartupFixture fixture;
    check(fixture._StartDB(), "success returns true");
    check(scenario.validationCalls.size() == 2 &&
          scenario.validationCalls[0] == "schema" &&
          scenario.validationCalls[1] == "ownership",
          "success validates in order");
    check(scenario.initCalls.size() == 4, "success initializes four databases");
    check(scenario.writes.size() == 3, "success writes cleanup exactly three times");
    check(scenario.haltWorld + scenario.haltCharacter + scenario.haltLogin +
          scenario.haltLogs == 0, "success does not halt delay threads");
    check(!scenario.writes.empty() &&
          scenario.writes[0].find("`account`") != std::string::npos &&
          scenario.writes[1].find("`characters`") != std::string::npos &&
          scenario.writes[2].find("`character_battleground_data`") !=
          std::string::npos, "cleanup write targets");
}
}

int main()
{
    testInvalidRealm();
    testInitFailure();
    testSchemaFailure();
    testOwnershipFailure();
    testSuccess();
    std::cout << "Executed " << checks << " checks; " << failures
              << " failure(s)\n";
    return failures == 0 ? 0 : 1;
}
