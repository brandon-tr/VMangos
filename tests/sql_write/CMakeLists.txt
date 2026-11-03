cmake_minimum_required(VERSION 3.20)

project(sql_write_tests CXX)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

enable_testing()

set(SQL_WRITE_ROOT "${CMAKE_CURRENT_LIST_DIR}/../..")

add_executable(sql_write_tests
    SqlWriteTests.cpp
    "${SQL_WRITE_ROOT}/src/shared/Database/SqlWrite.cpp"
    "${SQL_WRITE_ROOT}/src/shared/Database/SqlPreparedWrite.cpp"
)

target_include_directories(sql_write_tests PRIVATE
    "${SQL_WRITE_ROOT}/src/shared"
    "${SQL_WRITE_ROOT}/src/framework"
    "${SQL_WRITE_ROOT}/dep/include"
    "${SQL_WRITE_ROOT}/dep/windows/include"
    "${SQL_WRITE_ROOT}/dep/windows/include/mysql"
)

add_test(NAME sql_write COMMAND sql_write_tests)

add_executable(sql_transaction_tests SqlTransactionTests.cpp)
target_include_directories(sql_transaction_tests PRIVATE
    "${SQL_WRITE_ROOT}/src/shared"
)
add_test(NAME sql_transaction COMMAND sql_transaction_tests)

add_executable(sql_checked_batch_tests
    SqlCheckedBatchTests.cpp
    "${SQL_WRITE_ROOT}/src/shared/Database/SqlCheckedBatch.cpp"
    "${SQL_WRITE_ROOT}/src/shared/Database/SqlWrite.cpp"
)
target_include_directories(sql_checked_batch_tests PRIVATE
    "${SQL_WRITE_ROOT}/src/shared"
    "${SQL_WRITE_ROOT}/src/framework"
    "${SQL_WRITE_ROOT}/dep/include"
    "${SQL_WRITE_ROOT}/dep/windows/include"
    "${SQL_WRITE_ROOT}/dep/windows/include/mysql"
)
add_test(NAME sql_checked_batch COMMAND sql_checked_batch_tests)
