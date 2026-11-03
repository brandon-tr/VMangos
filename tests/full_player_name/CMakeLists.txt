cmake_minimum_required(VERSION 3.1...3.20)

project(full_player_name_tests CXX)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

enable_testing()

set(PLAYER_NAME_ROOT "${CMAKE_CURRENT_LIST_DIR}/../..")

add_executable(full_player_name_tests
    PlayerNameTests.cpp
    "${PLAYER_NAME_ROOT}/src/shared/PlayerName.cpp"
)

target_include_directories(full_player_name_tests PRIVATE
    "${PLAYER_NAME_ROOT}/src/shared"
    "${PLAYER_NAME_ROOT}/src/framework"
    "${PLAYER_NAME_ROOT}/dep/include"
)

add_test(NAME full_player_name COMMAND full_player_name_tests)

add_executable(player_name_pattern_tests
    PlayerNamePatternTests.cpp
    "${PLAYER_NAME_ROOT}/src/shared/PlayerNamePattern.cpp"
)

target_include_directories(player_name_pattern_tests PRIVATE
    "${PLAYER_NAME_ROOT}/src/shared"
    "${PLAYER_NAME_ROOT}/src/framework"
    "${PLAYER_NAME_ROOT}/dep/include"
)

add_test(NAME player_name_patterns COMMAND player_name_pattern_tests)

add_executable(player_name_lists_tests
    PlayerNameListsTests.cpp
    "${PLAYER_NAME_ROOT}/src/game/Database/PlayerNameLists.cpp"
    "${PLAYER_NAME_ROOT}/src/shared/PlayerName.cpp"
    "${PLAYER_NAME_ROOT}/src/shared/PlayerNamePattern.cpp"
)

target_include_directories(player_name_lists_tests PRIVATE
    "${PLAYER_NAME_ROOT}/src/shared"
    "${PLAYER_NAME_ROOT}/src/framework"
    "${PLAYER_NAME_ROOT}/src/game"
    "${PLAYER_NAME_ROOT}/dep/include"
)

add_test(NAME player_name_lists COMMAND player_name_lists_tests)

add_executable(stored_player_name_tests
    StoredPlayerNameTests.cpp
    "${PLAYER_NAME_ROOT}/src/shared/PlayerName.cpp"
)
target_include_directories(stored_player_name_tests PRIVATE
    "${PLAYER_NAME_ROOT}/src/shared"
    "${PLAYER_NAME_ROOT}/src/framework"
    "${PLAYER_NAME_ROOT}/dep/include"
)
add_test(NAME stored_player_name COMMAND stored_player_name_tests)

add_executable(naming_profile_tests
    NamingProfileTests.cpp
    "${PLAYER_NAME_ROOT}/src/shared/PlayerName.cpp"
)
target_include_directories(naming_profile_tests PRIVATE
    "${PLAYER_NAME_ROOT}/src/shared"
    "${PLAYER_NAME_ROOT}/src/framework"
    "${PLAYER_NAME_ROOT}/dep/include"
)
add_test(NAME naming_profile COMMAND naming_profile_tests)

add_executable(name_parity_probe
    NameParityProbe.cpp
    "${PLAYER_NAME_ROOT}/src/shared/PlayerName.cpp"
)
target_include_directories(name_parity_probe PRIVATE
    "${PLAYER_NAME_ROOT}/src/shared"
    "${PLAYER_NAME_ROOT}/src/framework"
    "${PLAYER_NAME_ROOT}/dep/include"
)
