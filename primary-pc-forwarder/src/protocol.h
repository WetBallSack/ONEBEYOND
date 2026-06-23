#pragma once

#include <cstdint>

// ── Protocol constants ──────────────────────────────────────────────────────
constexpr uint8_t PACKET_MAGIC = 0xAB;

// ── HidPacket ───────────────────────────────────────────────────────────────
// Binary wire format for mouse events.  All multi-byte integers are
// little-endian (native on x86/x64).
//
//   Offset  Size  Field
//   ------  ----  -----
//     0       1   magic   – must equal PACKET_MAGIC (0xAB)
//     1       2   dx      – signed relative X movement
//     3       2   dy      – signed relative Y movement
//     5       1   buttons – bit 0: Left, bit 1: Right, bit 2: Middle
//     6       1   seq     – rolling sequence 0-255
//
#pragma pack(push, 1)
struct HidPacket {
    uint8_t  magic   = PACKET_MAGIC;
    int16_t  dx      = 0;
    int16_t  dy      = 0;
    uint8_t  buttons = 0;
    uint8_t  seq     = 0;
};
#pragma pack(pop)

static_assert(sizeof(HidPacket) == 7, "HidPacket must be exactly 7 bytes");
