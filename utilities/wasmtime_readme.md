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
`--native-dir` 要求每项原生库存在，缺失时失败；省略它时，双位宽模式标记为 cross-mode only，
单个位宽模式只检查结果有限性、checksum 和重初始化等自洽性，不表示通过独立参考校验。
两种 Wasm 一致不是独立正确性证明；原生参考也使用同一内核与适配层，不是独立数学实现。

| 选项 | 默认值/作用 |
| --- | --- |
| `--warmups` | 5，至少 1 |
| `--samples` | 31，至少 5 |
| `--seed` | 20261027，每轮随机决定 32/64 先后顺序 |
| `--bits` | `both`，也可选 `32` 或 `64`；单个位宽只加载所选模块，不计算 T64/T32 |
| `--profile` | `none`；`counters` 在 Linux x86_64 上采集内核调用区间的硬件计数器 |
| `--events` | profiling 时默认 `cycles,instructions,branches,branch-misses`；逗号分隔的非重复子集 |
| `--cpu` | 可选，绑定到当前允许的某个 Linux 逻辑 CPU，绑定发生在 Engine 创建前 |
| `--emit-code-dir` | 可选，导出实际执行的编译后模块到 `目录/程序/wasm位宽/module.cwasm`；拒绝覆盖 |
| `--jit-profile` | `none`；`jitdump` 为外部 perf 导出 JIT 地址与机器码映射，独立于 `--profile` |
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

## 内核调用区间的硬件 profiling

此模式使用 Python 标准库 `ctypes` 调用 Linux `perf_event_open`，无需安装 `perf` 命令，
无需修改 C 内核或重新编译已有 Wasm。首版仅支持 Linux x86_64 上的 64 位 Python。
普通模式在其他原有平台上继续可用。硬件/虚拟机必须提供 PMU，且当前用户具有采集权限。

```bash
# 先查看逻辑核与物理核的对应关系，选择一个允许使用的核，并保持其 SMT 兄弟线程空闲。
lscpu -e=CPU,CORE,SOCKET,ONLINE

# 示例使用逻辑 CPU 2，请按实际允许的 CPU 集合调整。
# 单次进程只采集 wasm32；仍用原生参考校验。
python utilities/run_wasmtime.py --benchmark gemm --bits 32 \
  --profile counters --events cycles,instructions,branches,branch-misses \
  --cpu 2 --build-dir build --native-dir build \
  --json build/gemm-wasm32-counters.json

python utilities/run_wasmtime.py --benchmark gemm --bits 64 \
  --profile counters --events cycles,instructions,branches,branch-misses \
  --cpu 2 --build-dir build --native-dir build \
  --json build/gemm-wasm64-counters.json

# 同一进程交替两种位宽，计数按样本、位宽分别保存；也可使用 --benchmark all。
python utilities/run_wasmtime.py --benchmark jacobi-2d gemm nussinov seidel-2d adi \
  --bits both --profile counters --cpu 2 \
  --build-dir build --native-dir build --json build/counters-results.json
```

每个样本执行以下流程，预热不计数：

```text
bench_init() → 读取上一次累计计数时间、重置并开启计数器
             → 开始计时 → bench_run() → 结束计时
             → 关闭计数器 → 读取硬件统计 → 校验结果
```

计数器绑定当前执行线程，不继承给 JIT 工作线程；排除内核态和 hypervisor。
加载、JIT 编译、初始化、预热、原生参考和校验均在计数区间外。
这仍是“宿主调用加内核”的统计：包括 Python/FFI/Wasmtime 进出、时钟读取及计数器控制边界的
少量用户态指令，不能称为纯 Wasm 指令统计。异常退出内核时也关闭计数器。
profiling 下的耗时明确标记，不能直接替换未开启 profiling 的性能基线。
较短内核应另外评估调用和采集开销；每个样本必须恢复输入，不能直接连续调用内核替代。

