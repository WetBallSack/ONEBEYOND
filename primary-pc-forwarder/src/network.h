#pragma once

#define WIN32_LEAN_AND_MEAN
#include <WinSock2.h>
#include <ws2tcpip.h>

#include <atomic>
#include <cstdint>
#include <string>

#include "protocol.h"

// ── NetworkTransmitter ──────────────────────────────────────────────────────
// Thin Winsock2 UDP sender.  Designed to be called directly from the hook
// callback — sendto() for 7 bytes is non-blocking and atomic.
class NetworkTransmitter {
public:
    NetworkTransmitter(const std::string& target_ip, uint16_t target_port,
                       int log_level);
    ~NetworkTransmitter();

    // Non-copyable, non-movable.
    NetworkTransmitter(const NetworkTransmitter&)            = delete;
    NetworkTransmitter& operator=(const NetworkTransmitter&) = delete;

    // Initialise Winsock and create the socket.  Returns false on failure.
    bool init();

    // Send a raw HidPacket.  Returns true on success.
    bool send_packet(const HidPacket& pkt);

    // Tear everything down.
    void shutdown();

    // Statistics.
    uint64_t packets_sent() const { return packets_sent_.load(std::memory_order_relaxed); }

private:
    std::string       target_ip_;
    uint16_t          target_port_;
    int               log_level_;

    SOCKET            sock_       = INVALID_SOCKET;
    sockaddr_storage  dest_addr_  = {};
    int               dest_addr_len_ = 0;
    bool              wsa_up_     = false;

    std::atomic<uint64_t> packets_sent_{0};
};
