#include "network.h"

#include <iostream>

// ── Helpers ─────────────────────────────────────────────────────────────────

#define LOG_ERR(msg)                                          \
    do {                                                      \
        if (log_level_ >= 1)                                  \
            std::cerr << "[HID-FWD] [ERROR] " << msg << '\n'; \
    } while (0)

#define LOG_INFO(msg)                                         \
    do {                                                      \
        if (log_level_ >= 2)                                  \
            std::cout << "[HID-FWD] [INFO]  " << msg << '\n'; \
    } while (0)

#define LOG_DBG(msg)                                          \
    do {                                                      \
        if (log_level_ >= 3)                                  \
            std::cout << "[HID-FWD] [DEBUG] " << msg << '\n'; \
    } while (0)

// ── NetworkTransmitter ──────────────────────────────────────────────────────

NetworkTransmitter::NetworkTransmitter(const std::string& target_ip,
                                       uint16_t target_port, int log_level)
    : target_ip_(target_ip),
      target_port_(target_port),
      log_level_(log_level) {}

NetworkTransmitter::~NetworkTransmitter() { shutdown(); }

bool NetworkTransmitter::init() {
    // 1. Start Winsock ────────────────────────────────────────────────────────
    WSADATA wsa;
    int err = WSAStartup(MAKEWORD(2, 2), &wsa);
    if (err != 0) {
        LOG_ERR("WSAStartup failed: " << err);
        return false;
    }
    wsa_up_ = true;

    // 2. Resolve target address (IPv4 or IPv6) ────────────────────────────────
    addrinfo hints = {};
    hints.ai_family   = AF_UNSPEC;   // Allow both IPv4 and IPv6
    hints.ai_socktype = SOCK_DGRAM;
    hints.ai_protocol = IPPROTO_UDP;

    addrinfo* result = nullptr;
    std::string port_str = std::to_string(target_port_);
    int err2 = getaddrinfo(target_ip_.c_str(), port_str.c_str(), &hints, &result);
    if (err2 != 0 || !result) {
        LOG_ERR("getaddrinfo failed for " << target_ip_ << ": " << gai_strerrorA(err2));
        return false;
    }

    // 3. Create socket matching the resolved address family ──────────────────
    sock_ = socket(result->ai_family, result->ai_socktype, result->ai_protocol);
    if (sock_ == INVALID_SOCKET) {
        freeaddrinfo(result);
        LOG_ERR("socket() failed: " << WSAGetLastError());
        return false;
    }

    // Copy resolved address
    memcpy(&dest_addr_, result->ai_addr, result->ai_addrlen);
    dest_addr_len_ = static_cast<int>(result->ai_addrlen);
    freeaddrinfo(result);

    // 4. Non-blocking mode ───────────────────────────────────────────────────
    u_long nonblock = 1;
    if (ioctlsocket(sock_, FIONBIO, &nonblock) == SOCKET_ERROR) {
        LOG_ERR("ioctlsocket(FIONBIO) failed: " << WSAGetLastError());
        return false;
    }

    // 5. Minimise kernel send buffer for low latency ─────────────────────────
    int sndbuf = 1024;
    setsockopt(sock_, SOL_SOCKET, SO_SNDBUF,
               reinterpret_cast<const char*>(&sndbuf), sizeof(sndbuf));

    LOG_INFO("UDP socket ready -> " << target_ip_ << ':' << target_port_);
    return true;
}

bool NetworkTransmitter::send_packet(const HidPacket& pkt) {
    int sent = sendto(sock_,
                      reinterpret_cast<const char*>(&pkt), sizeof(pkt), 0,
                      reinterpret_cast<const sockaddr*>(&dest_addr_),
                      dest_addr_len_);

    if (sent == SOCKET_ERROR) {
        int err = WSAGetLastError();
        // WSAEWOULDBLOCK is expected on a non-blocking socket — not an error.
        if (err != WSAEWOULDBLOCK) {
            LOG_ERR("sendto failed: " << err);
        }
        return false;
    }

    packets_sent_.fetch_add(1, std::memory_order_relaxed);
    LOG_DBG("TX seq=" << static_cast<int>(pkt.seq)
                      << " dx=" << pkt.dx << " dy=" << pkt.dy
                      << " btn=0x" << std::hex << static_cast<int>(pkt.buttons)
                      << std::dec);
    return true;
}

void NetworkTransmitter::shutdown() {
    if (sock_ != INVALID_SOCKET) {
        closesocket(sock_);
        sock_ = INVALID_SOCKET;
        LOG_INFO("Socket closed");
    }
    if (wsa_up_) {
        WSACleanup();
        wsa_up_ = false;
    }
}
