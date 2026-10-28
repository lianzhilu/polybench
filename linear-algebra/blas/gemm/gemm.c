/**
 * This version is stamped on May 10, 2016
 *
 * Contact:
 *   Louis-Noel Pouchet <pouchet.ohio-state.edu>
 *   Tomofumi Yuki <tomofumi.yuki.fr>
 *
 * Web address: http://polybench.sourceforge.net
 */
/* gemm.c: this file is part of PolyBench/C */

#include <stdio.h>
#include <unistd.h>
#include <string.h>
#include <math.h>

/* Include polybench common header. */
#include <polybench.h>

/* Include benchmark-specific header. */
#include "gemm.h"

/* Accept the suite-wide mode while retaining the original GEMM mode/exports. */
#if defined(POLYBENCH_REMAIN_KERNEL) && !defined(GEMM_REMAIN_KERNEL)
#define GEMM_REMAIN_KERNEL
#endif

#ifdef GEMM_REMAIN_KERNEL
#include <stdint.h>
#endif


/* Array initialization. */
static
void init_array(int ni, int nj, int nk,
		DATA_TYPE *alpha,
		DATA_TYPE *beta,
		DATA_TYPE POLYBENCH_2D(C,NI,NJ,ni,nj),
		DATA_TYPE POLYBENCH_2D(A,NI,NK,ni,nk),
		DATA_TYPE POLYBENCH_2D(B,NK,NJ,nk,nj))
{
  int i, j;

  *alpha = 1.5;
  *beta = 1.2;
  for (i = 0; i < ni; i++)
    for (j = 0; j < nj; j++)
      C[i][j] = (DATA_TYPE) ((i*j+1) % ni) / ni;
  for (i = 0; i < ni; i++)
    for (j = 0; j < nk; j++)
      A[i][j] = (DATA_TYPE) (i*(j+1) % nk) / nk;
  for (i = 0; i < nk; i++)
    for (j = 0; j < nj; j++)
      B[i][j] = (DATA_TYPE) (i*(j+2) % nj) / nj;
}


/* DCE code. Must scan the entire live-out data.
   Can be used also to check the correctness of the output. */
#ifndef GEMM_REMAIN_KERNEL
static
void print_array(int ni, int nj,
		 DATA_TYPE POLYBENCH_2D(C,NI,NJ,ni,nj))
{
  int i, j;

  POLYBENCH_DUMP_START;
  POLYBENCH_DUMP_BEGIN("C");
  for (i = 0; i < ni; i++)
    for (j = 0; j < nj; j++) {
	if ((i * ni + j) % 20 == 0) fprintf (POLYBENCH_DUMP_TARGET, "\n");
	fprintf (POLYBENCH_DUMP_TARGET, DATA_PRINTF_MODIFIER, C[i][j]);
    }
  POLYBENCH_DUMP_END("C");
  POLYBENCH_DUMP_FINISH;
}
#endif


/* Main computational kernel. The whole function will be timed,
   including the call and return. */
static
void kernel_gemm(int ni, int nj, int nk,
		 DATA_TYPE alpha,
		 DATA_TYPE beta,
		 DATA_TYPE POLYBENCH_2D(C,NI,NJ,ni,nj),
		 DATA_TYPE POLYBENCH_2D(A,NI,NK,ni,nk),
		 DATA_TYPE POLYBENCH_2D(B,NK,NJ,nk,nj))
{
  int i, j, k;

//BLAS PARAMS
//TRANSA = 'N'
//TRANSB = 'N'
// => Form C := alpha*A*B + beta*C,
//A is NIxNK
//B is NKxNJ
//C is NIxNJ
#pragma scop
  for (i = 0; i < _PB_NI; i++) {
    for (j = 0; j < _PB_NJ; j++)
	C[i][j] *= beta;
    for (k = 0; k < _PB_NK; k++) {
       for (j = 0; j < _PB_NJ; j++)
	  C[i][j] += alpha * A[i][k] * B[k][j];
    }
  }
#pragma endscop

}


#ifdef GEMM_REMAIN_KERNEL

/* Keep the original kernel and initialization, but let the host own timing and
   output. These pointers use the same row layout and 4096-byte alignment as the
   default PolyBench heap allocation. */
static DATA_TYPE (*gemm_c)[NJ + POLYBENCH_PADDING_FACTOR];
static DATA_TYPE (*gemm_a)[NK + POLYBENCH_PADDING_FACTOR];
static DATA_TYPE (*gemm_b)[NJ + POLYBENCH_PADDING_FACTOR];
static DATA_TYPE gemm_alpha, gemm_beta;

static void* gemm_alloc(size_t bytes)
{
  void* ptr = NULL;
  if (posix_memalign(&ptr, 4096, bytes) != 0 || ptr == NULL)
    __builtin_trap();
  return ptr;
}

