# 在 Wasmtime 中比较 GEMM 的 wasm32 / wasm64

## 改动

`gemm.c` 新增可选的 `GEMM_REMAIN_KERNEL` 模式，原来的 `init_array` 和
`kernel_gemm` 函数体保持不变。也支持公共的 `POLYBENCH_REMAIN_KERNEL` 开关；两个开关均未定义时，仍编译原来的 PolyBench 命令行程序。

定义这个宏后，不再编译原来的 `main(argc, argv)` 和 `print_array`，也不使用
PolyBench 的内部计时、缓存冲刷和标准输出。模块改为由宿主调用的 reactor：

| 导出函数 | 用途 |
| --- | --- |
| `gemm_init()` | 首次分配数组，每次恢复 A、B、C、alpha 和 beta |
| `gemm_run()` | 调用原 GEMM 计算内核；必须先初始化 |
| `gemm_checksum() -> f64` | 遍历所有 C 元素求和，让结果保持可观察，供宿主校验 |
| `gemm_c_offset() -> i64` | 返回 C 的线性内存偏移，供宿主读取整个结果 |
| `gemm_ni/nj/nk() -> i32` | 返回实际矩阵维度 |
| `gemm_row_stride() -> i32` | 返回 C 每行包含的元素数，包括可选 padding |
| `gemm_pointer_bits() -> i32` | 返回 C 指针位数，检查实际编译目标 |
| `gemm_free()` | 释放数组；释放后可重新初始化 |

分配仍使用 4096 字节对齐的 `posix_memalign`。分配失败时直接 trap，不通过
stdio 或 `exit` 报错。`gemm_c_offset` 在两种构建中都显式返回 `uint64_t`，
宿主不能把该偏移截断为 32 位。

这样构建出的模块不需要 `args_sizes_get`、时钟或标准输出等 WASI 导入，避免
wasm64 的 C 指针 ABI 与 Wasmtime WASI Preview 1 的 32 位地址接口冲突。
下面的运行命令会实际检查导入表，不使用空函数伪造这些接口。

## 编译

以下所有命令均在 **PolyBench 仓库根目录** 执行。需要可用的 Emscripten；
以下配置已使用 Emscripten 6.0.10 验证。直接执行下列命令编译，无需 Makefile。

```bash
mkdir -p build

for bits in 32 64; do
  emcc -m${bits} \
    -O3 -ffp-contract=off \
    -DMEDIUM_DATASET -DDATA_TYPE_IS_DOUBLE \
    -DGEMM_REMAIN_KERNEL \
    -Iutilities \
    -Ilinear-algebra/blas/gemm \
    linear-algebra/blas/gemm/gemm.c \
    --no-entry \
    -sSTANDALONE_WASM=1 \
    -sINITIAL_MEMORY=536870912 \
    -sALLOW_MEMORY_GROWTH=0 \
    -sABORTING_MALLOC=0 \
    -sASSERTIONS=0 \
    -sFILESYSTEM=0 \
    '-sEXPORTED_FUNCTIONS=["_bench_init","_bench_run","_bench_checksum","_bench_free","_bench_abi_version","_bench_pointer_bits","_bench_output_count","_bench_output_type","_bench_output_rank","_bench_output_region","_bench_output_dim","_bench_output_stride","_bench_output_offset","_bench_param_count","_bench_param","_gemm_init","_gemm_run","_gemm_checksum","_gemm_c_offset","_gemm_ni","_gemm_nj","_gemm_nk","_gemm_row_stride","_gemm_pointer_bits","_gemm_free"]' \
    -o build/gemm-remain-kernel-wasm${bits}.wasm
done
```

与原来的编译命令相比：不编译 `utilities/polybench.c`，不定义 `POLYBENCH_TIME`；
增加 `GEMM_REMAIN_KERNEL`、`--no-entry` 和显式导出列表。
两边都显式关闭浮点乘加收缩，便于独立数值校验；不要只给一种位宽设置此选项。

可先使用 CLI 检查目标位数：

```bash
wasmtime run --invoke gemm_pointer_bits build/gemm-remain-kernel-wasm32.wasm
wasmtime run -W memory64=y --invoke gemm_pointer_bits build/gemm-remain-kernel-wasm64.wasm
```

