#pragma once

#define WIN32_LEAN_AND_MEAN
#include <Windows.h>
#include <winhttp.h>

#include <atomic>
#include <cstdint>
#include <string>
#include <thread>

#include "protocol.h"

// ── RelayClient ─────────────────────────────────────────────────────────────
// WinHTTP-based WebSocket client for streaming HID packets through a cloud
// relay server.  Maintains a background thread for (re)connection management.
class RelayClient {
public:
    RelayClient(const std::string& url, const std::string& key,
                bool verify_tls, int log_level);
    ~RelayClient();

    RelayClient(const RelayClient&)            = delete;
    RelayClient& operator=(const RelayClient&) = delete;

    // Connect and perform WebSocket upgrade.
    bool init();

    // Send a 7-byte binary frame.
    bool send_packet(const HidPacket& pkt);

    // Tear down connection.
    void shutdown();

    bool     is_connected()  const { return connected_.load(std::memory_order_acquire); }
    uint64_t packets_sent()  const { return packets_sent_.load(std::memory_order_relaxed); }

private:
    void connect_loop();   // Background thread for connection management
    bool do_connect();     // Single connection attempt
    bool send_join();      // Send the join message after WebSocket upgrade
    void close_handles();  // Close all WinHTTP handles

    std::string url_;
    std::string key_;
    bool        verify_tls_;
    int         log_level_;

    HINTERNET session_   = nullptr;
    HINTERNET connect_   = nullptr;
    HINTERNET request_   = nullptr;
    HINTERNET websocket_ = nullptr;

    std::atomic<bool>     connected_{false};
    std::atomic<bool>     running_{false};
    std::atomic<uint64_t> packets_sent_{0};
    std::thread           bg_thread_;

    // Parsed URL components
    std::wstring    host_;
    INTERNET_PORT   port_    = 443;
    std::wstring    path_;
    bool            use_tls_ = true;
};
