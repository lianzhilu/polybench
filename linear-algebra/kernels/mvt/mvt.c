/**
 * This version is stamped on May 10, 2016
 *
 * Contact:
 *   Louis-Noel Pouchet <pouchet.ohio-state.edu>
 *   Tomofumi Yuki <tomofumi.yuki.fr>
 *
 * Web address: http://polybench.sourceforge.net
 */
/* mvt.c: this file is part of PolyBench/C */

#include <stdio.h>
#include <unistd.h>
#include <string.h>
#include <math.h>

/* Include polybench common header. */
#include <polybench.h>

/* Include benchmark-specific header. */
#include "mvt.h"

#ifdef POLYBENCH_REMAIN_KERNEL
#include <wasm_bench.h>
#endif


/* Array initialization. */
static
void init_array(int n,
		DATA_TYPE POLYBENCH_1D(x1,N,n),
		DATA_TYPE POLYBENCH_1D(x2,N,n),
		DATA_TYPE POLYBENCH_1D(y_1,N,n),
		DATA_TYPE POLYBENCH_1D(y_2,N,n),
		DATA_TYPE POLYBENCH_2D(A,N,N,n,n))
{
  int i, j;

  for (i = 0; i < n; i++)
    {
      x1[i] = (DATA_TYPE) (i % n) / n;
      x2[i] = (DATA_TYPE) ((i + 1) % n) / n;
      y_1[i] = (DATA_TYPE) ((i + 3) % n) / n;
      y_2[i] = (DATA_TYPE) ((i + 4) % n) / n;
      for (j = 0; j < n; j++)
	A[i][j] = (DATA_TYPE) (i*j % n) / n;
    }
}


/* DCE code. Must scan the entire live-out data.
   Can be used also to check the correctness of the output. */
#ifndef POLYBENCH_REMAIN_KERNEL
static
void print_array(int n,
		 DATA_TYPE POLYBENCH_1D(x1,N,n),
		 DATA_TYPE POLYBENCH_1D(x2,N,n))

{
  int i;

  POLYBENCH_DUMP_START;
  POLYBENCH_DUMP_BEGIN("x1");
  for (i = 0; i < n; i++) {
    if (i % 20 == 0) fprintf (POLYBENCH_DUMP_TARGET, "\n");
    fprintf (POLYBENCH_DUMP_TARGET, DATA_PRINTF_MODIFIER, x1[i]);
  }
  POLYBENCH_DUMP_END("x1");

  POLYBENCH_DUMP_BEGIN("x2");
  for (i = 0; i < n; i++) {
    if (i % 20 == 0) fprintf (POLYBENCH_DUMP_TARGET, "\n");
    fprintf (POLYBENCH_DUMP_TARGET, DATA_PRINTF_MODIFIER, x2[i]);
  }
  POLYBENCH_DUMP_END("x2");
  POLYBENCH_DUMP_FINISH;
}
#endif


/* Main computational kernel. The whole function will be timed,
   including the call and return. */
static
void kernel_mvt(int n,
		DATA_TYPE POLYBENCH_1D(x1,N,n),
		DATA_TYPE POLYBENCH_1D(x2,N,n),
		DATA_TYPE POLYBENCH_1D(y_1,N,n),
		DATA_TYPE POLYBENCH_1D(y_2,N,n),
		DATA_TYPE POLYBENCH_2D(A,N,N,n,n))
{
  int i, j;

#pragma scop
  for (i = 0; i < _PB_N; i++)
    for (j = 0; j < _PB_N; j++)
      x1[i] = x1[i] + A[i][j] * y_1[j];
  for (i = 0; i < _PB_N; i++)
    for (j = 0; j < _PB_N; j++)
      x2[i] = x2[i] + A[j][i] * y_2[j];
#pragma endscop

}


#ifdef POLYBENCH_REMAIN_KERNEL

/* Host-owned lifecycle; original initialization and kernel are unchanged. */
static int n = N;
WB_DECLARE_2D(DATA_TYPE, A, N, N);
WB_DECLARE_1D(DATA_TYPE, x1, N);
WB_DECLARE_1D(DATA_TYPE, x2, N);
WB_DECLARE_1D(DATA_TYPE, y_1, N);
WB_DECLARE_1D(DATA_TYPE, y_2, N);

void bench_init(void)
{
  WB_RESET(A);
  WB_RESET(x1);
  WB_RESET(x2);
  WB_RESET(y_1);
  WB_RESET(y_2);
  init_array (n,
	      POLYBENCH_ARRAY(x1),
	      POLYBENCH_ARRAY(x2),
	      POLYBENCH_ARRAY(y_1),
	      POLYBENCH_ARRAY(y_2),
	      POLYBENCH_ARRAY(A));
  wb_output_count = 2;
  WB_OUTPUT_1D(0, x1, n);
  WB_OUTPUT_1D(1, x2, n);
  wb_ready = 1;
}

void bench_run(void)
{
  wb_require(wb_ready);
  kernel_mvt (n,
	      POLYBENCH_ARRAY(x1),
	      POLYBENCH_ARRAY(x2),
	      POLYBENCH_ARRAY(y_1),
	      POLYBENCH_ARRAY(y_2),
	      POLYBENCH_ARRAY(A));
}

int bench_param_count(void)
{
  return 1;
}

int bench_param(int index)
{
  const int params[] = {n};
  wb_require(index >= 0 && index < 1);
  return params[index];
}

void bench_free(void)
{
  WB_FREE(A);
  WB_FREE(x1);
  WB_FREE(x2);
  WB_FREE(y_1);
  WB_FREE(y_2);
  wb_ready = 0;
  wb_output_count = 0;
}

#else
int main(int argc, char** argv)
{
  /* Retrieve problem size. */
  int n = N;

  /* Variable declaration/allocation. */
  POLYBENCH_2D_ARRAY_DECL(A, DATA_TYPE, N, N, n, n);
  POLYBENCH_1D_ARRAY_DECL(x1, DATA_TYPE, N, n);
  POLYBENCH_1D_ARRAY_DECL(x2, DATA_TYPE, N, n);
  POLYBENCH_1D_ARRAY_DECL(y_1, DATA_TYPE, N, n);
  POLYBENCH_1D_ARRAY_DECL(y_2, DATA_TYPE, N, n);


  /* Initialize array(s). */
  init_array (n,
	      POLYBENCH_ARRAY(x1),
	      POLYBENCH_ARRAY(x2),
	      POLYBENCH_ARRAY(y_1),
	      POLYBENCH_ARRAY(y_2),
	      POLYBENCH_ARRAY(A));

  /* Start timer. */
  polybench_start_instruments;

  /* Run kernel. */
  kernel_mvt (n,
	      POLYBENCH_ARRAY(x1),
	      POLYBENCH_ARRAY(x2),
	      POLYBENCH_ARRAY(y_1),
	      POLYBENCH_ARRAY(y_2),
	      POLYBENCH_ARRAY(A));

  /* Stop and print timer. */
  polybench_stop_instruments;
  polybench_print_instruments;

  /* Prevent dead-code elimination. All live-out data must be printed
     by the function call in argument. */
  polybench_prevent_dce(print_array(n, POLYBENCH_ARRAY(x1), POLYBENCH_ARRAY(x2)));

  /* Be clean. */
  POLYBENCH_FREE_ARRAY(A);
  POLYBENCH_FREE_ARRAY(x1);
  POLYBENCH_FREE_ARRAY(x2);
  POLYBENCH_FREE_ARRAY(y_1);
  POLYBENCH_FREE_ARRAY(y_2);

  return 0;
}

#endif /* POLYBENCH_REMAIN_KERNEL */