| 指标 | 含义 |
| --- | --- |
| `cycles` | 当前线程用户态执行消耗的 CPU 周期 |
| `instructions` | 退休的 CPU 机器指令数，不是 Wasm 指令数 |
| `branches` | 退休的分支指令数 |
| `branch-misses` | 预测失败的退休分支数 |
| IPC | instructions / cycles；只有同时选择这两个事件才计算 |

事件组成一个组，同步启停与读取。每个样本的 `records[].counters` 保存 `counts`、
`time_enabled_ns`、`time_running_ns`、`running_ratio` 和 `ipc`。
Linux 的 RESET 不清零累计计数时间，脚本按样本计算时间差。
`counter_summary` 按位宽保存总计数、每次调用计数的中位数/均值、最小运行比例，
以及用总 instructions / 总 cycles 计算的 IPC（不平均每次的 IPC）。
单个位宽结果的 `cross_mode_validation` 为 false，`median_time_ratio_64_over_32` 为 null，
但仍保留所有所选模块的校验和二进制哈希。报告记录实际 CPU affinity。

首版采用严格策略：计数器未运行、读取不完整或 `time_running != time_enabled` 时该项失败，
不将复用计数自动放大为估算值。可减少事件，例如先采 `--events cycles,instructions`，
再单独采 `--events branches,branch-misses`，或关闭竞争使用 PMU 的其他 profiler。
权限不足或 PMU 不可用时会给出系统错误，检查 `/proc/sys/kernel/perf_event_paranoid`、
管理员授予的 `CAP_PERFMON` 及虚拟机 PMU 支持；脚本不会修改权限配置或静默退回计时模式。
失败记录为 `FAIL`，最终退出码为 1，不能将失败项混入硬件分析。

