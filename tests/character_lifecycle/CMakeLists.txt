cmake_minimum_required(VERSION 3.20)
project(character_lifecycle_tests CXX)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(Threads REQUIRED)

add_executable(character_persistence_context_tests
    CharacterPersistenceContextTests.cpp
    "${CMAKE_CURRENT_LIST_DIR}/../../src/game/Database/CharacterPersistenceContext.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/game/Database/CharacterIdentityPersistence.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/game/Database/CharacterStatePersistence.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/game/Database/CharacterPersistenceRecovery.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/shared/Database/SqlCheckedBatch.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/shared/Database/SqlWrite.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/shared/PlayerName.cpp"
)
target_include_directories(character_persistence_context_tests PRIVATE
    "${CMAKE_CURRENT_LIST_DIR}/stubs"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/game"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/shared"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/framework"
    "${CMAKE_CURRENT_LIST_DIR}/../../dep/include"
    "${CMAKE_CURRENT_LIST_DIR}/../../dep/windows/include"
    "${CMAKE_CURRENT_LIST_DIR}/../../dep/windows/include/mysql"
)
target_link_libraries(character_persistence_context_tests PRIVATE Threads::Threads)

enable_testing()
add_test(NAME character_persistence_context
         COMMAND character_persistence_context_tests)

option(FN_ENABLE_NATIVE_MYSQL "Build the opt-in native checked/prepared MySQL test" OFF)
if(FN_ENABLE_NATIVE_MYSQL)
    set(MYSQL_ROOT "${CMAKE_CURRENT_LIST_DIR}/../..")
    add_executable(native_character_persistence_tests
        NativeCharacterPersistenceTests.cpp
        NativeStatementShims.cpp
        "${MYSQL_ROOT}/src/game/Database/CharacterPersistenceContext.cpp"
        "${MYSQL_ROOT}/src/game/Database/CharacterIdentityPersistence.cpp"
        "${MYSQL_ROOT}/src/shared/Database/SqlCheckedBatch.cpp"
        "${MYSQL_ROOT}/src/shared/Database/SqlPreparedWrite.cpp"
        "${MYSQL_ROOT}/src/shared/Database/SqlWrite.cpp"
        "${MYSQL_ROOT}/src/shared/PlayerName.cpp"
    )
    target_include_directories(native_character_persistence_tests PRIVATE
        "${CMAKE_CURRENT_LIST_DIR}/stubs"
        "${MYSQL_ROOT}/src/game"
        "${MYSQL_ROOT}/src/shared"
        "${MYSQL_ROOT}/src/framework"
        "${MYSQL_ROOT}/dep/include"
        "${MYSQL_ROOT}/dep/windows/include"
        "${MYSQL_ROOT}/dep/windows/include/mysql"
    )
    if(MSVC)
        target_compile_options(native_character_persistence_tests PRIVATE /utf-8)
    endif()
    target_link_libraries(native_character_persistence_tests PRIVATE
        "${MYSQL_ROOT}/dep/windows/lib/x64_release/libmySQL.lib"
        ws2_32
        Threads::Threads
    )
    add_custom_command(TARGET native_character_persistence_tests POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${MYSQL_ROOT}/dep/windows/lib/x64_release/libmySQL.dll"
            "$<TARGET_FILE_DIR:native_character_persistence_tests>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${MYSQL_ROOT}/dep/windows/lib/x64_release/libeay32.dll"
            "$<TARGET_FILE_DIR:native_character_persistence_tests>")
endif()
