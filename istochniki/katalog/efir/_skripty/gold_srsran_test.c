#include "srsran/phy/common/sequence.h"
#include <stdio.h>
int main(){ srsran_sequence_t q={0}; unsigned seeds[3]={0,1,0x12345};
 for(int k=0;k<3;k++){ srsran_sequence_LTE_pr(&q, 64, seeds[k]); printf("%u ",seeds[k]); for(int i=0;i<64;i++) printf("%d",q.c[i]); printf("\n"); srsran_sequence_free(&q);} return 0;}
