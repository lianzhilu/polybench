/*
 * Runtime support for import-free PolyBench WebAssembly reactors.
 *
 * This deliberately implements only the allocation and live-out checksum
 * facilities needed by the benchmark kernels.  In particular it contains no
 * clocks, files, command-line handling, printing, or process-exit calls, so a
 * wasm64 build does not acquire the non-standard 64-bit WASIp1 imports emitted
 * by Emscripten's standalone libc support.
 */

#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>

/* polybench.h maps fprintf to this function in reactor builds. */
#ifdef fprintf
# undef fprintf
#endif

#define POLYBENCH_ALIGNMENT 4096u
#define FNV_OFFSET UINT64_C(14695981039346656037)
#define FNV_PRIME UINT64_C(1099511628211)

static volatile uint64_t polybench_checksum_value = FNV_OFFSET;

static void checksum_u64(uint64_t value)
{
  unsigned int i;
  uint64_t hash = polybench_checksum_value;
  for (i = 0; i < 8; ++i)
    {
      hash ^= value & UINT64_C(0xff);
      hash *= FNV_PRIME;
      value >>= 8;
    }
  polybench_checksum_value = hash;
}

void polybench_checksum_reset(void)
{
  polybench_checksum_value = FNV_OFFSET;
}

uint64_t benchmark_checksum(void)
{
  return polybench_checksum_value;
}

/*
 * PolyBench print_array functions pass one numeric value per formatting call.
 * Hash that value's representation instead of formatting it.  Calls that only
 * contain dump labels/newlines intentionally contribute nothing.
 */
int polybench_reactor_fprintf(void* stream, const char* format, ...)
{
  const char* cursor = format;
  char conversion = 0;
  va_list args;
  (void)stream;

  while (*cursor)
    {
      if (*cursor == '%')
        {
          ++cursor;
          if (*cursor == '%')
            {
              ++cursor;
              continue;
            }
          while (*cursor)
            {
              char c = *cursor++;
              if (c == 'd' || c == 'i' || c == 'o' || c == 'u' ||
                  c == 'x' || c == 'X' || c == 'f' || c == 'F' ||
                  c == 'e' || c == 'E' || c == 'g' || c == 'G' ||
                  c == 'a' || c == 'A')
                {
                  conversion = c;
                  break;
                }
            }
        }
      else
        ++cursor;
    }

  if (!conversion)
    return 0;

  va_start(args, format);
  if (conversion == 'f' || conversion == 'F' ||
      conversion == 'e' || conversion == 'E' ||
      conversion == 'g' || conversion == 'G' ||
      conversion == 'a' || conversion == 'A')
    {
      union
      {
        double floating;
        uint64_t bits;
      } value;
      value.floating = va_arg(args, double);
      checksum_u64(value.bits);
    }
  else if (conversion == 'd' || conversion == 'i')
    {
      int value = va_arg(args, int);
      checksum_u64((uint64_t)(int64_t)value);
    }
  else if (conversion == 'o' || conversion == 'u' ||
           conversion == 'x' || conversion == 'X')
    checksum_u64((uint64_t)va_arg(args, unsigned int));
  va_end(args);
  return 0;
}

void* polybench_alloc_data(unsigned long long int count, int element_size)
{
  size_t bytes;
  size_t allocation_size;
  uintptr_t aligned;
  void* allocation;

  if (element_size <= 0 || count > (SIZE_MAX / (size_t)element_size))
    __builtin_trap();

  bytes = (size_t)count * (size_t)element_size;
  if (bytes > SIZE_MAX - (POLYBENCH_ALIGNMENT - 1u) - sizeof(void*))
    __builtin_trap();

  allocation_size = bytes + (POLYBENCH_ALIGNMENT - 1u) + sizeof(void*);
  allocation = malloc(allocation_size);
  if (!allocation)
    __builtin_trap();

  aligned = ((uintptr_t)allocation + sizeof(void*) +
             (POLYBENCH_ALIGNMENT - 1u)) &
            ~((uintptr_t)POLYBENCH_ALIGNMENT - 1u);
  ((void**)aligned)[-1] = allocation;
  return (void*)aligned;
}

void polybench_free_data(void* pointer)
{
  if (pointer)
    free(((void**)pointer)[-1]);
}

/* No-op compatibility functions; timing belongs to the host harness. */
void polybench_flush_cache(void) {}
void polybench_prepare_instruments(void) {}
