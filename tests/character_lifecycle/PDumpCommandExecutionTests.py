#!/usr/bin/env python3
"""Actual HandlePDumpLoadCommand with fake argument/services and captured LoadDump."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMANDS = ROOT / "src/game/Commands/CharacterCommands.cpp"
CHAT = ROOT / "src/game/Chat/Chat.cpp"
HEADER = ROOT / "src/game/PlayerDump.h"
UTIL = ROOT / "src/shared/Util.h"


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
#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <memory>
#include <string>
#include <vector>

using uint32 = unsigned int;
using uint8 = unsigned char;
using uint64 = unsigned long long;

struct PlayerDumpNamePolicy { uint8 region; std::size_t locale; bool allowReserved; };
// DUMP_RETURN
enum { LANG_COMMAND_IMPORT_SUCCESS, LANG_FILE_OPEN_FAIL, LANG_DUMP_BROKEN,
       LANG_ACCOUNT_CHARACTER_LIST_FULL, LANG_COMMAND_IMPORT_FAILED,
       LANG_INVALID_CHARACTER_GUID, LANG_INVALID_CHARACTER_NAME };

struct WorldSession {
    uint8 region = 2; uint32 locale = 3; int security = 0;
    uint8 GetClientRegion() const { return region; }
    uint32 GetClientLocale() const { return locale; }
    int GetSecurity() const { return security; }
};
enum { SEC_ADMINISTRATOR = 3 };

struct Capture {
    int calls = 0;
    std::string file, name;
    uint32 account = 0, guid = 0;
    PlayerDumpNamePolicy policy{0, 0, false};
    DumpReturn status = DUMP_SUCCESS;
} g_capture;
struct PlayerDumpReader {
    DumpReturn LoadDump(std::string const& file, uint32 account,
                        std::string name, uint32 guid,
                        PlayerDumpNamePolicy const& policy) {
        ++g_capture.calls; g_capture.file = file; g_capture.account = account;
        g_capture.name = name; g_capture.guid = guid; g_capture.policy = policy;
        return g_capture.status;
    }
};

struct ChatHandler {
    WorldSession* session = nullptr;
    std::vector<int> messages;
    bool sentError = false;
    WorldSession* GetSession() const { return session; }
    void SetSentErrorMessage(bool value) { sentError = value; }
    template<typename... A> void PSendSysMessage(int id, A&&...) {
        messages.push_back(id);
    }
    void SkipWhiteSpaces(char** args);
    bool ExtractUInt32Base(char** args, uint32& val, uint32 base);
    bool ExtractUInt32(char** args, uint32& val) { return ExtractUInt32Base(args, val, 10); }
    char* ExtractLiteralArg(char** args, char const* lit = nullptr);
    char* ExtractQuotedArg(char** args, bool asis = false);
    char* ExtractQuotedOrLiteralArg(char** args, bool asis = false);
    uint32 ExtractAccountId(char** args, std::string*);
    bool HandlePDumpLoadCommand(char* args);
};

// WHITESPACE

uint32 ChatHandler::ExtractAccountId(char** args, std::string*) {
    char* token = ExtractLiteralArg(args);
    if (!token) return 0;
    return static_cast<uint32>(std::strtoul(token, nullptr, 10));
}

// PARSER_BODIES
'''

MAIN = r'''
static int failures = 0;
static void check(bool value, std::string const& label) {
    if (!value) { std::cerr << "FAIL " << label << "\n"; ++failures; }
}
static bool run(ChatHandler& handler, std::string const& input,
                DumpReturn status = DUMP_SUCCESS) {
    std::vector<char> buffer(input.begin(), input.end());
    buffer.push_back(char(0));
    buffer.push_back(char(0));
    g_capture = Capture{};
    g_capture.status = status;
    handler.sentError = false;
    handler.messages.clear();
    return handler.HandlePDumpLoadCommand(buffer.data());
}
int main(int argc, char** argv) {
    std::string scenario = argc > 1 ? argv[1] : "";
    ChatHandler handler; WorldSession session; handler.session = &session;
    std::string text;
    if (scenario == "quoted") text = "\"file name.sql\" 7 \"Alice Smith\" 42 --name-profile 1:2";
    else if (scenario == "tab") text = "file.sql 7 --name-profile\t1:2";
    else if (scenario == "fallback") text = "file.sql 7";
    else if (scenario == "console_missing") { handler.session = nullptr; text = "file.sql 7"; }
    else if (scenario == "prefix") text = "file.sql 7 --name-profilex 1:2";
    else if (scenario == "trailing") text = "file.sql 7 --name-profile 1:2 junk";
    else if (scenario == "invalid_profile") text = "file.sql 7 --name-profile 9:2";
    else if (scenario == "empty_name") text = "file.sql 7 \"\"";
    else if (scenario == "zero_guid") text = "file.sql 7 Alice 0";
    else if (scenario == "console_profile") { handler.session = nullptr; text = "file.sql 7 --name-profile 1:2"; }
    else if (scenario == "admin") { session.security = SEC_ADMINISTRATOR; text = "file.sql 7"; }
    else if (scenario == "lower_security") { session.security = 0; text = "file.sql 7"; }
    else if (scenario == "override_profile") { text = "file.sql 7 --name-profile 1:2"; }
    else if (scenario == "missing_value") text = "file.sql 7 --name-profile";
    else if (scenario == "attached_value") text = "file.sql 7 --name-profile1:2";
    else if (scenario == "nonnumeric_guid") text = "file.sql 7 Alice x";
    else if (scenario == "malformed_quote") text = "\"file.sql 7";
    else if (scenario == "attached_equal") text = "file.sql 7 --name-profile=1:2";
    else if (scenario == "quoted_attached") text = "file.sql 7 \"--name-profile1:2\"";
    else if (scenario == "quoted_omitted") text = "file.sql 7 \"Alice Smith\"";
    else if (scenario == "profile_after_name") text = "file.sql 7 \"Alice Smith\" 42 --name-profile 1:2";
    else if (scenario == "mapping") text = "file.sql 7";
    else if (scenario == "whitespace") text = "file.sql 7";
    else { std::cerr << "unknown\n"; return 2; }
    std::string const original = text;
    bool ok = run(handler, original);
    if (scenario == "whitespace") {
        check(isWhiteSpace(' ') && isWhiteSpace('\t') && isWhiteSpace('\n') &&
              isWhiteSpace('\v') && isWhiteSpace('\f') && isWhiteSpace('\r'),
              "production whitespace predicate");
        check(!isWhiteSpace('A') && !isWhiteSpace('\0'), "non-whitespace rejected");
    } else if (scenario == "mapping") {
        DumpReturn bad[] = {DUMP_FILE_OPEN_ERROR, DUMP_TOO_MANY_CHARS,
            DUMP_UNEXPECTED_END, DUMP_FILE_BROKEN, DUMP_DATABASE_ERROR,
            DUMP_IDENTITY_ERROR};
        int expected[] = {LANG_FILE_OPEN_FAIL, LANG_ACCOUNT_CHARACTER_LIST_FULL,
            LANG_COMMAND_IMPORT_FAILED, LANG_DUMP_BROKEN,
            LANG_COMMAND_IMPORT_FAILED, LANG_COMMAND_IMPORT_FAILED};
        for (int i = 0; i < 6; ++i) {
            bool mapped = run(handler, original, bad[i]);
            check(!mapped && g_capture.calls == 1 && handler.sentError &&
                  handler.messages.size() == 1 &&
                  handler.messages[0] == expected[i],
                  "non-success maps false/exact message/error");
        }
        bool mapped = run(handler, original, DUMP_SUCCESS);
        check(mapped && g_capture.calls == 1 && !handler.sentError &&
              handler.messages.size() == 1 &&
              handler.messages[0] == LANG_COMMAND_IMPORT_SUCCESS,
              "success maps true/success message/no error");
    } else if (scenario == "quoted") {
        check(ok && g_capture.calls == 1 && g_capture.file == "file name.sql" &&
              g_capture.name == "Alice Smith" && g_capture.guid == 42 &&
              g_capture.policy.region == 1 && g_capture.policy.locale == 2,
              "quoted file/name/guid/profile");
    } else if (scenario == "tab") {
        check(ok && g_capture.calls == 1 && g_capture.policy.region == 1 &&
              g_capture.policy.locale == 2, "tab profile");
    } else if (scenario == "fallback") {
        check(ok && g_capture.calls == 1 && g_capture.policy.region == 2 &&
              g_capture.policy.locale == 3, "session fallback");
    } else if (scenario == "console_profile") {
        check(ok && g_capture.calls == 1 && g_capture.policy.allowReserved,
              "console explicit profile/reserved");
    } else if (scenario == "admin") {
        check(ok && g_capture.calls == 1 && g_capture.policy.allowReserved,
              "admin reserved authority");
    } else if (scenario == "lower_security") {
        check(ok && g_capture.calls == 1 && !g_capture.policy.allowReserved,
              "lower security no reserved authority");
    } else if (scenario == "override_profile") {
        check(ok && g_capture.calls == 1 && g_capture.policy.region == 1 &&
              g_capture.policy.locale == 2, "explicit profile overrides session");
    } else if (scenario == "console_missing") {
        check(!ok && g_capture.calls == 0, "console missing profile rejected");
    } else if (scenario == "quoted_omitted") {
        check(ok && g_capture.calls == 1 && g_capture.name == "Alice Smith" &&
              g_capture.policy.region == 2 && g_capture.policy.locale == 3,
              "quoted name session profile");
    } else if (scenario == "profile_after_name") {
        check(ok && g_capture.calls == 1 && g_capture.name == "Alice Smith" &&
              g_capture.guid == 42 && g_capture.policy.region == 1 &&
              g_capture.policy.locale == 2, "profile after name/guid");
    } else if (scenario == "missing_value" || scenario == "attached_value" ||
               scenario == "attached_equal" || scenario == "quoted_attached" ||
               scenario == "nonnumeric_guid" || scenario == "malformed_quote" ||
               scenario == "prefix" || scenario == "trailing" ||
               scenario == "invalid_profile" || scenario == "empty_name" ||
               scenario == "zero_guid") {
        check(!ok && g_capture.calls == 0, scenario + " rejected before LoadDump");
    }
    if (failures) { std::cerr << "failures=" << failures << "\n"; return 1; }
    std::cout << "command " << scenario << ": PASS\n"; return 0;
}
'''


@unittest.skipUnless(shutil.which("cmake"), "cmake not available")
class PDumpCommandExecutionTests(unittest.TestCase):
    def test_actual_command(self):
        source = COMMANDS.read_text(encoding="utf-8")
        chat = CHAT.read_text(encoding="utf-8")
        header = HEADER.read_text(encoding="utf-8")
        util = UTIL.read_text(encoding="utf-8")
        whitespace = extract(util, "inline bool isWhiteSpace")
        dump_return = extract(header, "enum DumpReturn") + ";"
        command = extract(source, "bool ChatHandler::HandlePDumpLoadCommand")
        option = extract(source, "static bool IsExactOptionToken")
        profile = extract(source, "static bool ParseNameProfile")
        parsers = "\n".join(extract(chat, sig) for sig in [
            "void ChatHandler::SkipWhiteSpaces",
            "bool  ChatHandler::ExtractUInt32Base",
            "char* ChatHandler::ExtractLiteralArg(char** args, char const* lit /*= nullptr*/)",
            "char* ChatHandler::ExtractQuotedArg(char** args, bool asis /*= false*/)",
            "char* ChatHandler::ExtractQuotedOrLiteralArg(char** args, bool asis /*= false*/)"])
        program = (PREAMBLE.replace("// DUMP_RETURN", dump_return)
                   .replace("// WHITESPACE", whitespace)
                   .replace("// PARSER_BODIES", parsers) + "\n" +
                   option + "\n" + profile + "\n" + command + "\n" + MAIN)
        with tempfile.TemporaryDirectory(prefix="fn05h-v46-command-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(command_exec CXX)\n"
                "set(CMAKE_CXX_STANDARD 14)\n"
                "add_executable(probe probe.cpp)\n",
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
            failures = []
            for scenario in ["quoted", "tab", "fallback", "console_missing",
                             "prefix", "trailing", "invalid_profile",
                             "empty_name", "zero_guid", "console_profile",
                             "admin", "lower_security", "override_profile",
                             "missing_value", "attached_value", "attached_equal",
                             "quoted_attached", "nonnumeric_guid",
                             "malformed_quote", "quoted_omitted",
                             "profile_after_name", "mapping", "whitespace"]:
                result = subprocess.run([str(executable), scenario],
                                        text=True, capture_output=True, timeout=30)
                if result.returncode != 0 or (
                        "command " + scenario + ": PASS") not in result.stdout:
                    failures.append(scenario + "\n" + result.stdout + result.stderr)
            self.assertFalse(failures, "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
