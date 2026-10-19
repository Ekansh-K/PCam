#pragma once
#include <cstddef>
#include <cstdint>

// Sampled fingerprints let an independent V4L2 consumer verify frame identity
// without storing video or changing any pixels.
inline uint64_t frame_hash(const uint8_t *data, size_t size) {
    uint64_t value = UINT64_C(14695981039346656037);
    for (size_t index = 0; index < size; index += 4096)
        value = (value ^ data[index]) * UINT64_C(1099511628211);
    return value;
}
