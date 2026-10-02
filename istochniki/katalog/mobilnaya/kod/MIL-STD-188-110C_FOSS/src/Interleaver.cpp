#include "milstd110c/Interleaver.h"
#include <algorithm>

namespace mil::std110c {

std::vector<uint8_t> Interleaver::interleave(const BitStream& input) const {
    std::vector<uint8_t> matrix(kRows * kColumns, 0);

    size_t row = 0;
    size_t col = 0;
    size_t idx = 0;
    // Load bits using row increment of 9 mod kRows (todo: 7 mod kRows when 75bps fixed-freq)
    while (idx < input.getMaxBitIndex() && col < kColumns) {
        matrix[row * kColumns + col] = input.getBitVal(idx++);
        row = (row + 9) % kRows;
        if (row == 0) ++col;
    }

    // Fetch bits using column decrement of 17 mod kCol (todo: 7 mod kCol when 75bps fixed-freq)
    BitStream fetched;
    row = 0;
    col = 0;
    while (fetched.getMaxBitIndex() < kRows * kColumns) {
        fetched.putBit(matrix[row * kColumns + col]);
        ++row;
        if (row == kRows) {
            row = 0;
            col = (col + 1) % kColumns;
        } else {
            col = (col + kColumns - 17) % kColumns;
        }
    }

    // Group bits into tribits
    std::vector<uint8_t> symbols;
    for (size_t i = 0; i + 2 < fetched.getMaxBitIndex(); i += 3) {
        uint8_t symbol = static_cast<uint8_t>((fetched.getBitVal(i) << 2) |
                                              (fetched.getBitVal(i + 1) << 1) |
                                              fetched.getBitVal(i + 2));
        symbols.push_back(symbol);
    }

    return symbols;
}

} // namespace mil::std110c

