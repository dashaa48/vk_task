cmake_minimum_required(VERSION 3.20)
project(msgfmt LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

option(MSGFMT_SANITIZE "Build with -fsanitize=address,undefined" OFF)

add_library(msgfmt_lib STATIC src/msgfmt.cpp)
target_include_directories(msgfmt_lib PUBLIC include)

add_executable(msgfmt src/main.cpp)
target_link_libraries(msgfmt PRIVATE msgfmt_lib)

enable_testing()
add_executable(msgfmt_tests tests/test_msgfmt.cpp)
target_link_libraries(msgfmt_tests PRIVATE msgfmt_lib)
add_test(NAME msgfmt_unit COMMAND msgfmt_tests)
add_test(NAME msgfmt_cli
         COMMAND sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/cli_test.sh $<TARGET_FILE:msgfmt>)

if(NOT MSVC)
  foreach(t msgfmt_lib msgfmt msgfmt_tests)
    target_compile_options(${t} PRIVATE -Wall -Wextra)
    if(MSGFMT_SANITIZE)
      target_compile_options(${t} PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
      target_link_options(${t} PRIVATE -fsanitize=address,undefined)
    endif()
  endforeach()
endif()
