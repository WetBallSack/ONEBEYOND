#include "config.h"

#include <algorithm>
#include <cctype>
#include <fstream>
#include <iostream>
#include <string>

// ── Helpers ─────────────────────────────────────────────────────────────────

static std::string trim(const std::string& s) {
    auto start = s.find_first_not_of(" \t\r\n");
    if (start == std::string::npos) return {};
    auto end = s.find_last_not_of(" \t\r\n");
    return s.substr(start, end - start + 1);
}

static bool iequals(const std::string& a, const std::string& b) {
    if (a.size() != b.size()) return false;
    return std::equal(a.begin(), a.end(), b.begin(),
                      [](char ca, char cb) {
                          return std::tolower(static_cast<unsigned char>(ca)) ==
                                 std::tolower(static_cast<unsigned char>(cb));
                      });
}

// ── INI loader ──────────────────────────────────────────────────────────────

Config load_config(const std::string& path) {
    Config cfg;

    std::ifstream file(path);
    if (!file.is_open()) {
        std::cerr << "[HID-FWD] [WARN] Could not open config file: " << path
                  << " — using defaults\n";
        return cfg;
    }

    std::string line;
    while (std::getline(file, line)) {
        line = trim(line);

        // Skip empty lines, comments, and section headers.
        if (line.empty() || line[0] == '#' || line[0] == ';' || line[0] == '[')
            continue;

        auto eq = line.find('=');
        if (eq == std::string::npos) continue;

        std::string key   = trim(line.substr(0, eq));
        std::string value = trim(line.substr(eq + 1));

        if (iequals(key, "target_ip")) {
            cfg.target_ip = value;
        } else if (iequals(key, "target_port")) {
            cfg.target_port = static_cast<uint16_t>(std::stoi(value));
        } else if (iequals(key, "log_level")) {
            cfg.log_level = std::stoi(value);
        } else if (iequals(key, "use_relative_deltas")) {
            cfg.use_relative_deltas =
                iequals(value, "true") || value == "1";
        } else if (iequals(key, "relay_url")) {
            cfg.relay_url = value;
        } else if (iequals(key, "relay_key")) {
            cfg.relay_key = value;
        } else if (iequals(key, "use_relay")) {
            cfg.use_relay = iequals(value, "true") || value == "1";
        } else if (iequals(key, "relay_verify_tls")) {
            cfg.relay_verify_tls = iequals(value, "true") || value == "1";
        }
    }

    return cfg;
}
