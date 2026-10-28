# PolyBench host-driven wasm32 / wasm64 experiments

## 范围

30 项基准支持 `POLYBENCH_REMAIN_KERNEL`，逐项编译命令见各目录的 `readme.md`。
包括 GEMM 在内的全部程序使用同一公共头文件、`bench_*` 接口和 `utilities/run_wasmtime.py`。
`medley/nussinov/Nussinov.orig.c` 是历史参考文件，不作为另一项基准。

原初始化公式、计算内核、默认数据类型和原 `main` 保留。默认 `deriche` 为 float32，
`floyd-warshall` 和 `nussinov` 为 int32，其余 27 项为 float64，不统一改为 double。
新模式只编译基准 `.c`，不要链接 `utilities/polybench.c` 或定义 `POLYBENCH_TIME`。
不定义新宏时仍按原方式编译运行，链接 `utilities/polybench.c`。

## 批量编译

以下命令从仓库根目录执行，需要 Emscripten 6.0.10 或兼容版本，无需 Makefile 或构建脚本。
切换数据集时使用单独目录或完整重建，避免混用旧产物。

```bash
mkdir -p build
dataset=MEDIUM
exports='["_bench_init","_bench_run","_bench_checksum","_bench_free","_bench_abi_version","_bench_pointer_bits","_bench_output_count","_bench_output_type","_bench_output_rank","_bench_output_region","_bench_output_dim","_bench_output_stride","_bench_output_offset","_bench_param_count","_bench_param"]'
for source in datamining/*/*.c linear-algebra/blas/*/*.c \
              linear-algebra/kernels/*/*.c linear-algebra/solvers/*/*.c \
              medley/*/*.c stencils/*/*.c; do
  case "$source" in
    */Nussinov.orig.c) continue ;;
  esac
  name=$(basename "$source" .c)
  directory=$(dirname "$source")
  for bits in 32 64; do
    emcc -m${bits} -O3 -ffp-contract=off \
      -D${dataset}_DATASET -DPOLYBENCH_REMAIN_KERNEL \
      -Iutilities -I"$directory" "$source" \
      --no-entry -sSTANDALONE_WASM=1 \
      -sINITIAL_MEMORY=536870912 -sALLOW_MEMORY_GROWTH=0 \
      -sABORTING_MALLOC=0 -sASSERTIONS=0 -sFILESYSTEM=0 \
      "-sEXPORTED_FUNCTIONS=$exports" \
      -o "build/${name}-remain-kernel-wasm${bits}.wasm" || break 2
  done
  cc -std=c11 -D_POSIX_C_SOURCE=200112L -O3 -ffp-contract=off \
    -D${dataset}_DATASET -DPOLYBENCH_REMAIN_KERNEL \
    -Iutilities -I"$directory" -shared -fPIC "$source" -lm \
    -o "build/lib${name}-remain-kernel.so" || break
done
```

原生库命令适用于 Linux/macOS；macOS 使用 `.so` 扩展名时实际仍是 Mach-O 动态库。
原生参考和 Wasm 必须使用相同数据集、数据类型、padding 和浮点设置。

## 运行

```bash
python3 -m venv .venv-polybench
source .venv-polybench/bin/activate
python -m pip install wasmtime==50.0.1

# 单项，也可以在 --benchmark 后列出多个名称。
python utilities/run_wasmtime.py --benchmark bicg \
  --build-dir build --native-dir build --json build/bicg-results.json

# 全部 30 项（包括 GEMM），每种位宽默认预热 5 次、采样 31 次。
python utilities/run_wasmtime.py --benchmark all \
  --build-dir build --native-dir build --json build/all-results.json
```

GEMM 已与其他程序统一，不再保留 `GEMM_REMAIN_KERNEL`、`gemm_*` 和独立运行脚本。
从旧版本升级时，重新编译 GEMM 的两个 Wasm 文件和原生库；单项使用 `--benchmark gemm`，批量使用 `--benchmark all`。

Python 包内置自己的 Wasmtime 引擎，不调用系统的 CLI。脚本要求模块零导入，不伪造 WASI 接口。
`--native-dir` 要求每项原生库存在，缺失时失败；省略它时明确标记为 cross-mode only。
两种 Wasm 一致不是独立正确性证明；原生参考也使用同一内核与适配层，不是独立数学实现。

| 选项 | 默认值/作用 |
| --- | --- |
| `--warmups` | 5，至少 1 |
| `--samples` | 31，至少 5 |
| `--seed` | 20261027，每轮随机决定 32/64 先后顺序 |
| `--rtol` / `--atol` | 显式覆盖浮点容差并记录；整数始终精确比较 |
| `--allow-nonfinite` | 调查时允许对应 NaN/Inf，结果标记 `PASS_NONFINITE_ALLOWED`，不表示数值有效 |
| `--json` | 保存环境、参数、校验状态、输出描述、二进制哈希、原始样本和统计 |

