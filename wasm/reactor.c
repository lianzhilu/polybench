/* Common exported entry point for every generated PolyBench reactor. */

#include <stdint.h>

extern int polybench_original_main(int argc, char** argv);
extern void polybench_checksum_reset(void);

#if defined(__wasm__)
__attribute__((export_name("benchmark_run")))
#endif
int benchmark_run(void)
{
  polybench_checksum_reset();
  return polybench_original_main(0, (char**)0);
}
