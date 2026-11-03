#!/usr/bin/env python3
"""Actual login scheduling entries with owned request and actual callback."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HANDLER = ROOT / "src/game/Handlers/CharacterHandler.cpp"
HEADER = ROOT / "src/game/Server/WorldSession.h"
RECOVERY = ROOT / "src/game/Database/CharacterPersistenceRecovery.cpp"


def extract(source, signature):
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError("unterminated " + signature)


PREAMBLE = r'''
#include "Database/CharacterPersistenceRecovery.h"
#include "Database/DatabaseCallback.h"
#include <iostream>
#include <memory>
#include <string>

using uint32 = unsigned int;
using uint64 = unsigned long long;

int g_destroyed = 0;
struct ObjectGuid {
    uint32 value = 0;
    ObjectGuid() = default;
    explicit ObjectGuid(uint32 v) : value(v) {}
    bool IsPlayer() const { return true; }
    uint32 GetCounter() const { return value; }
};
struct SqlQueryHolder {
    int* destroyed; uint32 size = 0;
    explicit SqlQueryHolder(uint32, int* d = nullptr) : destroyed(d) {}
    virtual ~SqlQueryHolder() { if (destroyed) ++*destroyed; }
};
struct LoginQueryHolder : SqlQueryHolder {
    uint32 account; ObjectGuid guid; int* destroyed;
    LoginQueryHolder(uint32 a, ObjectGuid g, int* d = &g_destroyed)
        : SqlQueryHolder(a, d), account(a), guid(g), destroyed(d) {}
    bool Initialize();
    ObjectGuid GetGuid() const { return guid; }
    uint32 GetAccountId() const { return account; }
};
bool g_initOk = true, g_scheduleOk = true;
int g_allocated = 0;
bool g_loggedIn = false;
std::unique_ptr<MaNGOS::IQueryCallback> g_callback;
SqlQueryHolder* g_raw = nullptr;
std::shared_ptr<LoginQueryHolder> g_requestHolder;

bool LoginQueryHolder::Initialize() { ++g_allocated; return g_initOk; }
// LOGIN_DECL

namespace WorldPackets { namespace Character {
struct PlayerLogin { ObjectGuid guid; };
struct CharacterLoginFailed { int result = 0; };
}}

struct World {
    int config = 1;
    int getConfig(int) { return config; }
} sWorld;
enum { CONFIG_BOOL_WORLD_AVAILABLE = 1 };
#define ASSERT(x) do { } while (0)

struct WorldSession {
    using LoginAdmissionRequest = ExtractedLoginAdmissionRequest;
    uint32 account = 9010;
    uint32 sessionGuid = 0x99;
    bool loading = false;
    int packets = 0;
    uint32 GetAccountId() const { return account; }
    uint32 GetGUID() const { return sessionGuid; }
    bool PlayerLoading() const { return loading; }
    void* GetPlayer() const { return nullptr; }
    int GetSecurity() const { return SEC_PLAYER; }
    template<typename T> void SendPacket(std::unique_ptr<T>) { ++packets; }
    void HandlePlayerLoginOpcode(WorldPackets::Character::PlayerLogin const& packet);
    void LoginPlayer(ObjectGuid guid);
    bool m_playerLoading = false;
};

struct CharacterHandler {
    static void HandlePlayerLoginCallback(std::unique_ptr<QueryResult>,
        SqlQueryHolder*, WorldSession::LoginAdmissionRequest);
};

struct Database {
    template<typename Request>
    bool DelayQueryHolderUnsafe(
        void (*fn)(std::unique_ptr<QueryResult>, SqlQueryHolder*, Request),
        SqlQueryHolder* raw, Request request) {
        if (!g_scheduleOk) { g_raw = nullptr; g_requestHolder.reset(); return false; }
        g_raw = raw; g_requestHolder = request.holder;
        if (raw != request.holder.get()) { std::cerr << "raw mismatch\n"; return false; }
        g_callback.reset(new MaNGOS::SQueryCallback<SqlQueryHolder*, Request>(
            fn, nullptr, raw, request));
        return true;
    }
};
Database CharacterDatabase;
'''

MAIN = r'''
static int failures = 0;
static void check(bool value, std::string const& label) {
    if (!value) { std::cerr << "FAIL " << label << "\n"; ++failures; }
}
static bool admissible(uint32 account) {
    return GetAccountLifecycleRegistry().TryAcquire(
        {account}, AccountLifecycleKind::Mutation) != nullptr;
}
void CharacterHandler::HandlePlayerLoginCallback(
    std::unique_ptr<QueryResult>, SqlQueryHolder*,
    WorldSession::LoginAdmissionRequest) { g_loggedIn = true; }
int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";
    WorldSession session;
    WorldPackets::Character::PlayerLogin packet{ObjectGuid(901)};
    bool opcode = scenario.rfind("opcode_", 0) == 0;
    bool accepted = false;
    AccountLifecycleLeasePtr pre;
    if (scenario.find("busy") != std::string::npos)
        pre = GetAccountLifecycleRegistry().TryAcquire(
            {session.account}, AccountLifecycleKind::Mutation);
    if (scenario.find("init_false") != std::string::npos) g_initOk = false;
    if (scenario.find("schedule_false") != std::string::npos) g_scheduleOk = false;

    if (opcode) session.HandlePlayerLoginOpcode(packet);
    else session.LoginPlayer(ObjectGuid(901));

    if (scenario.find("busy") != std::string::npos) {
        check(g_allocated == 0 && g_destroyed == 0 && !g_callback && pre != nullptr,
              "busy no allocation/schedule");
    } else if (scenario.find("init_false") != std::string::npos) {
        check(g_allocated == 1 && g_destroyed == 1 && !g_callback, "init false one holder no schedule");
        check(admissible(session.account), "init false releases admission");
    } else if (scenario.find("schedule_false") != std::string::npos) {
        check(g_allocated == 1 && g_destroyed == 1 && !g_callback && !session.m_playerLoading,
              "schedule false holder destroyed/loading false");
        check(admissible(session.account), "schedule false releases admission");
    } else if (scenario.find("accepted") != std::string::npos) {
        check(g_callback != nullptr && g_raw == g_requestHolder.get(),
              "accepted callback owns raw view");
        check(!admissible(session.account), "accepted holds admission");
        g_requestHolder.reset(); g_raw = nullptr;
        check(g_destroyed == 0, "callback still owns holder");
        g_callback.reset();
        check(g_destroyed == 1, "cancel destroys holder once");
        check(admissible(session.account), "cancel releases admission");
    }
    if (failures) { std::cerr << "failures=" << failures << "\n"; return 1; }
    std::cout << "scheduling " << scenario << ": PASS\n";
    return 0;
}
'''


@unittest.skipUnless(shutil.which("cmake"), "cmake not available")
class LoginSchedulingExecutionTests(unittest.TestCase):
    def test_actual_entries(self):
        cpp = HANDLER.read_text(encoding="utf-8")
        header = HEADER.read_text(encoding="utf-8")
        decl = extract(header, "struct LoginAdmissionRequest")
        decl = decl.replace("struct LoginAdmissionRequest",
                            "struct ExtractedLoginAdmissionRequest", 1) + ";"
        opcode = extract(cpp, "void WorldSession::HandlePlayerLoginOpcode")
        login = extract(cpp, "void WorldSession::LoginPlayer")
        program = (PREAMBLE.replace("// LOGIN_DECL\n", "")
                   .replace("// LOGIN_DECL", decl)
                   .replace("struct WorldSession {", decl + "\nstruct WorldSession {"))
        # Inject declaration before WorldSession so shared_ptr type is visible.
        program = PREAMBLE.replace("// LOGIN_DECL", decl) + "\n" + opcode + "\n" + login + "\n" + MAIN
        with tempfile.TemporaryDirectory(prefix="fn05h-v40-sched-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(sched_exec CXX)\n"
                "set(CMAKE_CXX_STANDARD 14)\n"
                f"add_executable(probe probe.cpp \"{RECOVERY.as_posix()}\")\n"
                f"target_include_directories(probe PRIVATE "
                f"\"{(ROOT / 'src/game').as_posix()}\" "
                f"\"{(ROOT / 'src/shared').as_posix()}\" "
                f"\"{(ROOT / 'src/framework').as_posix()}\")\n",
                encoding="utf-8")
            (temp / "probe.cpp").write_text(program, encoding="utf-8")
            build = temp / "build"
            subprocess.run(["cmake", "-S", str(temp), "-B", str(build),
                            "-DCMAKE_BUILD_TYPE=Release"], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            built = subprocess.run(["cmake", "--build", str(build),
                                    "--config", "Release", "--parallel", "2"],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True)
            self.assertEqual(built.returncode, 0, built.stdout)
            executable = build / "Release" / "probe.exe"
            if not executable.exists():
                executable = build / "probe"
            cases = []
            for entry in ["opcode", "login"]:
                for kind in ["busy", "init_false", "schedule_false", "accepted"]:
                    cases.append(entry + "_" + kind)
            for scenario in cases:
                result = subprocess.run([str(executable), scenario],
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0,
                                 scenario + "\n" + result.stdout + result.stderr)
                self.assertIn("scheduling " + scenario + ": PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