预期分别返回 `32`、`64`。模块没有 `_start`，单纯 `wasmtime run file.wasm`
不会执行原来的完整基准。也不要在不同 CLI 进程中分别调用 init 和 run：
不同进程创建不同实例，数组状态不会保留。

## 公共运行脚本（包含在 `--benchmark all` 中）

GEMM 新增公共 ABI v1 的 `bench_*` 别名，复用原来的初始化、计算、checksum 和释放逻辑。
上面的编译命令同时导出 `bench_*` 和 `gemm_*`，既可使用公共脚本，也可使用原独立脚本。
旧的 Wasm 若只导出了 `gemm_*`，需重新编译后再使用公共脚本。

构建与上述 MEDIUM/double 配置一致的原生参考：

```bash
cc -std=c11 -D_POSIX_C_SOURCE=200112L -O3 -ffp-contract=off \
  -DMEDIUM_DATASET -DDATA_TYPE_IS_DOUBLE -DGEMM_REMAIN_KERNEL \
  -Iutilities -Ilinear-algebra/blas/gemm -shared -fPIC \
  linear-algebra/blas/gemm/gemm.c -lm -o build/libgemm-remain-kernel.so
```

在已安装 `wasmtime==50.0.1` 的 Python 环境中运行：

```bash
python utilities/run_wasmtime.py --benchmark gemm \
  --build-dir build --native-dir build --json build/gemm-results.json
```

使用公共批量编译命令准备好其他程序后，`--benchmark all` 会运行包括 GEMM 的全部 30 项。
完整批量命令和公共接口说明见 [公共实验说明](../../../utilities/wasmtime_readme.md)。

## GEMM 独立运行脚本

使用 Python 的 Wasmtime 嵌入 API，在**同一个实例**上依次调用初始化、计算和校验。
Python 包内置自己的 Wasmtime 引擎，不调用系统中的 `wasmtime` CLI；
下面固定为 `50.0.1`，实际版本会打印出来。CLI 的 `-W` 选项不会传给 Python。

准备依赖：

```bash
python3 -m venv .venv-gemm
source .venv-gemm/bin/activate
python -m pip install wasmtime==50.0.1
```

宿主脚本保存在同目录的 `run_wasmtime.py` 中，负责校验、预热、计时和输出统计结果。
默认 MEDIUM 数据集会完整校验每种模块的 44,000 个结果元素。从仓库根目录执行：

```bash
python linear-algebra/blas/gemm/run_wasmtime.py
```

`T64/T32` 大于 1 表示 wasm64 中位耗时更长。模块加载、编译、实例化、reactor 初始化、
分配、输入初始化、预热、结果读取、校验和输出均不在计时区间内。
计量值包含一次 Python→Wasmtime 调用及宿主时钟读取开销；过小的数据集可能受其影响。

每次计时前必须重置输入：原内核计算 `C = alpha*A*B + beta*C`，在上次结果上直接继续运行
会改变工作负载。checksum 遍历全部结果保留计算的可观察性；完整逐元素参考校验则避免
仅比较 checksum 漏掉相互抵消的错误。

本模式不做显式缓存冲刷，初始化会预先访问输入数据；不保证全部数据驻留于缓存。
原 PolyBench 默认计时前有缓存冲刷，因此不能把这里的绝对耗时直接与原命令输出比较。
要比较位宽，应对两种模块使用这套相同的框架、数据集、运行时版本和配置。

## 原来的运行方式

不定义 `GEMM_REMAIN_KERNEL` 时保留原行为，例如：

```bash
emcc -m32 -O3 -DMEDIUM_DATASET -DPOLYBENCH_TIME \
  -Iutilities -Ilinear-algebra/blas/gemm \
  utilities/polybench.c linear-algebra/blas/gemm/gemm.c \
  -sSTANDALONE_WASM=1 -sINITIAL_MEMORY=536870912 \
  -o build/gemm-original-wasm32.wasm

wasmtime run build/gemm-original-wasm32.wasm
```

这保留了兼容性，但原来的 wasm64 命令仍可能遇到本文开头说明的 WASI ABI 冲突。
