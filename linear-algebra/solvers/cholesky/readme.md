# cholesky：wasm32 / wasm64 实验

`cholesky.c` 新增 `POLYBENCH_REMAIN_KERNEL` 模式，保留原 `init_array`、计算内核和默认数据类型。
不定义此宏时，原来的 PolyBench 命令行程序保持可用。本程序使用公共 ABI v1。

- 默认数据类型：**double / float64**，两种位宽使用相同类型。
- 输出顺序：`A`。
- 初始化会恢复输入并清零工作数组；宿主只计时 `bench_run()`。
- 无显式缓存冲刷。不能把绝对耗时直接与原 PolyBench 的默认缓存冲刷模式混合比较。

原初始化函数内部的临时矩阵仍由公共分配实现处理，初始化的计算与临时分配均在计时外。

只读取和校验下三角（含对角线）。

## 编译

以下命令均在仓库根目录执行；需要 Emscripten 6.0.10 或兼容版本。
`dataset` 可选 MINI、SMALL、MEDIUM、LARGE、EXTRALARGE，两种位宽必须取相同值。

```bash
mkdir -p build
dataset=MEDIUM
for bits in 32 64; do
  emcc -m${bits} -O3 -ffp-contract=off \
    -D${dataset}_DATASET -DPOLYBENCH_REMAIN_KERNEL \
    -Iutilities -Ilinear-algebra/solvers/cholesky \
    linear-algebra/solvers/cholesky/cholesky.c \
    --no-entry -sSTANDALONE_WASM=1 \
    -sINITIAL_MEMORY=536870912 -sALLOW_MEMORY_GROWTH=0 \
    -sABORTING_MALLOC=0 -sASSERTIONS=0 -sFILESYSTEM=0 \
    '-sEXPORTED_FUNCTIONS=["_bench_init","_bench_run","_bench_checksum","_bench_free","_bench_abi_version","_bench_pointer_bits","_bench_output_count","_bench_output_type","_bench_output_rank","_bench_output_region","_bench_output_dim","_bench_output_stride","_bench_output_offset","_bench_param_count","_bench_param"]' \
    -o build/cholesky-remain-kernel-wasm${bits}.wasm
done
```

不要链接 `utilities/polybench.c`，不要定义 `POLYBENCH_TIME`。
固定内存为 512 MiB；超大数据集应先检查所有数组及栈是否能放入这个预算。

## 原生参考（建议一起构建）

使用与 Wasm 相同的 `dataset`、源文件、元素类型和浮点收缩设置。在 Linux/macOS 上：

```bash
cc -std=c11 -D_POSIX_C_SOURCE=200112L -O3 -ffp-contract=off \
  -D${dataset}_DATASET -DPOLYBENCH_REMAIN_KERNEL \
  -Iutilities -Ilinear-algebra/solvers/cholesky \
  -shared -fPIC linear-algebra/solvers/cholesky/cholesky.c -lm \
  -o build/libcholesky-remain-kernel.so
```

原生库使用同一内核及适配层，是运行时交叉验证参考，不是独立数学实现。

## 校验和运行

```bash
python3 -m venv .venv-polybench
source .venv-polybench/bin/activate
python -m pip install wasmtime==50.0.1
python utilities/run_wasmtime.py --benchmark cholesky \
  --build-dir build --native-dir build --warmups 5 --samples 31 \
  --json build/cholesky-results.json
```

脚本检查真实指针/内存位宽、零宿主导入、参数和输出布局，按输出类型和有效区域读取结果。
逐元素比较 wasm32、wasm64、原生参考；检查释放后重初始化、每次采样的 checksum 和最终完整输出。
初始化、编译、参考执行、结果校验和打印均在计时区间外；时间包含一次 Python→Wasmtime 调用开销。

默认整数精确比较；float32 容差为 rtol=1e-4、atol=1e-5，float64 为 rtol=1e-8、atol=1e-9。
可通过 `--rtol`、`--atol` 显式设置浮点容差，设置会写入 JSON。省略 `--native-dir` 时只进行两种 Wasm 的交叉校验，输出会明确标记。

程序输出中位数、四分位区间和 `T64/T32`；比值大于 1 表示 wasm64 耗时更长。
原始采样、运行时版本、校验状态、数组描述和二进制哈希保存到指定 JSON。

公共接口及批量运行说明见 [utilities/wasmtime_readme.md](../../../utilities/wasmtime_readme.md)。
