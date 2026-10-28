/**
 * This version is stamped on May 10, 2016
 *
 * Contact:
 *   Louis-Noel Pouchet <pouchet.ohio-state.edu>
 *   Tomofumi Yuki <tomofumi.yuki.fr>
 *
 * Web address: http://polybench.sourceforge.net
 */
/* gesummv.c: this file is part of PolyBench/C */

#include <stdio.h>
#include <unistd.h>
#include <string.h>
#include <math.h>

/* Include polybench common header. */
#include <polybench.h>

/* Include benchmark-specific header. */
#include "gesummv.h"

#ifdef POLYBENCH_REMAIN_KERNEL
#include <wasm_bench.h>
#endif


/* Array initialization. */
static
void init_array(int n,
		DATA_TYPE *alpha,
		DATA_TYPE *beta,
		DATA_TYPE POLYBENCH_2D(A,N,N,n,n),
		DATA_TYPE POLYBENCH_2D(B,N,N,n,n),
		DATA_TYPE POLYBENCH_1D(x,N,n))
{
  int i, j;

  *alpha = 1.5;
  *beta = 1.2;
  for (i = 0; i < n; i++)
    {
      x[i] = (DATA_TYPE)( i % n) / n;
      for (j = 0; j < n; j++) {
	A[i][j] = (DATA_TYPE) ((i*j+1) % n) / n;
	B[i][j] = (DATA_TYPE) ((i*j+2) % n) / n;
      }
    }
}


/* DCE code. Must scan the entire live-out data.
   Can be used also to check the correctness of the output. */
#ifndef POLYBENCH_REMAIN_KERNEL
static
void print_array(int n,
		 DATA_TYPE POLYBENCH_1D(y,N,n))

{
  int i;

  POLYBENCH_DUMP_START;
  POLYBENCH_DUMP_BEGIN("y");
  for (i = 0; i < n; i++) {
    if (i % 20 == 0) fprintf (POLYBENCH_DUMP_TARGET, "\n");
    fprintf (POLYBENCH_DUMP_TARGET, DATA_PRINTF_MODIFIER, y[i]);
  }
  POLYBENCH_DUMP_END("y");
  POLYBENCH_DUMP_FINISH;
}
#endif


/* Main computational kernel. The whole function will be timed,
   including the call and return. */
static
void kernel_gesummv(int n,
		    DATA_TYPE alpha,
		    DATA_TYPE beta,
		    DATA_TYPE POLYBENCH_2D(A,N,N,n,n),
		    DATA_TYPE POLYBENCH_2D(B,N,N,n,n),
		    DATA_TYPE POLYBENCH_1D(tmp,N,n),
		    DATA_TYPE POLYBENCH_1D(x,N,n),
		    DATA_TYPE POLYBENCH_1D(y,N,n))
{
  int i, j;

#pragma scop
  for (i = 0; i < _PB_N; i++)
    {
      tmp[i] = SCALAR_VAL(0.0);
      y[i] = SCALAR_VAL(0.0);
      for (j = 0; j < _PB_N; j++)
	{
	  tmp[i] = A[i][j] * x[j] + tmp[i];
	  y[i] = B[i][j] * x[j] + y[i];
	}
      y[i] = alpha * tmp[i] + beta * y[i];
    }
#pragma endscop

}


#ifdef POLYBENCH_REMAIN_KERNEL

/* Host-owned lifecycle; original initialization and kernel are unchanged. */
static int n = N;
static DATA_TYPE alpha;
static DATA_TYPE beta;
WB_DECLARE_2D(DATA_TYPE, A, N, N);
WB_DECLARE_2D(DATA_TYPE, B, N, N);
WB_DECLARE_1D(DATA_TYPE, tmp, N);
WB_DECLARE_1D(DATA_TYPE, x, N);
WB_DECLARE_1D(DATA_TYPE, y, N);

void bench_init(void)
{
  WB_RESET(A);
  WB_RESET(B);
  WB_RESET(tmp);
  WB_RESET(x);
  WB_RESET(y);
  init_array (n, &alpha, &beta,
	      POLYBENCH_ARRAY(A),
	      POLYBENCH_ARRAY(B),
	      POLYBENCH_ARRAY(x));
  wb_output_count = 1;
  WB_OUTPUT_1D(0, y, n);
  wb_ready = 1;
}

void bench_run(void)
{
  wb_require(wb_ready);
  kernel_gesummv (n, alpha, beta,
		  POLYBENCH_ARRAY(A),
		  POLYBENCH_ARRAY(B),
		  POLYBENCH_ARRAY(tmp),
		  POLYBENCH_ARRAY(x),
		  POLYBENCH_ARRAY(y));
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
  WB_FREE(B);
  WB_FREE(tmp);
  WB_FREE(x);
  WB_FREE(y);
  wb_ready = 0;
  wb_output_count = 0;
}

#else
int main(int argc, char** argv)
{
  /* Retrieve problem size. */
  int n = N;

  /* Variable declaration/allocation. */
  DATA_TYPE alpha;
  DATA_TYPE beta;
  POLYBENCH_2D_ARRAY_DECL(A, DATA_TYPE, N, N, n, n);
  POLYBENCH_2D_ARRAY_DECL(B, DATA_TYPE, N, N, n, n);
  POLYBENCH_1D_ARRAY_DECL(tmp, DATA_TYPE, N, n);
  POLYBENCH_1D_ARRAY_DECL(x, DATA_TYPE, N, n);
  POLYBENCH_1D_ARRAY_DECL(y, DATA_TYPE, N, n);


  /* Initialize array(s). */
  init_array (n, &alpha, &beta,
	      POLYBENCH_ARRAY(A),
	      POLYBENCH_ARRAY(B),
	      POLYBENCH_ARRAY(x));

  /* Start timer. */
  polybench_start_instruments;

  /* Run kernel. */
  kernel_gesummv (n, alpha, beta,
		  POLYBENCH_ARRAY(A),
		  POLYBENCH_ARRAY(B),
		  POLYBENCH_ARRAY(tmp),
		  POLYBENCH_ARRAY(x),
		  POLYBENCH_ARRAY(y));

  /* Stop and print timer. */
  polybench_stop_instruments;
  polybench_print_instruments;

  /* Prevent dead-code elimination. All live-out data must be printed
     by the function call in argument. */
  polybench_prevent_dce(print_array(n, POLYBENCH_ARRAY(y)));

  /* Be clean. */
  POLYBENCH_FREE_ARRAY(A);
  POLYBENCH_FREE_ARRAY(B);
  POLYBENCH_FREE_ARRAY(tmp);
  POLYBENCH_FREE_ARRAY(x);
  POLYBENCH_FREE_ARRAY(y);

  return 0;
}

#endif /* POLYBENCH_REMAIN_KERNEL */
