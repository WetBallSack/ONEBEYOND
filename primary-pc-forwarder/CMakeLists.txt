cmake_minimum_required(VERSION 3.15)
project(hid_forwarder LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# ── Sources ──────────────────────────────────────────────────────────────────
set(SOURCES
    src/main.cpp
    src/config.cpp
    src/network.cpp
    src/mouse_hook.cpp
    src/relay_client.cpp
)

set(HEADERS
    src/protocol.h
    src/config.h
    src/network.h
    src/mouse_hook.h
    src/relay_client.h
)

# ── Executable ───────────────────────────────────────────────────────────────
add_executable(hid_forwarder ${SOURCES} ${HEADERS})

target_compile_definitions(hid_forwarder PRIVATE
    UNICODE
    _UNICODE
    WIN32_LEAN_AND_MEAN
    _CRT_SECURE_NO_WARNINGS
)

target_link_libraries(hid_forwarder PRIVATE
    ws2_32
    user32
    winhttp
)

# ── Copy config.ini next to the binary for convenience ───────────────────────
add_custom_command(TARGET hid_forwarder POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${CMAKE_SOURCE_DIR}/config.ini"
        "$<TARGET_FILE_DIR:hid_forwarder>/config.ini"
    COMMENT "Copying config.ini to output directory"
)
