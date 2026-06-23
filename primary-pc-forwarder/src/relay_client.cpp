#include "relay_client.h"

#include <iostream>
#include <sstream>

#pragma comment(lib, "winhttp.lib")

// ── Logging helpers ─────────────────────────────────────────────────────────

#define LOG_ERR(msg)                                          \
    do {                                                      \
        if (log_level_ >= 1)                                  \
            std::cerr << "[RELAY] [ERROR] " << msg << '\n';   \
    } while (0)

#define LOG_INFO(msg)                                         \
    do {                                                      \
        if (log_level_ >= 2)                                  \
            std::cout << "[RELAY] [INFO]  " << msg << '\n';   \
    } while (0)

#define LOG_DBG(msg)                                          \
    do {                                                      \
        if (log_level_ >= 3)                                  \
            std::cout << "[RELAY] [DEBUG] " << msg << '\n';   \
    } while (0)

// ── Helpers ─────────────────────────────────────────────────────────────────

static std::wstring to_wide(const std::string& s) {
    if (s.empty()) return {};
    int len = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), nullptr, 0);
    std::wstring ws(len, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), &ws[0], len);
    return ws;
}

// Parse a WebSocket URL: wss://host:port/path or ws://host:port/path
static bool parse_ws_url(const std::string& url,
                         std::wstring& host, INTERNET_PORT& port,
                         std::wstring& path, bool& use_tls) {
    std::string u = url;

    // Determine scheme
    if (u.rfind("wss://", 0) == 0) {
        use_tls = true;
        u = u.substr(6);
    } else if (u.rfind("ws://", 0) == 0) {
        use_tls = false;
        u = u.substr(5);
    } else {
        return false;  // unknown scheme
    }

    port = use_tls ? 443 : 80;

    // Split host[:port] from /path
    std::string host_port;
    auto slash = u.find('/');
    if (slash != std::string::npos) {
        host_port = u.substr(0, slash);
        path = to_wide(u.substr(slash));
    } else {
        host_port = u;
        path = L"/";
    }

    // Split host and port — handle IPv6 [addr]:port
    if (!host_port.empty() && host_port[0] == '[') {
        // IPv6 literal
        auto bracket = host_port.find(']');
        if (bracket == std::string::npos) return false;
        host = to_wide(host_port.substr(1, bracket - 1));
        if (bracket + 1 < host_port.size() && host_port[bracket + 1] == ':') {
            port = static_cast<INTERNET_PORT>(std::stoi(host_port.substr(bracket + 2)));
        }
    } else {
        auto colon = host_port.rfind(':');
        if (colon != std::string::npos) {
            host = to_wide(host_port.substr(0, colon));
            port = static_cast<INTERNET_PORT>(std::stoi(host_port.substr(colon + 1)));
        } else {
            host = to_wide(host_port);
        }
    }

    return !host.empty();
}

// ── Construction / Destruction ──────────────────────────────────────────────

RelayClient::RelayClient(const std::string& url, const std::string& key,
                         bool verify_tls, int log_level)
    : url_(url), key_(key), verify_tls_(verify_tls), log_level_(log_level) {}

RelayClient::~RelayClient() { shutdown(); }

// ── Public API ──────────────────────────────────────────────────────────────

bool RelayClient::init() {
    if (!parse_ws_url(url_, host_, port_, path_, use_tls_)) {
        LOG_ERR("Failed to parse relay URL: " << url_);
        return false;
    }

    LOG_INFO("Relay target: " << url_ << " (TLS=" << (use_tls_ ? "yes" : "no")
             << ", port=" << port_ << ")");

    running_.store(true, std::memory_order_release);
    bg_thread_ = std::thread(&RelayClient::connect_loop, this);
    return true;
}

bool RelayClient::send_packet(const HidPacket& pkt) {
    if (!connected_.load(std::memory_order_acquire) || !websocket_)
        return false;

    DWORD err = WinHttpWebSocketSend(
        websocket_, WINHTTP_WEB_SOCKET_BINARY_MESSAGE_BUFFER_TYPE,
        (PVOID)&pkt, sizeof(pkt));

    if (err != NO_ERROR) {
        LOG_ERR("WebSocket send failed: " << err);
        connected_.store(false, std::memory_order_release);
        return false;
    }

    packets_sent_.fetch_add(1, std::memory_order_relaxed);
    LOG_DBG("Relay TX seq=" << static_cast<int>(pkt.seq));
    return true;
}

void RelayClient::shutdown() {
    running_.store(false, std::memory_order_release);

    if (websocket_) {
        WinHttpWebSocketClose(websocket_,
                              WINHTTP_WEB_SOCKET_SUCCESS_CLOSE_STATUS,
                              nullptr, 0);
    }

    close_handles();

    if (bg_thread_.joinable())
        bg_thread_.join();

    connected_.store(false, std::memory_order_release);
    LOG_INFO("Relay client shut down");
}

