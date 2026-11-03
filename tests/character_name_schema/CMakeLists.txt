cmake_minimum_required(VERSION 3.20)
project(character_name_schema_tests CXX)

find_package(Python3 COMPONENTS Interpreter REQUIRED)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(character_name_schema_tests CharacterNameSchemaTests.cpp)
target_include_directories(character_name_schema_tests PRIVATE
    "${CMAKE_CURRENT_LIST_DIR}/../../src/game"
)

add_executable(character_name_schema_adapter_tests
    CharacterNameSchemaAdapterTests.cpp
    "${CMAKE_CURRENT_LIST_DIR}/../../src/game/Database/CharacterNameSchema.cpp"
)
target_include_directories(character_name_schema_adapter_tests PRIVATE
    "${CMAKE_CURRENT_LIST_DIR}/stubs"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/game"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/shared"
    "${CMAKE_CURRENT_LIST_DIR}/../../src/framework"
)

set(STARTUP_GENERATED "${CMAKE_CURRENT_BINARY_DIR}/StartupGateBody.inc")
add_custom_command(
    OUTPUT "${STARTUP_GENERATED}"
    COMMAND ${Python3_EXECUTABLE}
            "${CMAKE_CURRENT_LIST_DIR}/startup/GenerateStartupGateFixture.py"
            "${STARTUP_GENERATED}"
    DEPENDS
        "${CMAKE_CURRENT_LIST_DIR}/startup/GenerateStartupGateFixture.py"
        "${CMAKE_CURRENT_LIST_DIR}/../../src/mangosd/Master.cpp"
    VERBATIM
)
add_executable(character_name_startup_gate_tests
    startup/StartupGateTests.cpp
    "${STARTUP_GENERATED}"
)
target_include_directories(character_name_startup_gate_tests PRIVATE
    "${CMAKE_CURRENT_BINARY_DIR}"
)

enable_testing()
add_test(NAME character_name_schema COMMAND character_name_schema_tests)
add_test(NAME character_name_schema_adapter
         COMMAND character_name_schema_adapter_tests)
add_test(NAME character_name_startup_gate
         COMMAND character_name_startup_gate_tests)

