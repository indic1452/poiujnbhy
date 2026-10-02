#include <stdlib.h>
#include <stdint.h>
static inline uint16_t* srsran_vec_u16_malloc(uint32_t n) { return (uint16_t*)malloc(n * sizeof(uint16_t)); }
