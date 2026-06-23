#define WIN32_LEAN_AND_MEAN
#include <Windows.h>

#include <atomic>
#include <chrono>
#include <filesystem>
#include <iostream>
#include <memory>
#include <string>
#include <thread>
#include <thread>

#include "config.h"
#include "mouse_hook.h"
#include "network.h"
#include "relay_client.h"

// ── Global shutdown flag ────────────────────────────────────────────────────
static std::atomic<bool> g_running{true};
static DWORD             g_main_thread_id = 0;

// ── Console Ctrl handler ────────────────────────────────────────────────────
static BOOL WINAPI ConsoleCtrlHandler(DWORD ctrlType) {
    switch (ctrlType) {
    case CTRL_C_EVENT:
    case CTRL_BREAK_EVENT:
    case CTRL_CLOSE_EVENT:
    case CTRL_LOGOFF_EVENT:
    case CTRL_SHUTDOWN_EVENT:
        g_running.store(false, std::memory_order_release);
        // Wake the main thread's message wait (if any).
        PostThreadMessageW(g_main_thread_id, WM_QUIT, 0, 0);
        return TRUE;
    default:
        return FALSE;
    }
}

// ── Helpers ─────────────────────────────────────────────────────────────────

static std::string exe_directory() {
    wchar_t buf[MAX_PATH];
    GetModuleFileNameW(nullptr, buf, MAX_PATH);
    std::filesystem::path p(buf);
    return p.parent_path().string();
}

static void print_banner(const Config& cfg) {
    std::cout
        << "========================================\n"
        << "  HID Forwarder  (primary-pc-forwarder)\n"
        << "========================================\n"
        << "  Target  : " << cfg.target_ip << ':' << cfg.target_port << '\n'
        << "  Log lvl : " << cfg.log_level << '\n'
        << "  Deltas  : " << (cfg.use_relative_deltas ? "relative" : "absolute") << '\n'
        << "  Relay   : " << (cfg.use_relay ? cfg.relay_url : "disabled") << '\n'
        << "========================================\n"
        << "  Press Ctrl+C to stop.\n"
        << "========================================\n\n";
}

// ── Main ────────────────────────────────────────────────────────────────────

int main() {
    g_main_thread_id = GetCurrentThreadId();

    // 1. Load configuration ──────────────────────────────────────────────────
    std::string exe_dir    = exe_directory();
    std::string config_path = exe_dir + "\\config.ini";
    Config cfg = load_config(config_path);

    print_banner(cfg);

    // 2. Initialise network ──────────────────────────────────────────────────
    NetworkTransmitter net(cfg.target_ip, cfg.target_port, cfg.log_level);
    if (!net.init()) {
        std::cerr << "[HID-FWD] [FATAL] Network initialisation failed.\n";
        return 1;
    }

    // 3. Initialise relay (optional) ─────────────────────────────────────────
    RelayClient* relay_ptr = nullptr;
    std::unique_ptr<RelayClient> relay;
    if (cfg.use_relay && !cfg.relay_url.empty()) {
        relay = std::make_unique<RelayClient>(
            cfg.relay_url, cfg.relay_key,
            cfg.relay_verify_tls, cfg.log_level);
        if (!relay->init()) {
            std::cerr << "[HID-FWD] [WARN] Relay init failed — continuing without relay.\n";
            relay.reset();
        } else {
            relay_ptr = relay.get();
        }
    }

    // 4. Start mouse hook ────────────────────────────────────────────────────
    MouseHook hook(&net, &cfg, relay_ptr);
    if (!hook.start()) {
        std::cerr << "[HID-FWD] [FATAL] Mouse hook failed to start.\n";
        return 1;
    }

    // 5. Install console control handler ─────────────────────────────────────
    SetConsoleCtrlHandler(ConsoleCtrlHandler, TRUE);

    auto start_time = std::chrono::steady_clock::now();

    // 6. Wait until signalled to stop ────────────────────────────────────────
    //    Use a lightweight sleep loop — the main thread has nothing else to do.
    while (g_running.load(std::memory_order_acquire)) {
        Sleep(100);
    }

    // 7. Cleanup ─────────────────────────────────────────────────────────────
    if (cfg.log_level >= 2)
        std::cout << "\n[HID-FWD] [INFO]  Shutting down...\n";

    hook.stop();
    if (relay) relay->shutdown();
    net.shutdown();

    // 8. Print statistics ────────────────────────────────────────────────────
    auto elapsed = std::chrono::steady_clock::now() - start_time;
    auto secs    = std::chrono::duration_cast<std::chrono::seconds>(elapsed).count();

    std::cout << "\n========================================\n"
              << "  Session Statistics\n"
              << "========================================\n"
              << "  UDP packets  : " << net.packets_sent() << '\n';
    if (relay)
        std::cout << "  Relay packets: " << relay->packets_sent() << '\n';
    std::cout << "  Uptime       : " << secs << " seconds\n"
              << "========================================\n";

    return 0;
}