// ── Private ─────────────────────────────────────────────────────────────────

void RelayClient::close_handles() {
    if (websocket_) { WinHttpCloseHandle(websocket_); websocket_ = nullptr; }
    if (request_)   { WinHttpCloseHandle(request_);   request_   = nullptr; }
    if (connect_)   { WinHttpCloseHandle(connect_);   connect_   = nullptr; }
    if (session_)   { WinHttpCloseHandle(session_);   session_   = nullptr; }
}

bool RelayClient::do_connect() {
    // Clean up any previous handles
    close_handles();

    // 1. Open session
    session_ = WinHttpOpen(L"HID-Bridge/1.0",
                           WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY,
                           WINHTTP_NO_PROXY_NAME,
                           WINHTTP_NO_PROXY_BYPASS, 0);
    if (!session_) {
        LOG_ERR("WinHttpOpen failed: " << GetLastError());
        return false;
    }

    // 2. Connect to host
    connect_ = WinHttpConnect(session_, host_.c_str(), port_, 0);
    if (!connect_) {
        LOG_ERR("WinHttpConnect failed: " << GetLastError());
        close_handles();
        return false;
    }

    // 3. Open request
    DWORD req_flags = use_tls_ ? WINHTTP_FLAG_SECURE : 0;
    request_ = WinHttpOpenRequest(connect_, L"GET", path_.c_str(),
                                  nullptr, WINHTTP_NO_REFERER,
                                  WINHTTP_DEFAULT_ACCEPT_TYPES,
                                  req_flags);
    if (!request_) {
        LOG_ERR("WinHttpOpenRequest failed: " << GetLastError());
        close_handles();
        return false;
    }

    // 4. Optionally disable TLS verification
    if (use_tls_ && !verify_tls_) {
        DWORD flags = SECURITY_FLAG_IGNORE_UNKNOWN_CA |
                      SECURITY_FLAG_IGNORE_CERT_DATE_INVALID |
                      SECURITY_FLAG_IGNORE_CERT_CN_INVALID |
                      SECURITY_FLAG_IGNORE_CERT_WRONG_USAGE;
        WinHttpSetOption(request_, WINHTTP_OPTION_SECURITY_FLAGS,
                         &flags, sizeof(flags));
    }

    // 5. Request WebSocket upgrade
    if (!WinHttpSetOption(request_, WINHTTP_OPTION_UPGRADE_TO_WEB_SOCKET,
                          nullptr, 0)) {
        LOG_ERR("Failed to set WebSocket upgrade option: " << GetLastError());
        close_handles();
        return false;
    }

    // 6. Send the HTTP request
    if (!WinHttpSendRequest(request_, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
                            WINHTTP_NO_REQUEST_DATA, 0, 0, 0)) {
        LOG_ERR("WinHttpSendRequest failed: " << GetLastError());
        close_handles();
        return false;
    }

    // 7. Receive response
    if (!WinHttpReceiveResponse(request_, nullptr)) {
        LOG_ERR("WinHttpReceiveResponse failed: " << GetLastError());
        close_handles();
        return false;
    }

    // 8. Complete the WebSocket upgrade
    websocket_ = WinHttpWebSocketCompleteUpgrade(request_, NULL);
    if (!websocket_) {
        LOG_ERR("WebSocket upgrade failed: " << GetLastError());
        close_handles();
        return false;
    }

    // Request handle is no longer needed after upgrade
    WinHttpCloseHandle(request_);
    request_ = nullptr;

    LOG_INFO("WebSocket connected to relay");
    return true;
}

bool RelayClient::send_join() {
    std::string json = "{\"action\":\"join\",\"key\":\"" + key_ + "\"}";

    DWORD err = WinHttpWebSocketSend(
        websocket_, WINHTTP_WEB_SOCKET_UTF8_MESSAGE_BUFFER_TYPE,
        (PVOID)json.c_str(), (DWORD)json.size());

    if (err != NO_ERROR) {
        LOG_ERR("Failed to send join message: " << err);
        return false;
    }

    LOG_INFO("Sent join message to relay (key=****)");
    return true;
}

void RelayClient::connect_loop() {
    while (running_.load(std::memory_order_acquire)) {
        if (!connected_.load(std::memory_order_acquire)) {
            LOG_INFO("Attempting relay connection...");

            if (do_connect() && send_join()) {
                connected_.store(true, std::memory_order_release);
                LOG_INFO("Relay connection established");
            } else {
                close_handles();
                LOG_INFO("Relay connection failed — retrying in 2s");

                // Sleep in small increments so we can exit promptly
                for (int i = 0; i < 20 && running_.load(std::memory_order_acquire); ++i)
                    Sleep(100);
                continue;
            }
        }

        // Connected — sleep and let send failures trigger reconnection
        Sleep(1000);
    }
}
