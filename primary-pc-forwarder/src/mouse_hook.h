#pragma once

#define WIN32_LEAN_AND_MEAN
#include <Windows.h>

#include <atomic>
#include <cstdint>
#include <thread>

#include "config.h"
#include "network.h"
#include "relay_client.h"

// ── MouseHook ───────────────────────────────────────────────────────────────
// Installs a WH_MOUSE_LL hook on a dedicated thread.  Only injected events
// (LLMHF_INJECTED / LLMHF_LOWER_IL_INJECTED) are captured and forwarded;
// real hardware input is passed through untouched.
class MouseHook {
public:
    MouseHook(NetworkTransmitter* transmitter, const Config* cfg,
              RelayClient* relay = nullptr);
    ~MouseHook();

    // Non-copyable.
    MouseHook(const MouseHook&)            = delete;
    MouseHook& operator=(const MouseHook&) = delete;

    // Spawn the hook thread.
    bool start();

    // Signal the hook thread to exit and join it.
    void stop();

private:
    // Thread entry: installs hook, runs GetMessage loop, unhooks.
    void hook_thread_proc();

    // Win32 hook callback (static — uses g_instance to reach members).
    static LRESULT CALLBACK LowLevelMouseProc(int nCode, WPARAM wParam,
                                              LPARAM lParam);

    // Process an individual mouse message.  Called from the callback.
    LRESULT handle_mouse(int nCode, WPARAM wParam, MSLLHOOKSTRUCT* ms);

    NetworkTransmitter* transmitter_;
    const Config*       cfg_;
    RelayClient*        relay_;

    std::thread         thread_;
    DWORD               thread_id_ = 0;
    HHOOK               hook_      = nullptr;

    // Rolling sequence number (wraps 0-255).
    std::atomic<uint8_t> seq_{0};

    // Current button bitmask (bit0=L, bit1=R, bit2=M).
    uint8_t              buttons_ = 0;

    // Last known cursor position for delta computation.
    POINT                last_pos_ = {0, 0};
    bool                 last_pos_valid_ = false;

    // Global instance pointer — only one MouseHook may be active at a time.
    static MouseHook*    g_instance;
};
