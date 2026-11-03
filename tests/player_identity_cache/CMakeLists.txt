cmake_minimum_required(VERSION 3.20)
project(player_identity_cache_tests CXX)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(Threads REQUIRED)

add_executable(player_identity_cache_tests
    PlayerIdentityCacheTests.cpp
    "${CMAKE_CURRENT_LIST_DIR}/../../src/game/PlayerIdentityCache.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/shared/PlayerName.cpp"
)
target_include_directories(player_identity_cache_tests PRIVATE
    "${CMAKE_CURRENT_LIST_DIR}/stubs"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/game"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/shared"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/framework"
    "${CMAKE_CURRENT_LIST_DIR}/../../dep/include"
)
target_link_libraries(player_identity_cache_tests PRIVATE Threads::Threads)

enable_testing()
add_test(NAME player_identity_cache COMMAND player_identity_cache_tests)
