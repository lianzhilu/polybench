/* Host-driven PolyBench ABI v1. Include only in POLYBENCH_REMAIN_KERNEL mode.
 * Each module/shared library contains one benchmark translation unit. */
#ifndef POLYBENCH_WASM_BENCH_H
#define POLYBENCH_WASM_BENCH_H

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#if defined(POLYBENCH_STACK_ARRAYS) || defined(POLYBENCH_ENABLE_INTARRAY_PAD)
#error "Host-driven mode requires default heap allocation without inter-array padding"
#endif

enum { WB_F64 = 1, WB_F32 = 2, WB_I32 = 3 };
enum { WB_FULL = 0, WB_LOWER = 1, WB_UPPER = 2 };
#if defined(DATA_TYPE_IS_DOUBLE)
#define WB_DATA_TYPE WB_F64
#elif defined(DATA_TYPE_IS_FLOAT)
#define WB_DATA_TYPE WB_F32
#elif defined(DATA_TYPE_IS_INT)
#define WB_DATA_TYPE WB_I32
#else
#error "Unsupported benchmark element type"
#endif

typedef struct {
  void* data;
  int type, rank, region;
  int dims[3];
  uint64_t strides[3]; /* Bytes, including row/plane padding. */
} wb_output;

static wb_output wb_outputs[4];
static int wb_output_count;
static int wb_ready;

static void wb_require(int condition)
{
  if (!condition)
    __builtin_trap();
}

/* Also serves temporary allocations in the unchanged cholesky/lu/ludcmp init. */
void* polybench_alloc_data(unsigned long long int n, int elt_size)
{
  void* ptr = NULL;
  wb_require(elt_size > 0 && n <= SIZE_MAX / (unsigned int)elt_size);
  if (posix_memalign(&ptr, 4096, (size_t)n * (size_t)elt_size) != 0 || !ptr)
    __builtin_trap();
  return ptr;
}

void polybench_free_data(void* ptr) { free(ptr); }

#define WB_DECLARE_1D(type, name, d1) \
  static type (*name)[(d1) + POLYBENCH_PADDING_FACTOR]
#define WB_DECLARE_2D(type, name, d1, d2) \
  static type (*name)[(d1) + POLYBENCH_PADDING_FACTOR][(d2) + POLYBENCH_PADDING_FACTOR]
#define WB_DECLARE_3D(type, name, d1, d2, d3) \
  static type (*name)[(d1) + POLYBENCH_PADDING_FACTOR][(d2) + POLYBENCH_PADDING_FACTOR][(d3) + POLYBENCH_PADDING_FACTOR]
#define WB_RESET(name) do { \
  if (!(name)) (name) = polybench_alloc_data(sizeof(*(name)), 1); \
  memset((name), 0, sizeof(*(name))); \
} while (0)
#define WB_FREE(name) do { free(name); (name) = NULL; } while (0)

#define WB_OUTPUT_1D(index, name, d1) \
  wb_outputs[index] = (wb_output){(name), WB_DATA_TYPE, 1, WB_FULL, \
    {d1, 1, 1}, {sizeof((*name)[0]), 0, 0}}
#define WB_OUTPUT_2D(index, name, d1, d2, mask) \
  wb_outputs[index] = (wb_output){(name), WB_DATA_TYPE, 2, mask, \
    {d1, d2, 1}, {sizeof((*name)[0]), sizeof((*name)[0][0]), 0}}
#define WB_OUTPUT_3D(index, name, d1, d2, d3) \
  wb_outputs[index] = (wb_output){(name), WB_DATA_TYPE, 3, WB_FULL, \
    {d1, d2, d3}, {sizeof((*name)[0]), sizeof((*name)[0][0]), sizeof((*name)[0][0][0])}}

static wb_output* wb_get_output(int index)
{
  wb_require(wb_ready && index >= 0 && index < wb_output_count);
  return &wb_outputs[index];
}

int bench_abi_version(void) { return 1; }
int bench_pointer_bits(void) { return (int)(8 * sizeof(void*)); }
int bench_output_count(void) { wb_require(wb_ready); return wb_output_count; }
int bench_output_type(int index) { return wb_get_output(index)->type; }
int bench_output_rank(int index) { return wb_get_output(index)->rank; }
int bench_output_region(int index) { return wb_get_output(index)->region; }
int bench_output_dim(int index, int axis)
{
  wb_output* out = wb_get_output(index);
  wb_require(axis >= 0 && axis < out->rank);
  return out->dims[axis];
}
uint64_t bench_output_stride(int index, int axis)
{
  wb_output* out = wb_get_output(index);
  wb_require(axis >= 0 && axis < out->rank);
  return out->strides[axis];
}
/* Explicit uint64_t/i64 in both Wasm builds and in the native reference. */
uint64_t bench_output_offset(int index)
{
  return (uint64_t)(uintptr_t)wb_get_output(index)->data;
}

double bench_checksum(int index)
{
  wb_output* out = wb_get_output(index);
  double sum = 0.0;
  for (int i = 0; i < out->dims[0]; ++i)
    for (int j = 0; j < out->dims[1]; ++j) {
      if ((out->region == WB_LOWER && j > i) ||
          (out->region == WB_UPPER && j < i))
        continue;
      for (int k = 0; k < out->dims[2]; ++k) {
        const char* address = (const char*)out->data +
          (size_t)i * out->strides[0] + (size_t)j * out->strides[1] +
          (size_t)k * out->strides[2];
        switch (out->type) {
          case WB_F64: sum += *(const double*)address; break;
          case WB_F32: sum += *(const float*)address; break;
          case WB_I32: sum += *(const int*)address; break;
          default: __builtin_trap();
        }
      }
    }
  return sum;
}

#endif
