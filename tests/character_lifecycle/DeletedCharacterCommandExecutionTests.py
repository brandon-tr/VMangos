import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMANDS = ROOT / "src" / "game" / "Commands" / "CharacterCommands.cpp"
CHAT = ROOT / "src" / "game" / "Chat" / "Chat.cpp"


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


def functions():
    commands = COMMANDS.read_text(encoding="utf-8")
    chat = CHAT.read_text(encoding="utf-8")
    return {
        "skip": extract(chat, "void ChatHandler::SkipWhiteSpaces(char** args)"),
        "literal": extract(chat, "char* ChatHandler::ExtractLiteralArg(char** args"),
        "quoted": extract(chat, "char* ChatHandler::ExtractQuotedArg(char** args"),
        "either": extract(chat, "char* ChatHandler::ExtractQuotedOrLiteralArg(char** args"),
        "parse_guid": extract(commands, "static bool ParseDeletedCharacterGuid"),
        "parse_profile": extract(commands, "static bool ParseNameProfile"),
        "restore": extract(commands, "bool ChatHandler::HandleCharacterDeletedRestoreCommand"),
        "delete": extract(commands, "bool ChatHandler::HandleCharacterDeletedDeleteCommand"),
        "list": extract(commands, "bool ChatHandler::HandleCharacterDeletedListCommand"),
    }


PREAMBLE = r'''
#include <string>
#include <vector>
#include <cstring>
#include <cctype>
#include <cstdlib>
#include <algorithm>
#include <iostream>
using uint32=unsigned int; using uint64=unsigned long long; using int32=int; using uint8=unsigned char;
bool isWhiteSpace(char c){return std::isspace(static_cast<unsigned char>(c))!=0;}
bool normalizeFullPlayerName(std::string&){return true;}
bool isCanonicalFullPlayerName(std::string const&){return true;}
bool normalizeFullPlayerNameForLookup(std::string&){return true;}
struct PlayerNameCharsets { uint8 primary=1, secondary=1; static PlayerNameCharsets ForClient(int,std::size_t){return {};} };
struct PlayerNameLists {};
PlayerNameLists const& GetPlayerNameLists(){static PlayerNameLists lists;return lists;}
constexpr int CHAR_NAME_SUCCESS=0x52;
uint8 prepareFullPlayerName(std::string& name,PlayerNameLists const&,PlayerNameCharsets){return name.find(' ')!=std::string::npos?CHAR_NAME_SUCCESS:0x48;}
struct WorldSession { uint8 GetClientRegion()const{return 1;} int GetClientLocale()const{return 0;} };
struct ObjectGuid { ObjectGuid(){} ObjectGuid(int,uint32){} };
enum { HIGHGUID_PLAYER=0 };
constexpr int LANG_CHARACTER_DELETED_LIST_EMPTY=1;
constexpr int LANG_CHARACTER_DELETED_ERR_RENAME=2;
constexpr int LANG_CHARACTER_DELETED_RESTORE=3;
constexpr int LANG_CHARACTER_DELETED_DELETE=4;
struct Accounts {void GetName(uint32,std::string&){};} sAccountMgr;
int g_queries=0, g_restores=0, g_deletes=0, g_matches=1, g_success=0;
bool g_helper_ok=true;
bool g_delete_ok=true;
std::string g_search;
struct CharacterDeletionSnapshot { struct { uint32 guid=0; uint32 accountId=0; std::string canonicalName; uint64 generation=0; } identity; uint64 deletedTime=0; };
struct Player { static bool DeleteFromDB(ObjectGuid,uint32,bool,bool,CharacterDeletionSnapshot const*){if(!g_delete_ok)return false;++g_deletes;return true;} };
struct ChatHandler {
 WorldSession session;
 WorldSession* sessionPtr=&session;
 WorldSession* GetSession(){return sessionPtr;}
 struct DeletedInfo {uint32 lowguid=1;std::string name="Alice Smith",originalName="Alice Smith";uint32 accountId=7,originalAccountId=7;uint64 originalGeneration=1,originalDeletedTime=1;std::string accountName="test";};
 using DeletedInfoList=std::vector<DeletedInfo>;
 static void SkipWhiteSpaces(char**);
 char* ExtractLiteralArg(char**,char const* lit=nullptr);
 char* ExtractQuotedArg(char**,bool=false);
 char* ExtractQuotedOrLiteralArg(char**,bool=false);
 bool ExtractOptInt32(char** args,int32& val,int32){if(!args||!*args||!**args)return false;char* end=nullptr;long v=strtol(*args,&end,10);if(end==*args||(*end&&!isWhiteSpace(*end)))return false;val=int32(v);*args=end;SkipWhiteSpaces(args);return true;}
 bool GetDeletedCharacterInfoList(DeletedInfoList& list,bool,std::string text){++g_queries;g_search=text;list.assign(g_matches,DeletedInfo{});return true;}
 void SendSysMessage(int id){if(id==LANG_CHARACTER_DELETED_RESTORE)++g_success;}
 void HandleCharacterDeletedListHelper(DeletedInfoList const&){}
 bool HandleCharacterDeletedRestoreHelper(DeletedInfo const&){++g_restores;return g_helper_ok;}
 bool HandleCharacterDeletedRestoreCommand(char*);
 bool HandleCharacterDeletedDeleteCommand(char*);
 bool HandleCharacterDeletedListCommand(char*,bool);
};
'''