默认拒绝 NaN/Inf。如果原生参考也异常，应调查原输入或算法条件，不自动改算法或放宽容差。
批量运行某项失败会记录 `FAIL` 并继续其他项目，最终退出码为 1。JSON 每项完成后更新。
不要将失败项或允许非有限值的结果混入正常性能结论。

## ABI v1

一个模块/原生共享库包含一个基准翻译单元。先实例化，若存在 `_initialize` 则调用一次，
然后在同一实例中执行 `bench_init → bench_run → 读取结果`。每个样本都重新初始化输入。

| 导出 | 返回值及约定 |
| --- | --- |
| `bench_abi_version()` | int，当前为 1 |
| `bench_pointer_bits()` | int，Wasm 分别为 32/64 |
| `bench_init()` | 首次分配，每次恢复输入；通用适配层同时清零工作数组 |
| `bench_run()` | 原计算内核，未初始化时 trap |
| `bench_free()` | 释放并清空指针，重复调用安全，之后可重新初始化 |
| `bench_param_count()` / `bench_param(index)` | 有序问题参数，包括适用的时间步数 |
| `bench_output_count()` | 结果数组数量，需要先初始化 |
| `bench_output_type(index)` | 1=float64、2=float32、3=int32 |
| `bench_output_rank(index)` | 1、2 或 3 |
| `bench_output_dim(index, axis)` | 不含 padding 的逻辑维度 |
| `bench_output_stride(index, axis)` | uint64，包含 padding 的字节步长 |
| `bench_output_region(index)` | 0=全部、1=下三角含对角线、2=上三角含对角线 |
| `bench_output_offset(index)` | uint64，Wasm 内存偏移，原生库中为 uintptr 地址 |
| `bench_checksum(index)` | double，按行主序遍历该输出的有效区域 |

offset/stride 在两种 Wasm 中都显式使用 i64。宿主使用 Python 整数处理，不截断 memory64 偏移；
内部指针和 size_t 仍按目标位宽编译。读取结果时必须考虑元素类型、多输出、维度、步长和区域。

保留 4096 字节对齐堆分配，支持 `POLYBENCH_PADDING_FACTOR` 的行/平面 padding。
新模式明确拒绝 `POLYBENCH_STACK_ARRAYS` 和 inter-array padding，原模式不受影响。
cholesky/lu/ludcmp 初始化内的临时分配仍在原位置；内核局部数组不移出计时区间。

## 校验与计时

检查真实指针位宽、内存类型、512 MiB 内存、零导入、ABI 版本、问题参数和输出布局。
逐元素比较两种 Wasm，以及提供的原生参考。检查宿主读取总和、导出 checksum、释放后重初始化、
每个样本的 checksum 和最终完整输出。三角区域只读取有效元素，避免读取未初始化区域。

int32 精确比较；默认 float32 使用 rtol=1e-4/atol=1e-5，float64 使用 rtol=1e-8/atol=1e-9。
原生参考不能排除共有适配错误，应结合原程序输出、原函数保持不变的检查和算法性质解释结果。

只计量一次 Python→Wasmtime 调用及原内核，包含调用/时钟开销。加载、编译、实例化、分配、
输入初始化、预热、原生参考、校验和输出不计入。极小数据集可能由调用开销主导。
MINI/SMALL 用于校验和回归，MEDIUM 用于完整校验及每种位宽 31 次采样。

默认采用“恢复输入、不显式冲刷缓存”。初始化会预先访问数据，不保证工作集完全驻留缓存，
也不等同于原 PolyBench 默认缓存冲刷模式。两种位宽统一启用 memory64、Cranelift、speed 优化，
固定内存为 512 MiB。更大数据集要先核算所有数组和局部栈预算。

`T64/T32` 是 wasm64 中位耗时 / wasm32 中位耗时，大于 1 表示 wasm64 较慢。
跨程序应逐项报告；若给总指标，需说明几何平均和异常项处理，不将毫秒跨程序直接算术平均。
推荐固定 CPU、多次独立启动，并记录硬件、系统、编译器和运行时版本。

## 验证记录

先前版本已验证全部 30 项 MEDIUM 的 wasm32、wasm64 和原生参考结果，以及原 29 项的 MINI/SMALL 和旧模式。
本次统一后，在 macOS arm64、Emscripten 6.0.10、Python Wasmtime 50.0.1 下，
按本页无特殊分支的命令重新构建全部 30 项 MEDIUM 的 wasm32、wasm64 和原生参考。
30 项均通过零导入、逐元素原生参考、重初始化和最终完整输出校验，每种位宽预热 5 次、采样 31 次。
GEMM 原初始化和内核函数体保持不变；非改造模式的 wasm32 程序也编译运行成功。
这些检查验证运行链路，性能结论仍需在目标机器重新测量。