建议在目标机器多次独立运行并交替 32/64 顺序，另外记录实际频率、温度、CPU 型号和编译命令。
计数器模式不采集热点样本或缓存/TLB 事件；下面的独立采集流程提供 JIT 映射和热点报告。
接口依据：[Linux perf_event_open 手册](https://man7.org/linux/man-pages/man2/perf_event_open.2.html)。

## 完整汇编与通用热点采样

`utilities/collect_wasmtime_profiles.py` 复用统一运行器，每个程序、每种位宽单独启动进程，
保存完整机器码及分析材料。已有 Wasm 无需重新编译。应在产生性能数据的 Linux x86_64 机器上
执行采样；在 macOS arm64 上导出的代码是 ARM64，不能用来解释 x86-64 实验。

### 只导出完整汇编

先激活之前的 Python 环境。从仓库根目录执行：

```bash
python utilities/collect_wasmtime_profiles.py \
  --benchmark all --bits both --mode assembly \
  --build-dir build --native-dir build \
  --output-dir build/assembly-v1
```

此模式不需要 perf 或 PMU 权限，需要 GNU `objdump` 或兼容的 `llvm-objdump`，
可用 `--objdump /path/to/llvm-objdump` 指定。默认每项每种位宽预热 5 次、执行 5 次用于校验。
支持当前主机为 Linux/macOS 的 x86_64/arm64；产物必须与主机架构一致。
不提供 `--native-dir` 时只有单个位宽的自洽性检查，完整汇编本身不是正确性证明。

运行器调用 `module.serialize()` 保存随后用于实例化执行的同一个 Module。
不是调用另外一个 Wasmtime CLI 重新编译；所有导出和反汇编操作都在内核计时区间外。
批量脚本对完整 `.text` 使用 `objdump -D -z --section=.text`，x86-64 使用 Intel 语法。
`full.asm` 包含未采样命中的冷代码和零字节区间；填充和嵌入数据也可能被解码成指令，
分析时结合 `symbols.txt` 的函数范围，不把所有反汇编行直接当作实际执行的指令。
完整范围是该模块生成的机器码，不包括 Python/Wasmtime 宿主库，也不是逐条动态执行轨迹。
优化删除/内联的 C 函数不一定保留独立符号；无调试名字时可能显示 `wasm[0]::function[N]`。

### 完整汇编加热点采样

需要 Linux x86_64、可访问的 PMU、`perf` 和 `objdump`。Ubuntu 可安装匹配运行内核的工具：

```bash
sudo apt install linux-tools-common linux-tools-$(uname -r) binutils
perf --version
objdump --version
```

首先采一项确认链路，随后批量采集。输出目录必须不存在：

```bash
python utilities/collect_wasmtime_profiles.py \
  --benchmark gemm --bits both --mode sample \
  --build-dir build --native-dir build --cpu 2 \
  --samples 1001 --output-dir build/gemm-profile-v1

# 使用之前的完整结果 JSON 估算各内核所需次数。
# 把 build/all-results-v2.json 替换为实验机上真实的结果文件路径。
python utilities/collect_wasmtime_profiles.py \
  --benchmark all --bits both --mode sample \
  --build-dir build --native-dir build --cpu 2 \
  --baseline-results build/all-results-v2.json \
  --target-kernel-seconds 10 --max-samples 100000 \
  --timeout 600 --output-dir build/all-profiles-v1
```

`--cpu 2` 必须是当前允许使用的逻辑核，保持其 SMT 兄弟线程空闲。每种位宽按 32、64 顺序单独
采样；重复实验可用 `--bits 64` / `--bits 32` 分别执行并交替顺序。不要把采样下的耗时替换
普通计时基线。若 PMU 权限不足，按上一节检查权限，脚本不修改系统安全设置。

| 选项 | 批量采集脚本的行为 |
| --- | --- |
| `--benchmark` / `--bits` | 复用 30 项名称及 `all`，位宽默认 `both` |
| `--mode` | 默认 `sample`；`assembly` 只执行校验和完整代码导出 |
| `--output-dir` | 必填的新目录，已存在即拒绝，避免覆盖实验 |
| `--samples` | 显式次数，至少 5；优先于基线估算，不受 `--max-samples` 限制 |
| `--baseline-results` | 用每种位宽的中位耗时估算次数；要求该项 PASS 且 Wasm SHA-256 匹配 |
| `--target-kernel-seconds` | 默认 10，估算累计正式内核调用时间；只在使用基线估算时生效 |
| `--max-samples` | 默认 100000，仅限制估算次数；达到上限会提示并记录，目标时间可能不足 |
| `--frequency` | 默认 997 Hz，perf 请求频率；实际可能被系统调整 |
| `--timeout` | 每个外部命令默认最多 600 秒；超时终止其进程组并保留失败日志 |
| `--warmups` | 默认 5，至少 1 |

采样模式不提供基线也不指定次数时，默认 1001 次。基线估算使用
`ceil(目标秒数 * 1000 / median_ms)`，下限 5；基线只是时间估计，并不保证相同 CPU 或运行环境。
初始化、校验和编译会增加总耗时。manifest 同时记录目标、估计、实际计时区间累计耗时和任务总耗时。
显式 `--samples` 或 assembly 模式不使用基线估算。

实际流程为 `perf record -e cycles:u -F 997 -k mono` 启动运行器（`--profile none --jit-profile jitdump`），
导出完整代码后执行 `perf inject --jit`、`perf report`、`perf annotate`。
采样覆盖整个进程，包括初始化、JIT、校验、预热；不是正式 `bench_run()` 区间的精确采样。
`report.txt` 列出全进程热点，`annotated.asm` 限定 JIT 生成的 ELF 文件，避免误读原生参考或 Python 的同名函数，
但其中仍可能包含 JIT 初始化函数、checksum 和预热调用。进一步按 `bench_run` 或内联后的计算循环筛选。
既有 counters 模式继续负责正式调用区间的硬件计数；批量采样不同时开启它。

### 产物及失败处理

```text
all-profiles-v1/
  manifest.json                 # 环境、工具版本、命令参数、基线哈希及所有任务状态
  cpuinfo.txt                   # Linux CPU 信息
  gemm/
    wasm32/
      module.cwasm              # 实际执行模块的序列化编译产物
      full.asm                  # 完整 .text 反汇编，独立于热点样本
      symbols.txt               # 符号表与函数范围
      run-results.json          # 校验、计时、Wasm/编译产物哈希、Engine 配置、CPU affinity
      job.json                  # 任务命令、返回码、耗时、文件清单、错误
      command.log               # 命令及 stderr
      run.log                   # 运行器 stdout
      perf.data                 # 以下文件仅 sample 模式产生
      perf.jit.data
      inject.log
      report.txt
      annotated.asm
      jit-*.dump
      jitted-*.so
    wasm64/
      ...
```

保留整个输出目录和 JIT 文件，分析期间不要移动或删除；perf 数据可能包含绝对路径。
单项失败会继续后续任务，manifest 每项更新，任何失败最终返回退出码 1。中断返回 130，
当前任务状态保存在其 job.json。若采样后处理失败，但 `assembly_status` 为 PASS，
该项的 `module.cwasm/full.asm/symbols.txt` 仍可用于静态分析。
PASS 表示执行、校验和产物生成通过，不保证每个内核已采集足够样本；检查 report 和采样日志。
缺少 JIT ELF 映射或没有可注释的样本时明确失败，不把空文件视为完整分析结果。

也可只用原运行器保存编译产物，之后自行反汇编：

```bash
python utilities/run_wasmtime.py --benchmark all --bits both \
  --build-dir build --native-dir build \
  --emit-code-dir build/compiled-v1 --json build/compiled-v1-results.json

objdump -D -z --section=.text -M intel \
  build/compiled-v1/gemm/wasm64/module.cwasm > build/gemm-wasm64-full.asm
```

源码行号并非生成汇编的前提。当前流程不自动添加调试编译参数或改变优化设置；如需源码行映射，
另建带调试信息的产物并配置 Wasmtime debug_info，记录它与当前性能基线的区别。
参考：[Wasmtime JIT/perf 工作流](https://docs.wasmtime.dev/examples-profiling-perf.html)。

## 验证记录

完整汇编流程已在 macOS arm64、Python Wasmtime 50.0.1 上实际执行全部 30 项、两种位宽共 60 个
MEDIUM 模块：均通过原生参考校验，生成 `.cwasm`、完整汇编和符号表；检查反汇编地址覆盖了各模块
完整 `.text` 区间。另检查序列化产物可重新加载执行、已有目录/产物拒绝覆盖、缺失输入后继续任务。
样本数估算、哈希不匹配、超时清理、JIT 后处理顺序与缺失映射失败通过模拟工具检查。
Linux x86_64 的真实 `perf record/inject/annotate` 尚未在本地执行，需在目标实验机验证；
上述 ARM64 汇编与模拟采样均不作为 x86-64 性能证据。

新增 profiling 后，在 macOS arm64 上完成全部 30 项 MEDIUM 的普通模式回归（两种位宽、原生参考，
每种位宽预热 1 次、采样 5 次），全部通过。单个位宽在另一模块不存在时通过，
非法参数和不支持平台的失败报告也已检查。模拟系统接口检查了 ABI 布局、事件 ID 对应、
累计时间差、未运行/复用拒绝、部分打开失败时关闭描述符，以及内核异常时关闭计数器；
模拟计数器配合真实 Wasm 验证了 JSON 汇总链路。这些模拟计数不是硬件测量数据。
Linux x86_64 的真实 PMU 采集仍需在实验机验证，不能由上述 macOS 检查替代。

先前版本已验证全部 30 项 MEDIUM 的 wasm32、wasm64 和原生参考结果，以及原 29 项的 MINI/SMALL 和旧模式。
本次统一后，在 macOS arm64、Emscripten 6.0.10、Python Wasmtime 50.0.1 下，
按本页无特殊分支的命令重新构建全部 30 项 MEDIUM 的 wasm32、wasm64 和原生参考。
30 项均通过零导入、逐元素原生参考、重初始化和最终完整输出校验，每种位宽预热 5 次、采样 31 次。
GEMM 原初始化和内核函数体保持不变；非改造模式的 wasm32 程序也编译运行成功。
这些检查验证运行链路，性能结论仍需在目标机器重新测量。
