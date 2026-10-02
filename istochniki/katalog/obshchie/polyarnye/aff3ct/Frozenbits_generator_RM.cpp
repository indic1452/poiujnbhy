#ifndef _USE_MATH_DEFINES
#define _USE_MATH_DEFINES
#endif
#include <cli.hpp>
#include <cmath>
#include <cstdint>
#include <numeric>
#include <streampu.hpp>
#include <string>
#include <vector>

#include "Tools/Code/Polar/Frozenbits_generator/Frozenbits_generator_RM.hpp"

using namespace aff3ct::tools;

Frozenbits_generator_RM ::Frozenbits_generator_RM(const int K,
                                                  const int N,
                                                  const std::string& dump_channels_path,
                                                  const bool dump_channels_single_thread)
  : Frozenbits_generator(K, N, dump_channels_path, dump_channels_single_thread)
{
}

Frozenbits_generator_RM ::~Frozenbits_generator_RM() {}

Frozenbits_generator_RM*
Frozenbits_generator_RM ::clone() const
{
    auto t = new Frozenbits_generator_RM(*this);
    return t;
}

void
Frozenbits_generator_RM ::evaluate()
{
    std::vector<uint32_t> best_channels_mother(this->N);
    std::iota(best_channels_mother.begin(), best_channels_mother.end(), 0);
    std::vector<uint32_t> channel_weights(this->N);
    for (int i = 0; i < this->N; i++)
    {
        int temp = i;
        int weight = 0;
        while (temp)
        {
            weight += (temp)&1;
            temp >>= 1;
        }
        channel_weights[i] = weight;
    }

    std::sort(best_channels_mother.begin(),
              best_channels_mother.end(),
              [&channel_weights](int x, int y) { return channel_weights[x] > channel_weights[y]; });

    std::copy(best_channels_mother.begin(), best_channels_mother.end(), best_channels.begin());
}