/* Call before EVERY measurement: C is an input as well as an output. */
void gemm_init(void)
{
  if (gemm_c == NULL) {
    gemm_c = gemm_alloc(sizeof(DATA_TYPE[NI + POLYBENCH_PADDING_FACTOR]
                                     [NJ + POLYBENCH_PADDING_FACTOR]));
    gemm_a = gemm_alloc(sizeof(DATA_TYPE[NI + POLYBENCH_PADDING_FACTOR]
                                     [NK + POLYBENCH_PADDING_FACTOR]));
    gemm_b = gemm_alloc(sizeof(DATA_TYPE[NK + POLYBENCH_PADDING_FACTOR]
                                     [NJ + POLYBENCH_PADDING_FACTOR]));
  }
  init_array(NI, NJ, NK, &gemm_alpha, &gemm_beta, gemm_c, gemm_a, gemm_b);
}

void gemm_run(void)
{
  if (gemm_c == NULL)
    __builtin_trap();
  kernel_gemm(NI, NJ, NK, gemm_alpha, gemm_beta, gemm_c, gemm_a, gemm_b);
}

/* All live-out elements remain observable under -O3; no stdio is needed. */
double gemm_checksum(void)
{
  double sum = 0.0;
  if (gemm_c == NULL)
    __builtin_trap();
  for (int i = 0; i < NI; i++)
    for (int j = 0; j < NJ; j++)
      sum += gemm_c[i][j];
  return sum;
}

/* Explicit i64 in BOTH builds, rather than a width-dependent pointer ABI. */
uint64_t gemm_c_offset(void) { return (uint64_t)(uintptr_t)gemm_c; }
int gemm_ni(void) { return NI; }
int gemm_nj(void) { return NJ; }
int gemm_nk(void) { return NK; }
int gemm_row_stride(void) { return NJ + POLYBENCH_PADDING_FACTOR; }
int gemm_pointer_bits(void) { return (int)(8 * sizeof(void*)); }

void gemm_free(void)
{
  free(gemm_c);
  free(gemm_a);
  free(gemm_b);
  gemm_c = NULL;
  gemm_a = NULL;
  gemm_b = NULL;
}

/* Common ABI v1 aliases. Existing gemm_* callers continue to work. */
static void gemm_require_output(int index)
{
  if (index != 0 || gemm_c == NULL)
    __builtin_trap();
}

void bench_init(void) { gemm_init(); }
void bench_run(void) { gemm_run(); }
void bench_free(void) { gemm_free(); }
int bench_abi_version(void) { return 1; }
int bench_pointer_bits(void) { return gemm_pointer_bits(); }
int bench_param_count(void) { return 3; }
int bench_param(int index)
{
  const int params[] = {NI, NJ, NK};
  if (index < 0 || index >= 3)
    __builtin_trap();
  return params[index];
}
int bench_output_count(void) { gemm_require_output(0); return 1; }
int bench_output_type(int index)
{
  gemm_require_output(index);
  return _Generic((DATA_TYPE)0, double: 1, float: 2, int: 3);
}
int bench_output_rank(int index) { gemm_require_output(index); return 2; }
int bench_output_region(int index) { gemm_require_output(index); return 0; }
int bench_output_dim(int index, int axis)
{
  gemm_require_output(index);
  if (axis < 0 || axis >= 2)
    __builtin_trap();
  return axis == 0 ? NI : NJ;
}
uint64_t bench_output_stride(int index, int axis)
{
  gemm_require_output(index);
  if (axis < 0 || axis >= 2)
    __builtin_trap();
  return axis == 0 ? sizeof(*gemm_c) : sizeof(DATA_TYPE);
}
uint64_t bench_output_offset(int index)
{
  gemm_require_output(index);
  return gemm_c_offset();
}
double bench_checksum(int index)
{
  gemm_require_output(index);
  return gemm_checksum();
}

#else

int main(int argc, char** argv)
{
  /* Retrieve problem size. */
  int ni = NI;
  int nj = NJ;
  int nk = NK;

  /* Variable declaration/allocation. */
  DATA_TYPE alpha;
  DATA_TYPE beta;
  POLYBENCH_2D_ARRAY_DECL(C,DATA_TYPE,NI,NJ,ni,nj);
  POLYBENCH_2D_ARRAY_DECL(A,DATA_TYPE,NI,NK,ni,nk);
  POLYBENCH_2D_ARRAY_DECL(B,DATA_TYPE,NK,NJ,nk,nj);

  /* Initialize array(s). */
  init_array (ni, nj, nk, &alpha, &beta,
	      POLYBENCH_ARRAY(C),
	      POLYBENCH_ARRAY(A),
	      POLYBENCH_ARRAY(B));

  /* Start timer. */
  polybench_start_instruments;

  /* Run kernel. */
  kernel_gemm (ni, nj, nk,
	       alpha, beta,
	       POLYBENCH_ARRAY(C),
	       POLYBENCH_ARRAY(A),
	       POLYBENCH_ARRAY(B));

  /* Stop and print timer. */
  polybench_stop_instruments;
  polybench_print_instruments;

  /* Prevent dead-code elimination. All live-out data must be printed
     by the function call in argument. */
  polybench_prevent_dce(print_array(ni, nj,  POLYBENCH_ARRAY(C)));

  /* Be clean. */
  POLYBENCH_FREE_ARRAY(C);
  POLYBENCH_FREE_ARRAY(A);
  POLYBENCH_FREE_ARRAY(B);

  return 0;
}

#endif /* GEMM_REMAIN_KERNEL */
