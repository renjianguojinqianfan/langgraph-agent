# harness 里跑离线回归的 bash 假红

> 2026-11-01 实测，覆盖 opencode / qoder / dsh 三个 agent。**触发与 agent 用哪种 shell 无关**，但各家
> harness 的**限制范围**不同：opencode / qoder 只限制「从仓库目录内启动的进程」，dsh 限制它执行的每一条
> 命令。普通终端与 CI 的 `backend-windows-smoke` 都不命中——只有 harness 里会看到。

## 签名

- 退出码 `3221225794`（`0xC0000142`，STATUS_DLL_INIT_FAILED）。
- stderr 一律是 MSYS 运行时的 `*** fatal error -`；**卡住的那一步随 harness 而不同**（同一个 `.venv311`、同一个 bash）：
  - opencode / qoder：`NtCreateDirectoryObject(\BaseNamedObjects\msys-2.0S5-…): 0xC0000022`（建共享对象目录被拒）
  - dsh：`couldn't create signal pipe, Win32 error 5`（建信号管道被拒）
- 表现：`test_bash_tool.py` 整文件 + 依赖 bash 的 e2e（`test_models.py::test_tool_args_and_final_answer_are_redacted`）集体失败，断言看着像逻辑错。

已实测的 agent（2026-11-01；「仓库内」= 用 `.venv311` 解释器跑探针，「仓库外」= 用 `pyvenv.cfg` 的 `home` 解释器跑同一条探针）：

| agent | shell | 仓库内 | 仓库外 | 限制形状 |
|---|---|---|---|---|
| opencode | Git Bash | 假红（共享对象目录） | **好使** | 按路径：只限制仓库内启动的进程 |
| qoder | Bash 工具 | 假红（共享对象目录） | **好使** | 按路径：只限制仓库内启动的进程 |
| dsh | pwsh | 假红（信号管道） | **仍假红** | 不认路径：包住它执行的每条命令 |

三个全中，所以「换个 agent 就好了」不成立。两件事各自独立：

- **卡住的那一步跟着 shell/血缘走，不跟 agent 名字走**：两个 bash-shelled 的同签名，pwsh-shelled 的是另一个签名——各家 harness 施加的限制并不相同，只是都够挡住 MSYS 初始化。
- **限制的形状跟着 harness 走**：opencode 按路径限制（所以仓库外解释器能解），dsh 不认路径（所以换解释器没用，只能回自己终端）。

## 判据：两条探针，在哪个 agent 里都能跑

**探针一**（自己终端里跑；Git Bash 路径用你机器上的）——分清是机器问题还是 harness 问题：

```powershell
& "<repo-root>\.venv311\Scripts\python.exe" -c "import subprocess; p=subprocess.run([r'<bash>','-c','echo ok'],capture_output=True); print(p.returncode, p.stderr[:100])"
```

`0` → 机器是好的，红来自 harness 血统；仍挂 → 机器层面，见文末第一类。

**探针二**（在 harness 里跑，解释器换成处置第一支那个仓库外的）——分清 harness 有没有出路：好使 → 走处置第一支；仍假红 → 走处置第二支。

## 机理

**已证实**：harness 施加一道限制给子孙进程，使 MSYS 运行时建不了自己要用的内核对象，bash 初始化即死。限制的**范围**随 harness 而不同：opencode / qoder 只覆盖「从仓库目录内启动的进程」，dsh 覆盖它执行的每一条命令（所以仓库外解释器在 dsh 里同样挂）。

边界实测——同一个 `python -m venv` 建出来的 venv，两个 `python.exe` 的 md5 完全相同，只因位置不同结果不同（此表只在 opencode / qoder 下成立）：

| 解释器位置 | spawn bash |
|---|---|
| 仓库内 `.venvtest\Scripts\python.exe` | 挂 |
| `<repo-root>\..\..\venvtest4`（仓库外、同盘、同上级目录） | 好 |
| `<repo-root>\..\..\..\venvtest3`（仓库外、同盘） | 好 |
| `%TEMP%\opencode\venvtest` | 好 |

**已排除**（逐项试过，全都一样挂；这些方向都已查过，无需重查）：uv venv 重定向器（md5 相同的 python.exe 仅因位置不同而结果不同，所以不是它）、venv 工具、site-packages 内容、`close_fds=False`、`-I -S -E`、`os.system` / `os.spawnv` / `cmd start`、四种 `creationflags`、`AllocConsole`、干净环境、restricted SID（= 0）、job 成员（`IsProcessInJob` = False）。限制附着在**父进程**而非子进程路径——仓库内的 bash 副本被仓库外 python 拉起时能正常启动。

> 施加限制的确切方式未定（token 形状与仓库外完全一致、不在 job 里），但边界与传播方向是实测的。

## 处置

代码侧无需改动：发现逻辑 / env 白名单 / pump / Job Object 一行都没执行到，CI 跑同一批是绿的。

- **探针二好使**（opencode / qoder）：限制按路径，换解释器位置即可。用这个跑回归：

  ```bash
  "C:\Users\<user>\AppData\Roaming\uv\python\cpython-3.11.15-windows-x86_64-none\python.exe" -m pytest backend/tests/ -q
  ```

  本机在真解释器的 site-packages 放了 cwd 判定的 `sitecustomize.py`，把仓库 venv 的 site-packages 桥接过去——**不能用 `PYTHONPATH`**：MCP 测试会 spawn 子进程，子进程吃的是 `build_child_env` 白名单，环境变量到不了它。删该文件即回退；cwd 判定保证仓库外无副作用。

- **探针二仍假红**（dsh）：限制不认路径，harness 内没有解释器能解。回自己的终端跑，或在 dsh 里用 `danger-full-access`。

## 另外两类：先判是不是它们

**一、机器层面的残留对象**（2026-11-01 重启前）：`\BaseNamedObjects\msys-2.0S5-<key>` 残留一个 ACL 坏掉的对象，`NtOpenDirectoryObject` 能打开但连读安全描述符都被拒（`0xC0000022`），任何非 MSYS 父进程的 bash 都挂；重启清掉。判据：探针显示对象 name-not-found 但创建仍被拒 → 不是这一类。

**二、dsh 的 ACL sandbox**（本机已复现信号管道签名）：dsh 用受限 token 包它执行的命令，MSYS bash 在建信号管道时就被拒（`couldn't create signal pipe, Win32 error 5`）；上游记录同 token 下 `cmd` / `pwsh` 正常、只有 MSYS 挂，且连 `setTokenDefaultDaclGrant(..., worldSid)` 补丁都救不了完整 MSYS 运行时（它挂在更早的内核对象创建上）。判据：**harness 自己的 bash 工具连 `echo` 都跑不了** → 是这一类，需要 `danger-full-access` 或换非 MSYS shell。本文管的是「harness 的 bash 好使，但仓库内 python spawn 的 bash 挂」。
