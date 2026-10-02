/* Печать перестановки перемежителя турбокода UMTS из srsRAN_4G (tc_interl_umts.c) для K = 40..5114. */
#include <stdio.h>
#include "srsran/phy/fec/turbo/tc_interl.h"
int main(void) {
  srsran_tc_interl_t h; srsran_tc_interl_init(&h, 5114);
  for (unsigned K = 40; K <= 5114; K++) {
    if (srsran_tc_interl_UMTS_gen(&h, K)) { printf("ERR %u\n", K); continue; }
    printf("%u", K); for (unsigned i = 0; i < K; i++) printf(" %u", h.forward[i]); printf("\n");
  }
  return 0;
}
