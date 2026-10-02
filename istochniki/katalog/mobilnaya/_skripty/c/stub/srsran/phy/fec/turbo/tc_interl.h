/* заглушка заголовка srsRAN для сборки tc_interl_umts.c вне дерева */
#include <stdint.h>
typedef struct { uint16_t* forward; uint16_t* reverse; uint32_t max_long_cb; } srsran_tc_interl_t;
int srsran_tc_interl_UMTS_gen(srsran_tc_interl_t* h, uint32_t long_cb);
int srsran_tc_interl_init(srsran_tc_interl_t* h, uint32_t max_long_cb);
void srsran_tc_interl_free(srsran_tc_interl_t* h);
