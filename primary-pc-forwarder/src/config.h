#pragma once

#include <cstdint>
#include <string>

// ── Configuration ───────────────────────────────────────────────────────────
struct Config {
    std::string target_ip        = "192.168.1.100";
    uint16_t    target_port      = 5555;
    int         log_level        = 2;   // 0=silent 1=errors 2=info 3=debug
    bool        use_relative_deltas = true;

    // Cloud relay (WAN connectivity via WebSocket)
    std::string relay_url        = "";    // e.g. wss://anthe.auraplot.site:8765
    std::string relay_key        = "";    // shared room key
    bool        use_relay         = false; // enable relay mode
    bool        relay_verify_tls  = true;  // verify TLS cert
};

// Load configuration from a simple INI file.
// Lines of the form `key = value` are recognised.  Comments (#, ;) and
// section headers ([...]) are silently ignored.  Missing keys keep the
// default value from the Config struct above.
Config load_config(const std::string& path);