MAIN = r'''
static std::vector<char> buffer(std::string const& text)
{
    std::vector<char> value(text.begin(), text.end());
    value.push_back(0); value.push_back(0);
    return value;
}
static int expect(std::string const& label, bool actual, bool wanted, int queries, int effects)
{
    if (actual != wanted || g_queries != queries || g_restores + g_deletes != effects)
    {
        std::cerr << "FAIL " << label << " actual=" << actual
                  << " queries=" << g_queries << " effects=" << (g_restores+g_deletes) << "\n";
        return 1;
    }
    return 0;
}
static void reset(int matches=1){g_queries=g_restores=g_deletes=g_success=0;g_search.clear();g_matches=matches;g_delete_ok=true;g_helper_ok=true;}
int main(){
    int fail=0;
    for (std::string input : {std::string("\"\""),std::string("''"),std::string("[]"),
                              std::string("\"\" \"Bob Jones\""),std::string("'' \"Bob Jones\""),
                              std::string("[] \"Bob Jones\"")})
    {
        reset(); ChatHandler h; auto b=buffer(input);
        fail += expect("restore empty "+input, h.HandleCharacterDeletedRestoreCommand(b.data()),
                       false, 0, 0);
    }
    reset(); {ChatHandler h; auto b=buffer(""); fail += expect("restore empty literal", h.HandleCharacterDeletedRestoreCommand(b.data()), false, 0, 0);}
    reset(); {ChatHandler h; auto b=buffer("\"Alice"); fail += expect("malformed quote", h.HandleCharacterDeletedRestoreCommand(b.data()), false, 0, 0);}
    for (std::string input : {std::string("\"\""),std::string("''"),std::string("[]")})
    {
        reset(); ChatHandler h; auto b=buffer(input);
        fail += expect("delete empty "+input, h.HandleCharacterDeletedDeleteCommand(b.data()), false, 0, 0);
    }
    uint8 profileRegion = 0; std::size_t profileLocale = 0;
    if (!ParseNameProfile("5:7", profileRegion, profileLocale) ||
        profileRegion != 5 || profileLocale != 7) { std::cerr << "FAIL profile boundary\\n"; ++fail; }
    if (ParseNameProfile("6:0", profileRegion, profileLocale) ||
        ParseNameProfile("0:8", profileRegion, profileLocale) ||
        ParseNameProfile("999999999999999999999999999999:0", profileRegion, profileLocale) ||
        ParseNameProfile("0:999999999999999999999999999999", profileRegion, profileLocale))
    { std::cerr << "FAIL profile overflow/bounds\\n"; ++fail; }
    reset(1); {ChatHandler h; auto b=buffer("1"); fail += expect("restore guid one", h.HandleCharacterDeletedRestoreCommand(b.data()), true, 1, 1);}
    reset(1); {ChatHandler h; auto b=buffer("1 --name-profile 1:0"); fail += expect("restore explicit profile", h.HandleCharacterDeletedRestoreCommand(b.data()), true, 1, 1);}
    reset(1); {ChatHandler h; auto b=buffer("1 --name-profile 9:0"); fail += expect("restore invalid profile", h.HandleCharacterDeletedRestoreCommand(b.data()), false, 0, 0);}
    reset(1); {ChatHandler h; h.sessionPtr=nullptr; auto b=buffer("1"); fail += expect("restore console needs profile", h.HandleCharacterDeletedRestoreCommand(b.data()), false, 0, 0);}
    reset(1); {ChatHandler h; h.sessionPtr=nullptr; auto b=buffer("1 --name-profile 0:0"); fail += expect("restore console explicit profile", h.HandleCharacterDeletedRestoreCommand(b.data()), true, 1, 1);}
    for (int matches : {0,2})
    {
        reset(matches); ChatHandler h; auto b=buffer("1");
        fail += expect("restore guid ambiguous", h.HandleCharacterDeletedRestoreCommand(b.data()), false, 1, 0);
    }
    reset(1); g_delete_ok=false; {ChatHandler h; auto b=buffer("1"); fail += expect("delete failure truthful", h.HandleCharacterDeletedDeleteCommand(b.data()), false, 1, 0);}
    reset(1); {ChatHandler h; auto b=buffer("1"); fail += expect("delete success scoped", h.HandleCharacterDeletedDeleteCommand(b.data()), true, 1, 1);}
    reset(1); g_helper_ok=false; {ChatHandler h; auto b=buffer("1");
        bool ok=h.HandleCharacterDeletedRestoreCommand(b.data());
        if(ok || g_restores!=1 || g_success!=0){std::cerr<<"FAIL helper false suppresses success\\n";++fail;}}
    reset(1); {ChatHandler h; auto b=buffer("1");
        bool ok=h.HandleCharacterDeletedRestoreCommand(b.data());
        if(!ok || g_restores!=1 || g_success!=1){std::cerr<<"FAIL helper true emits success once\\n";++fail;}}
    reset(2); {ChatHandler h; auto b=buffer("1"); fail += expect("delete ambiguous", h.HandleCharacterDeletedDeleteCommand(b.data()), false, 1, 0);}
    reset(1); {ChatHandler h; auto b=buffer("1 2"); fail += expect("delete trailing", h.HandleCharacterDeletedDeleteCommand(b.data()), false, 0, 0);}
    uint32 guid=0;
    fail += expect("guid empty", ParseDeletedCharacterGuid("",guid), false, 0, 0);
    fail += expect("guid zero", ParseDeletedCharacterGuid("0",guid), false, 0, 0);
    fail += expect("guid overflow", ParseDeletedCharacterGuid("4294967296",guid), false, 0, 0);
    fail += expect("guid huge", ParseDeletedCharacterGuid("999999999999999999999999999",guid), false, 0, 0);
    if (!ParseDeletedCharacterGuid("1",guid) || guid!=1) {std::cerr<<"FAIL guid one value\n";++fail;}
    if (!ParseDeletedCharacterGuid("4294967295",guid) || guid!=4294967295u) {std::cerr<<"FAIL guid max value\n";++fail;}
    reset(1); {ChatHandler h; auto b=buffer(""); fail += expect("list empty", h.HandleCharacterDeletedListCommand(b.data(),true), true, 1, 0);}
    std::cout << "failures=" << fail << "\n";
    return fail ? 1 : 0;
}
'''


