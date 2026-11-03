#!/usr/bin/env python3
"""Actual login admission callback + real SQueryCallback/DatabaseCallback header."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HANDLER = ROOT / "src/game/Handlers/CharacterHandler.cpp"
HEADER = ROOT / "src/game/Server/WorldSession.h"
RECOVERY = ROOT / "src/game/Database/CharacterPersistenceRecovery.cpp"
SQLOPS = ROOT / "src/shared/Database/SqlOperations.cpp"


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
#include <vector>

// LOGIN_DECL
class SqlQueryHolder {
    public:
        explicit SqlQueryHolder(int* destroyed = nullptr) : m_destroyed(destroyed) {}
        virtual ~SqlQueryHolder() { if (m_destroyed) ++*m_destroyed; }
    private:
        int* m_destroyed;
};
class LoginQueryHolder : public SqlQueryHolder {
    public:
        LoginQueryHolder(uint32 account, int* destroyed = nullptr)
            : SqlQueryHolder(destroyed), m_account(account) {}
        uint32 GetAccountId() const { return m_account; }
    private:
        uint32 m_account;
};
struct WorldSession;
struct World {
    WorldSession* session = nullptr;
    WorldSession* FindSession(uint32) { return session; }
};
World sWorld;

struct WorldSession {
    using LoginAdmissionRequest = ExtractedLoginAdmissionRequest;
    uint32 accountId = 0;
    uint32 nonce = 0;
    int loginCalls = 0;
    std::shared_ptr<AccountLifecycleLease> retainedLease;
    std::shared_ptr<LoginQueryHolder> retainedHolder;
    uint32 GetAccountId() const { return accountId; }
    uint64 GetGUID() const { return nonce; }
    void HandlePlayerLogin(std::shared_ptr<LoginQueryHolder> holder,
                           std::shared_ptr<AccountLifecycleLease> lease);
};
struct CharacterHandler {
    static void HandlePlayerLoginCallback(
        std::unique_ptr<QueryResult>, SqlQueryHolder*,
        WorldSession::LoginAdmissionRequest);
};
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
void WorldSession::HandlePlayerLogin(std::shared_ptr<LoginQueryHolder> holder,
                                     std::shared_ptr<AccountLifecycleLease> lease) {
    ++loginCalls;
    check(lease && lease->IsActive() &&
              GetAccountLifecycleRegistry().Allows(
                  *lease, accountId, AccountLifecycleKind::Mutation),
          "receiving login lease valid");
    retainedLease = std::move(lease);
    retainedHolder = std::move(holder);
}
int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";
    uint32 account = 4010;
    uint32 nonce = 0xABC;
    WorldSession session;
    session.accountId = account; session.nonce = nonce;
    sWorld.session = &session;
    auto lease = GetAccountLifecycleRegistry().TryAcquire(
        {account}, AccountLifecycleKind::Mutation);
    int holderDestroyed = 0;
    auto owned = std::make_shared<LoginQueryHolder>(account, &holderDestroyed);
    LoginQueryHolder* holder = owned.get();
    WorldSession::LoginAdmissionRequest request{nonce, lease, owned};
    auto callback = std::make_unique<MaNGOS::SQueryCallback<
        SqlQueryHolder*, WorldSession::LoginAdmissionRequest>>(
            &CharacterHandler::HandlePlayerLoginCallback, nullptr, holder, request);

    if (scenario == "accepted") {
        lease.reset(); request.lease.reset(); owned.reset(); request.holder.reset();
        check(!admissible(account), "callback alone owns holder and admission");
        callback->Execute();
        check(session.loginCalls == 1 && holderDestroyed == 0, "login accepted once");
        callback.reset();
        check(!admissible(account), "receiving lease excludes after callback destruction");
        session.retainedHolder.reset();
        check(holderDestroyed == 1, "receiving releases holder exactly once");
        session.retainedLease.reset();
        check(admissible(account), "receiving release restores admission");
    } else if (scenario == "replacement") {
        session.nonce = nonce + 1;
        lease.reset(); request.lease.reset();
        callback->Execute();
        owned.reset(); request.holder.reset(); callback.reset();
        check(session.loginCalls == 0 && holderDestroyed == 1,
              "replacement rejects/deletes holder once");
        check(admissible(account), "replacement callback release restores admission");
    } else if (scenario == "missing") {
        sWorld.session = nullptr;
        lease.reset(); request.lease.reset();
        callback->Execute();
        owned.reset(); request.holder.reset(); callback.reset();
        check(session.loginCalls == 0 && holderDestroyed == 1,
              "missing session deletes holder once");
        check(admissible(account), "missing callback release restores admission");
    } else if (scenario == "wrong_kind") {
        callback.reset(); owned.reset(); request.holder.reset(); holderDestroyed = 0;
        lease.reset(); request.lease.reset();
        auto wrong = GetAccountLifecycleRegistry().TryAcquire(
            {account}, AccountLifecycleKind::Deletion);
        check(wrong && wrong->GetKind() == AccountLifecycleKind::Deletion,
              "wrong-kind token acquired");
        owned = std::make_shared<LoginQueryHolder>(account, &holderDestroyed);
        holder = owned.get();
        WorldSession::LoginAdmissionRequest wrongRequest{nonce, wrong, owned};
        auto wrongCallback = std::make_unique<MaNGOS::SQueryCallback<
            SqlQueryHolder*, WorldSession::LoginAdmissionRequest>>(
                &CharacterHandler::HandlePlayerLoginCallback, nullptr,
                holder, wrongRequest);
        lease.reset(); request.lease.reset(); wrong.reset();
        wrongCallback->Execute();
        owned.reset(); wrongRequest.holder.reset(); wrongCallback.reset(); wrongRequest.lease.reset();
        check(session.loginCalls == 0 && holderDestroyed == 1,
              "wrong kind releases holder exactly once");
        check(admissible(account), "wrong kind releases admission");
    } else if (scenario == "foreign_registry") {
        callback.reset(); owned.reset(); request.holder.reset(); holderDestroyed = 0;
        lease.reset(); request.lease.reset();
        AccountLifecycleRegistry other;
        auto foreign = other.TryAcquire({account}, AccountLifecycleKind::Mutation);
        check(foreign && foreign->IsActive(), "foreign token acquired");
        owned = std::make_shared<LoginQueryHolder>(account, &holderDestroyed);
        holder = owned.get();
        WorldSession::LoginAdmissionRequest foreignRequest{nonce, foreign, owned};
        auto foreignCallback = std::make_unique<MaNGOS::SQueryCallback<
            SqlQueryHolder*, WorldSession::LoginAdmissionRequest>>(
                &CharacterHandler::HandlePlayerLoginCallback, nullptr,
                holder, foreignRequest);
        foreign.reset();
        foreignCallback->Execute();
        owned.reset(); foreignRequest.holder.reset(); foreignCallback.reset(); foreignRequest.lease.reset();
        check(session.loginCalls == 0 && holderDestroyed == 1,
              "foreign registry releases holder exactly once");
        check(admissible(account), "global registry usable after foreign rejection");
        auto otherAgain = other.TryAcquire({account}, AccountLifecycleKind::Mutation);
        check(otherAgain != nullptr, "foreign registry usable after rejection");
    } else if (scenario == "mismatch") {
        callback.reset(); owned.reset(); request.holder.reset(); request.lease.reset(); holderDestroyed = 0;
        auto ownedA = std::make_shared<LoginQueryHolder>(account, &holderDestroyed);
        int bDestroyed = 0;
        auto ownedB = std::make_shared<LoginQueryHolder>(account, &bDestroyed);
        WorldSession::LoginAdmissionRequest mismatchRequest{nonce, lease, ownedA};
        auto mismatchCallback = std::make_unique<MaNGOS::SQueryCallback<
            SqlQueryHolder*, WorldSession::LoginAdmissionRequest>>(
                &CharacterHandler::HandlePlayerLoginCallback, nullptr,
                ownedB.get(), mismatchRequest);
        lease.reset(); mismatchRequest.lease.reset();
        mismatchCallback->Execute();
        check(session.loginCalls == 0 && holderDestroyed == 0 && bDestroyed == 0,
              "raw/owned mismatch invokes no login and deletes neither holder");
        mismatchCallback.reset(); mismatchRequest.holder.reset(); ownedA.reset();
        check(holderDestroyed == 1 && bDestroyed == 0,
              "request-owned holder released, unrelated raw untouched");
        ownedA.reset(); ownedB.reset();
        check(bDestroyed == 1, "test-owned unrelated holder cleaned once");
        check(admissible(account), "mismatch releases admission");
    } else if (scenario == "unexecuted") {
        lease.reset(); request.lease.reset();
        callback.reset(); owned.reset(); request.holder.reset();
        check(session.loginCalls == 0, "unexecuted callback no login");
        check(admissible(account), "unexecuted callback releases admission");
        check(holderDestroyed == 1, "unexecuted callback destroys owned holder once");
    } else { std::cerr << "unknown scenario\n"; return 2; }
    if (failures) { std::cerr << "failures=" << failures << "\n"; return 1; }
    std::cout << "login callback " << scenario << ": PASS\n";
    return 0;
}
'''

SCENARIOS = ["accepted", "replacement", "missing", "wrong_kind", "foreign_registry", "mismatch", "unexecuted"]

CANCEL_PREAMBLE = r'''
#include "Database/CharacterPersistenceRecovery.h"
#include "Database/DatabaseCallback.h"
#include <iostream>
#include <memory>
#include <vector>
struct CancelCallback : MaNGOS::IQueryCallback {
    static int destroyed;
    std::shared_ptr<AccountLifecycleLease> lease;
    bool* executed = nullptr;
    CancelCallback(std::shared_ptr<AccountLifecycleLease> value, bool* flag)
        : lease(std::move(value)), executed(flag) {}
    ~CancelCallback() override { ++destroyed; }
    void Execute() override { if (executed) *executed = true; }
    void SetResult(std::unique_ptr<QueryResult>) override {}
    std::unique_ptr<QueryResult>& GetResult() override {
        static std::unique_ptr<QueryResult> result; return result;
    }
};
int CancelCallback::destroyed = 0;
struct CallbackQueue {
    std::vector<MaNGOS::IQueryCallback*> items;
    std::size_t index = 0;
    bool next(MaNGOS::IQueryCallback*& cb) {
        if (index >= items.size()) return false;
        cb = items[index++]; return true;
    }
};
struct SqlResultQueue {
    CallbackQueue ordinary;
    CallbackQueue _threadUnsafeWaitingQueries;
    uint32 numUnsafeQueries = 0;
    bool next(MaNGOS::IQueryCallback*& cb) { return ordinary.next(cb); }
    void CancelAll();
};
'''

CANCEL_MAIN = r'''
static int failures = 0;
static void check(bool value, std::string const& label) {
    if (!value) { std::cerr << "FAIL " << label << "\n"; ++failures; }
}
static bool admissible(uint32 account) {
    return GetAccountLifecycleRegistry().TryAcquire(
        {account}, AccountLifecycleKind::Mutation) != nullptr;
}
int main() {
    bool executed = false;
    auto lease = GetAccountLifecycleRegistry().TryAcquire(
        {5010}, AccountLifecycleKind::Mutation);
    SqlResultQueue queue;
    queue.ordinary.items.push_back(new CancelCallback(lease, &executed));
    lease.reset();
    check(!admissible(5010), "ordinary callback holds admission");
    queue.CancelAll();
    check(!executed && CancelCallback::destroyed == 1,
          "ordinary callback destroyed without execution");
    check(admissible(5010), "ordinary cancellation releases admission");

    bool u1 = false, u2 = false;
    auto l1 = GetAccountLifecycleRegistry().TryAcquire(
        {5011}, AccountLifecycleKind::Mutation);
    auto l2 = GetAccountLifecycleRegistry().TryAcquire(
        {5012}, AccountLifecycleKind::Mutation);
    queue = SqlResultQueue();
    queue._threadUnsafeWaitingQueries.items = {
        new CancelCallback(l1, &u1), new CancelCallback(l2, &u2)};
    queue.numUnsafeQueries = 2;
    l1.reset(); l2.reset();
    queue.CancelAll();
    check(!u1 && !u2 && CancelCallback::destroyed == 3,
          "unsafe callbacks destroyed without execution");
    check(queue.numUnsafeQueries == 0, "unsafe count drained");
    check(admissible(5011) && admissible(5012),
          "unsafe cancellation releases admission");

    queue = SqlResultQueue();
    bool bothOrdinaryExecuted = false, bothUnsafeExecuted = false;
    auto bothBefore = CancelCallback::destroyed;
    auto bothOrdinaryLease = GetAccountLifecycleRegistry().TryAcquire(
        {5013}, AccountLifecycleKind::Mutation);
    auto bothUnsafeLease = GetAccountLifecycleRegistry().TryAcquire(
        {5014}, AccountLifecycleKind::Mutation);
    queue.ordinary.items.push_back(new CancelCallback(bothOrdinaryLease, &bothOrdinaryExecuted));
    queue._threadUnsafeWaitingQueries.items.push_back(
        new CancelCallback(bothUnsafeLease, &bothUnsafeExecuted));
    queue.numUnsafeQueries = 1;
    bothOrdinaryLease.reset(); bothUnsafeLease.reset();
    queue.CancelAll();
    check(!bothOrdinaryExecuted && !bothUnsafeExecuted &&
          CancelCallback::destroyed == bothBefore + 2,
          "ordinary+unsafe callbacks destroyed without execution");
    check(queue.numUnsafeQueries == 0, "combined unsafe count drained");
    check(admissible(5013) && admissible(5014),
          "combined cancellation releases admissions");
    queue = SqlResultQueue();
    bool zeroExecuted = false;
    auto zeroBefore = CancelCallback::destroyed;
    queue._threadUnsafeWaitingQueries.items = {
        new CancelCallback(nullptr, &zeroExecuted)};
    queue.numUnsafeQueries = 0;
    queue.CancelAll();
    check(!zeroExecuted && CancelCallback::destroyed == zeroBefore + 1,
          "zero-counter callback destroyed without execution");
    check(queue.numUnsafeQueries == 0, "unsafe count no underflow");
    if (failures) { std::cerr << "failures=" << failures << "\n"; return 1; }
    std::cout << "cancel all: PASS\n"; return 0;
}
'''


@unittest.skipUnless(shutil.which("cmake"), "cmake not available")
class LoginAdmissionCallbackExecutionTests(unittest.TestCase):
    def test_actual_login_callback(self):
        handler = HANDLER.read_text(encoding="utf-8")
        header = HEADER.read_text(encoding="utf-8")
        signature = "static void HandlePlayerLoginCallback"
        callback = extract(handler, signature).replace(
            signature, "void CharacterHandler::HandlePlayerLoginCallback", 1)
        decl = extract(header, "struct LoginAdmissionRequest")
        decl = decl.replace("struct LoginAdmissionRequest",
                            "struct ExtractedLoginAdmissionRequest", 1) + ";"
        program = PREAMBLE.replace("// LOGIN_DECL\n", "").replace(
        "struct WorldSession {", decl + "\nstruct WorldSession {") + "\n" + callback + "\n" + MAIN
        with tempfile.TemporaryDirectory(prefix="fn05h-v33-login-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(login_callback_exec CXX)\n"
                "set(CMAKE_CXX_STANDARD 14)\n"
                f"add_executable(login probe.cpp \"{RECOVERY.as_posix()}\")\n"
                f"target_include_directories(login PRIVATE "
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
            executable = build / "Release" / "login.exe"
            if not executable.exists():
                executable = build / "login"
            for scenario in SCENARIOS:
                result = subprocess.run([str(executable), scenario],
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0,
                                 scenario + "\n" + result.stdout + result.stderr)
                self.assertIn("login callback " + scenario + ": PASS", result.stdout)


    def test_actual_cancel_all(self):
        source = SQLOPS.read_text(encoding="utf-8")
        body = extract(source, "void SqlResultQueue::CancelAll()")
        program = CANCEL_PREAMBLE + "\n" + body + "\n" + CANCEL_MAIN
        with tempfile.TemporaryDirectory(prefix="fn05h-v33-cancel-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(cancel_all_exec CXX)\n"
                "set(CMAKE_CXX_STANDARD 14)\n"
                f"add_executable(cancel probe.cpp \"{RECOVERY.as_posix()}\")\n"
                f"target_include_directories(cancel PRIVATE "
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
            executable = build / "Release" / "cancel.exe"
            if not executable.exists():
                executable = build / "cancel"
            result = subprocess.run([str(executable)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("cancel all: PASS", result.stdout)


    def test_actual_login_callback_cancelled(self):
        handler = HANDLER.read_text(encoding="utf-8")
        header = HEADER.read_text(encoding="utf-8")
        signature = "static void HandlePlayerLoginCallback"
        callback = extract(handler, signature).replace(
            signature, "void CharacterHandler::HandlePlayerLoginCallback", 1)
        decl = extract(header, "struct LoginAdmissionRequest")
        decl = decl.replace("struct LoginAdmissionRequest",
                            "struct ExtractedLoginAdmissionRequest", 1) + ";"
        cancel_body = extract(SQLOPS.read_text(encoding="utf-8"),
                              "void SqlResultQueue::CancelAll()")
        adapters = r'''
struct CallbackQueue {
    std::vector<MaNGOS::IQueryCallback*> items;
    std::size_t index = 0;
    bool next(MaNGOS::IQueryCallback*& cb) {
        if (index >= items.size()) return false;
        cb = items[index++]; return true;
    }
};
struct SqlResultQueue {
    CallbackQueue ordinary;
    CallbackQueue _threadUnsafeWaitingQueries;
    uint32 numUnsafeQueries = 0;
    bool next(MaNGOS::IQueryCallback*& cb) { return ordinary.next(cb); }
    void CancelAll();
};
'''
        body = r'''
static int failures = 0;
static bool loggedIn = false;
static void check(bool value, std::string const& label) {
    if (!value) { std::cerr << "FAIL " << label << "\n"; ++failures; }
}
static bool admissible(uint32 account) {
    return GetAccountLifecycleRegistry().TryAcquire(
        {account}, AccountLifecycleKind::Mutation) != nullptr;
}
void WorldSession::HandlePlayerLogin(std::shared_ptr<LoginQueryHolder>,
                                     std::shared_ptr<AccountLifecycleLease>) {
    loggedIn = true;
}
int main(int argc, char** argv) {
    std::string mode = argc > 1 ? argv[1] : "ordinary";
    uint32 account = mode == "unsafe" ? 6011 : 6010;
    WorldSession session; session.accountId = account; session.nonce = 0x55;
    sWorld.session = &session;
    auto lease = GetAccountLifecycleRegistry().TryAcquire(
        {account}, AccountLifecycleKind::Mutation);
    int destroyed = 0;
    auto owned = std::make_shared<LoginQueryHolder>(account, &destroyed);
    WorldSession::LoginAdmissionRequest request{0x55, lease, owned};
    auto callback = std::make_unique<MaNGOS::SQueryCallback<
        SqlQueryHolder*, WorldSession::LoginAdmissionRequest>>(
            &CharacterHandler::HandlePlayerLoginCallback, nullptr,
            owned.get(), request);
    SqlResultQueue queue;
    if (mode == "unsafe") {
        queue._threadUnsafeWaitingQueries.items.push_back(callback.release());
        queue.numUnsafeQueries = 1;
    } else {
        queue.ordinary.items.push_back(callback.release());
    }
    lease.reset(); request.lease.reset(); request.holder.reset(); owned.reset();
    check(!admissible(account), "queued login callback holds admission");
    check(destroyed == 0, "queued login callback holds holder");
    queue.CancelAll();
    check(!loggedIn, "cancelled login callback never executes");
    check(destroyed == 1, "cancelled login callback destroys holder once");
    check(queue.numUnsafeQueries == 0, "unsafe count drained");
    check(admissible(account), "cancelled login callback releases admission");
    if (failures) { std::cerr << "failures=" << failures << "\n"; return 1; }
    std::cout << "login cancellation " << mode << ": PASS\n"; return 0;
}
'''
        program = (PREAMBLE.replace("// LOGIN_DECL\n", "").replace(
            "struct WorldSession {", decl + "\nstruct WorldSession {") + "\n" +
            callback + "\n" + adapters + "\n" + cancel_body + "\n" + body)
        with tempfile.TemporaryDirectory(prefix="fn05h-v41-login-cancel-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(login_cancel_exec CXX)\n"
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
            for mode in ["ordinary", "unsafe"]:
                result = subprocess.run([str(executable), mode],
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("login cancellation " + mode + ": PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
