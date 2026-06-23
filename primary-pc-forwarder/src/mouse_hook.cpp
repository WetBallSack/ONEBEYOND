#include "mouse_hook.h"

#include <iostream>

#include "protocol.h"

// ── Static instance pointer ─────────────────────────────────────────────────
MouseHook* MouseHook::g_instance = nullptr;

// ── Logging helpers (use cfg directly) ──────────────────────────────────────

#define LOG_ERR(msg)                                           \
    do {                                                       \
        if (cfg_->log_level >= 1)                              \
            std::cerr << "[HID-FWD] [ERROR] " << msg << '\n'; \
    } while (0)

#define LOG_INFO(msg)                                          \
    do {                                                       \
        if (cfg_->log_level >= 2)                              \
            std::cout << "[HID-FWD] [INFO]  " << msg << '\n'; \
    } while (0)

#define LOG_DBG(msg)                                           \
    do {                                                       \
        if (cfg_->log_level >= 3)                              \
            std::cout << "[HID-FWD] [DEBUG] " << msg << '\n'; \
    } while (0)

// Button-bit constants.
constexpr uint8_t BTN_LEFT   = 0x01;  // bit 0
constexpr uint8_t BTN_RIGHT  = 0x02;  // bit 1
constexpr uint8_t BTN_MIDDLE = 0x04;  // bit 2

// ── Construction / destruction ──────────────────────────────────────────────

MouseHook::MouseHook(NetworkTransmitter* transmitter, const Config* cfg,
                     RelayClient* relay)
    : transmitter_(transmitter), cfg_(cfg), relay_(relay) {}

MouseHook::~MouseHook() { stop(); }

// ── Public API ──────────────────────────────────────────────────────────────

bool MouseHook::start() {
    if (g_instance) {
        LOG_ERR("Only one MouseHook instance may be active");
        return false;
    }
    g_instance = this;

    thread_ = std::thread(&MouseHook::hook_thread_proc, this);
    return true;
}

void MouseHook::stop() {
    if (thread_.joinable()) {
        // Post WM_QUIT to the hook thread's message queue.
        if (thread_id_ != 0) {
            PostThreadMessageW(thread_id_, WM_QUIT, 0, 0);
        }
        thread_.join();
    }
    g_instance = nullptr;
}

// ── Hook thread ─────────────────────────────────────────────────────────────

void MouseHook::hook_thread_proc() {
    thread_id_ = GetCurrentThreadId();

    hook_ = SetWindowsHookExW(WH_MOUSE_LL, LowLevelMouseProc,
                              nullptr, 0);
    if (!hook_) {
        LOG_ERR("SetWindowsHookExW failed: " << GetLastError());
        return;
    }

    LOG_INFO("Low-level mouse hook installed (thread " << thread_id_ << ')');

    // The message loop is mandatory — LL hooks are serviced by the OS via
    // messages dispatched to this thread.
    MSG msg;
    while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }

    UnhookWindowsHookEx(hook_);
    hook_ = nullptr;
    LOG_INFO("Mouse hook uninstalled");
}

// ── Static callback ─────────────────────────────────────────────────────────

LRESULT CALLBACK MouseHook::LowLevelMouseProc(int nCode, WPARAM wParam,
                                               LPARAM lParam) {
    if (nCode < 0 || !g_instance) {
        return CallNextHookEx(nullptr, nCode, wParam, lParam);
    }

    auto* ms = reinterpret_cast<MSLLHOOKSTRUCT*>(lParam);
    return g_instance->handle_mouse(nCode, wParam, ms);
}

// ── Per-event handler ───────────────────────────────────────────────────────

LRESULT MouseHook::handle_mouse(int nCode, WPARAM wParam,
                                 MSLLHOOKSTRUCT* ms) {
    // ── Pass through non-injected (real hardware) input ─────────────────────
    constexpr DWORD INJECTED_FLAGS = LLMHF_INJECTED | LLMHF_LOWER_IL_INJECTED;
    if (!(ms->flags & INJECTED_FLAGS)) {
        return CallNextHookEx(nullptr, nCode, wParam,
                              reinterpret_cast<LPARAM>(ms));
    }

    // ── Injected event — process it ─────────────────────────────────────────
    int16_t dx = 0;
    int16_t dy = 0;
    bool    send = false;

    switch (wParam) {
    // ── Movement ────────────────────────────────────────────────────────
    case WM_MOUSEMOVE: {
        if (cfg_->use_relative_deltas) {
            // Grab current cursor position *before* this injection is
            // applied.  The delta is the injected point minus where the
            // cursor actually is right now.
            POINT cur;
            GetCursorPos(&cur);
            dx = static_cast<int16_t>(ms->pt.x - cur.x);
            dy = static_cast<int16_t>(ms->pt.y - cur.y);
        } else {
            // Absolute mode — forward the raw coordinates as-is.
            dx = static_cast<int16_t>(ms->pt.x);
            dy = static_cast<int16_t>(ms->pt.y);
        }
        send = true;
        break;
    }

    // ── Buttons ─────────────────────────────────────────────────────────
    case WM_LBUTTONDOWN: buttons_ |=  BTN_LEFT;   send = true; break;
    case WM_LBUTTONUP:   buttons_ &= ~BTN_LEFT;   send = true; break;
    case WM_RBUTTONDOWN: buttons_ |=  BTN_RIGHT;  send = true; break;
    case WM_RBUTTONUP:   buttons_ &= ~BTN_RIGHT;  send = true; break;
    case WM_MBUTTONDOWN: buttons_ |=  BTN_MIDDLE; send = true; break;
    case WM_MBUTTONUP:   buttons_ &= ~BTN_MIDDLE; send = true; break;

    default:
        // Unknown or unsupported — pass through.
        return CallNextHookEx(nullptr, nCode, wParam,
                              reinterpret_cast<LPARAM>(ms));
    }

    if (send) {
        HidPacket pkt;
        pkt.dx      = dx;
        pkt.dy      = dy;
        pkt.buttons = buttons_;
        pkt.seq     = seq_.fetch_add(1, std::memory_order_relaxed);
        transmitter_->send_packet(pkt);

        if (relay_ && relay_->is_connected()) {
            relay_->send_packet(pkt);
        }
    }

    // Suppress the injected event so it doesn't reach the local desktop.
    return 1;
}
