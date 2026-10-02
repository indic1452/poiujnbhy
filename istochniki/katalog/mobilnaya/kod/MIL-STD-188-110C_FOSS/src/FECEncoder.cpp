#include "milstd110c/FECEncoder.h"

namespace mil::std110c {
namespace {
inline uint8_t parity(uint8_t x) {
    x ^= x >> 4;
    x ^= x >> 2;
    x ^= x >> 1;
    return x & 1;
}
} // namespace

FECEncoder::FECEncoder() : shift_register_(0) {}

BitStream FECEncoder::encode(const BitStream& data) {
    BitStream input(data);
    BitStream output;

    while (input.hasNext()) {
        uint8_t bit = input.getNextBit();
        shift_register_ = static_cast<uint8_t>(((shift_register_ << 1) | bit) & 0x7F);
        uint8_t t1 = parity(shift_register_ & 0b1011011); // 133 octal
        uint8_t t2 = parity(shift_register_ & 0b1111001); // 171 octal
        output.putBit(t1);
        output.putBit(t2);
    }

    return output;
}

} // namespace mil::std110c