def probe_source(fn, prefix):
    restore = fn["restore"]
    if prefix:
        needle = "if (!searchArg || !*searchArg)"
        if needle not in restore:
            raise AssertionError("fixed guard not found")
        restore = restore.replace(needle, "if (!searchArg)", 1)
    return (PREAMBLE + fn["skip"] + "\n" + fn["literal"] + "\n" +
            fn["quoted"] + "\n" + fn["either"] + "\n" + fn["parse_guid"] + "\n" +
            fn["parse_profile"] + "\n" +
            restore + "\n" + fn["delete"] + "\n" + fn["list"] + "\n" + MAIN)


@unittest.skipUnless(shutil.which("cmake"), "cmake not available")
class DeletedCommandExecutionTests(unittest.TestCase):
    def test_empty_source_and_parser_behavior(self):
        fn = functions()
        with tempfile.TemporaryDirectory(prefix="fn05h-v19-") as temp:
            temp = Path(temp)
            (temp / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(deleted_command_exec CXX)\n"
                "set(CMAKE_CXX_STANDARD 14)\n"
                "add_executable(current probe_current.cpp)\n"
                "add_executable(prefix probe_prefix.cpp)\n", encoding="utf-8")
            (temp / "probe_current.cpp").write_text(probe_source(fn, False), encoding="utf-8")
            (temp / "probe_prefix.cpp").write_text(probe_source(fn, True), encoding="utf-8")
            build = temp / "build"
            subprocess.run(["cmake", "-S", str(temp), "-B", str(build),
                            "-DCMAKE_BUILD_TYPE=Release"], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            built = subprocess.run(["cmake", "--build", str(build), "--config", "Release",
                            "--parallel", "2"], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)
            self.assertEqual(built.returncode, 0, built.stdout)
            current = build / "Release" / "current.exe"
            prefix = build / "Release" / "prefix.exe"
            if not current.exists():
                current = build / "current"
                prefix = build / "prefix"
            after = subprocess.run([str(current)], text=True, capture_output=True)
            before = subprocess.run([str(prefix)], text=True, capture_output=True)
            self.assertEqual(after.returncode, 0, after.stdout + after.stderr)
            self.assertNotEqual(before.returncode, 0,
                                "pre-fix fixture unexpectedly passed: " + before.stdout)


if __name__ == "__main__":
    unittest.main()
