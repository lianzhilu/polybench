/* Common exported entry point for every generated PolyBench reactor. */

#include <stdint.h>

extern int polybench_original_main(int argc, char** argv);
extern void polybench_checksum_reset(void);
#ifdef __wasm__
extern void __wasm_call_ctors(void);
#endif

static int runtime_initialized;

int benchmark_run(void)
{
  /* CLI runtimes invoke this export directly and do not necessarily call the
   * Emscripten reactor's `_initialize` export first.  Initialize lazily so the
   * same module works both from a CLI and from an embedding host. */
  if (!runtime_initialized)
    {
#ifdef __wasm__
      __wasm_call_ctors();
#endif
      runtime_initialized = 1;
    }
  polybench_checksum_reset();
  return polybench_original_main(0, (char**)0);
}
