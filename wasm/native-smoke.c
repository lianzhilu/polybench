#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>

extern int benchmark_run(void);
extern uint64_t benchmark_checksum(void);

int main(void)
{
  int result = benchmark_run();
  printf("result=%d checksum=%" PRIu64 "\n", result, benchmark_checksum());
  return result;
}
