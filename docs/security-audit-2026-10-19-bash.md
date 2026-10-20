# bash 工具安全审计 — 2026-10-19（只读）

> **点-时记录**：结论与 `file:line` 以 `0a28035`（C3 #165 的 merge commit）为准；此后提交会让行号漂移。
> **口径**：只读审计，未改任何代码；本文件是发现存档。当前决定暂不开跟进票（2026-10-19），实际处置见文末；不在本文件维护第二份 backlog。
> **对象**：C3（#158）新增的 `bash` 工具与本机监听面——`backend/core/tools/bash.py`、`backend/utils/sandbox.py`、
> `backend/main.py`、`start.py`、`docker-compose.yml` / `backend/Dockerfile`，以及 README / `.env.example` /
> ADR 0009 / `docs/architecture.md` 的安全口径。
> **方法**：通读代码 + 目标离线测试 + 本机探针（Windows 10、Git Bash 5.2.37、`.venv311` Python 3.11.15）。
> Linux 的 `/proc` 行为在本机 Docker Desktop 的 `docker-desktop` WSL 发行版（root、Yama=1）实测；**未启动完整 Docker 容器**，容器形态按同一内核语义推断。
> **严重度**：**P1** = 默认或推荐部署路径即可越过声称的边界；**P2** = 边界声明失真，或常见误配/普通命令即可触发；
> **P3** = 加固与表述准确性，默认路径不直接暴露。
> **当前适用形态（2026-10-19 追加）**：正文的 P1 按仓库随附形态（含 Docker/Linux）评定；实际使用是
> **Windows 10/11 + Git Bash + `uv tool install -e .` 自用**，不对外分发、不用 Docker、单用户。
> 按该形态重排见文末「附：当前适用形态与下一步修复」——F1/F2/F5 不触发，F3/F6/F7/F8/F4 仍需处置。

## 一句话结论

默认的**本机监听、Host 检查、CORS 与计划/子代理工具面收窄**是成立的，但它们都只是**减少暴露面**，不是权限边界。
按仓库随附形态（含 Docker/Linux），真正要修的集中在三处；**当前 Windows 自用只触发第 3 条**，见文末附记：

1. **Docker 部署默认对外可达**：`docker-compose.yml` 把 `8000` 发布到所有网卡，容器内是 root，`Host: localhost` 可被任何客户端伪造——**Host 检查挡的是 DNS 重绑定，不是访问控制**。
2. **环境变量白名单不是密钥边界**：它只阻止**继承**；`.env`、家目录、`data/` 仍可读。Linux 上子进程可以直接 `tr '\0' '\n' < /proc/$PPID/environ` 读父进程的密钥——本机 Docker Desktop 的 Linux VM（Yama=1）实测可读，不是纸面推断。
3. **停止/超时只回收“还活着的调用树”**：`bash` 退出后仍在跑的后台进程会留下来；继承 stdout/stderr 管道的子进程还会把工具调用拖住最多约 10s，并留下读线程/句柄。

## 发现

| # | 级别 | 发现 | 证据（`file:line`） | 置信 | 建议票 |
|---|---|---|---|---|---|
| F1 | 🔴 P1 | Docker 部署默认对所有网卡发布；容器内 root；Host 检查可被伪造 | `docker-compose.yml:10-11`、`backend/Dockerfile:17`、`backend/main.py:67-68`、`backend/config.py:93-96`；探针 | 1.0 | T1 |
| F2 | 🔴 P1 | 环境白名单不是密钥边界：只挡继承；Linux/Docker 下子进程可直接读父进程 `/proc/<pid>/environ`，文件（`.env` / 家目录 / `data/`）也始终可读 | `backend/utils/sandbox.py:16-49`、`backend/core/tools/bash.py:311`；README:53、`sandbox.py:5-6`；WSL 探针 | 1.0 | T2（+T5 措辞） |
| F3 | 🟠 P2 | 脱离父子关系的后台进程不受停止/超时回收；继承管道会把调用拖住最多约 10s 并泄漏读线程 | `backend/core/tools/bash.py:186-188,342-360`；探针 | 1.0 | T3 |
| F4 | 🟠 P2 | 除单次命令的墙钟超时外，没有 CPU / 内存 / 磁盘 / 进程数上限；超时也不约束工具调用本身 | `bash.py:34-38`、`README.md:40`、ADR 0009:72；无 cgroup / Job Object | 1.0 | T5 |
| F5 | 🟠 P2 | 登录默认关；回环不是本机进程/用户之间的信任边界 | `config.py:93-96`、`backend/services/auth.py:89-120`、README:47-54；G-20 | 1.0 | T5 / T6 |
| F6 | 🟡 P3 | `cors_origins=""` 回退为 `*`（“清空”不等于“关闭跨域”） | `config.py:192-194`；探针 | 1.0 | T4 |
| F7 | 🟡 P3 | 三处安全表述说过头：密钥“不会漏进去”、停止“杀进程树”、本机“只支持本机” | `sandbox.py:5-6`、README:40,53、ADR 0009:34、`.env.example:57-64`、`architecture.md:566` | 1.0 | T5 |
| F8 | 🟡 P3 | 工作区根之外的 `data/`（trace / checkpoint）、`.git`、仓库 `.env` 对 bash 可读写；审计面不可信 | `bash.py:305`；探针；README:52 已声明“不是围墙” | 1.0 | T5 |
| F9 | 🟡 P3 | `bash_path` 显式指向 System32 WSL stub 时被静默忽略；`taskkill` 按 PATH 解析 | `bash.py:66-73,152-157,190-196`；现有测试 | 0.9 | T3（顺带） |

> 置信度说明：F2 的 `/proc` 分支已在本机 Linux VM（Yama=1）实测，文件读取分支也是确定的；其余为代码与实测直接证明。
> 不同内核 / LSM 配置下 `/proc` 的权限检查可能不同，因此 F2 的结论是“**不能把白名单当密钥边界**”，不是“每个 Linux 都必然可读”。

### F1（P1）Docker 部署默认对外可达，Host 检查不是访问控制

**事实**

- `docker-compose.yml:10-11` 是 `- "8000:8000"`。Docker 的端口发布语义是：只写容器端口/宿主端口 → 绑定**所有**宿主接口；只有 `127.0.0.1:8000:8000` 才只绑回环（[Docker 文档](https://docs.docker.com/reference/cli/docker/container/run/)）。
- `backend/Dockerfile:17` 的 `CMD` 直接 `uvicorn ... --host 0.0.0.0`；镜像没有 `USER`，即以 root 运行。
- `backend/main.py:67-68` 的 `TrustedHostMiddleware` 只接受 Host 名 `127.0.0.1` / `localhost`；它拦 DNS 重绑定，**不识别来源地址**。任何能连到宿主 8000 的客户端都可以发 `Host: localhost`。
- 登录默认关（`config.py:93-96`），所有受保护路由在 `auth_enabled=false` 时直接放行（`auth.py:101-103`）。

**本机探针（TestClient，非真实网络）**

```text
base_url=http://192.168.1.5:8000  GET /health        -> 400（Host 不合法）
同一请求 + Host: localhost                          -> 200（Host 检查被伪造）
evil origin preflight                               -> 400, 无 ACAO
自身 origin preflight (http://localhost:5173)        -> 200, ACAO=http://localhost:5173
```

**影响**：在局域网里跑 `docker compose up` 的主机上，任何能访问宿主 8000 的对端都可以创建任务；在模型已配置时即可驱动 agent 执行容器内 bash（root），并读写挂载的 `./data`。CORS 只约束浏览器，命令行客户端完全无视。

**成立的部分**：默认 `start.py` 绑 `127.0.0.1`；Host 检查确实挡住浏览器 DNS 重绑定（恶意域名的 Host 不是 `localhost`）；CORS 默认只放行自身来源。这些都要保留。

**修法（T1）**：compose 改成 `127.0.0.1:8000:8000`；README / `docs/architecture.md` 写清 Docker 必须显式绑回环；考虑容器非 root（需处理 `./data` 挂载权限，可后置）；可选在入口对“非回环 + auth 关”发告警或拒绝。

### F2（P1，Linux/Docker）环境变量白名单不是密钥边界

**事实**

- `backend/utils/sandbox.py:16-49` 用 `ENV_ALLOWLIST` 过滤 `os.environ`，`bash.py:311` 把它传给 `Popen`。这确实阻止了**环境变量继承**：本机探针里 `env` 的 `LLM_API_KEY` 计数为 0。
- 但它挡不住 **`/proc`**。在本机 Docker Desktop 的 `docker-desktop` WSL 发行版（root、Yama=1）里，子进程先把 `SECRET` 从自己的环境里 `unset`，仍能读到父进程的环境：

```text
$ SECRET=abc sh /tmp/probe_env.sh
outer_self=1          # 父进程的 /proc/$$/environ 含 SECRET
inner_read=1          # 子进程已 unset SECRET，仍从 /proc/$PPID/environ 读到
inner_rc=0            # 读取成功（无 Permission denied）
# 把 /proc/sys/kernel/yama/ptrace_scope 临时改为 0 再跑，结果相同；随后恢复为 1
```

  `proc_pid_environ(5)` 说的 ptrace 读检查（[man7](https://man7.org/linux/man-pages/man5/proc_pid_environ.5.html)）在这台内核上**没有挡住同 UID 的 child→parent 读取**；不同内核 / LSM 配置可能不同，但不能把“读不到”当保证。真实 `BashTool` 的父进程是 Python；这条检查依据的是 UID / 父子关系，不依赖父进程语言，因此 `$PPID` 就是 server 进程时同样适用：一条 `tr '\0' '\n' < /proc/$PPID/environ` 即可拿到 `LLM_API_KEY`。完整的 `python:3.11-slim` 容器未启动，测的是同一内核的父子进程语义。
- 它也挡不住**文件**（native 运行时）：默认 cwd 是 `data/artifacts`，`../../.env`、`../../data/traces`、`../../data/checkpoints`、`../../.git`、家目录（`HOME` / `USERPROFILE` 在白名单里）都可读。`.env` 里可能有 `serpapi_key` / `auth_token`；trace / checkpoint 里有对话与工具输出。Docker 里仓库 `.env` 没被复制，但 `./data` 是挂载卷，trace / checkpoint 同样可读。
- **Windows 侧**：本机 MSYS 的 `/proc` 不提供 environ（探针返回 `no-proc-environ`），`env` 也被过滤；同账号调试器 / `ReadProcessMemory` 类手段理论上仍可读，但不是普通 shell 一行。**Windows 的默认路径比 Linux/Docker 稳，但不能把白名单当跨平台密钥边界。**

**影响**：Linux / Docker 下，模型的一条 bash 命令就能读取 server 的 provider key，然后经不受限的网络出站带走（见「不修」）。这直接推翻“密钥不进 bash”。在 Docker 里尤其相关：`docker-compose.yml:15` 把 `llm_api_key` 注入容器环境，而容器以 root 运行。

**修法（T2）**：这是一个需要拍板的票，不是纯文案：

- **接受并写清**（本章最小改法）：把 `sandbox.py:5-6` 与 README:53 改成“只挡继承，不是密钥边界”；在 README 补 `/proc` 与文件可达。这与 ADR 0009“容器 / 虚拟机才是边界”的立场一致。
- **可选代码加固**：Linux 启动时 `prctl(PR_SET_DUMPABLE, 0)`，让同 UID 子进程的 ptrace 读检查失败（除非有 `CAP_SYS_PTRACE`）；需 Linux-only 测试，且会影响调试 / core dump，不是完整沙箱。
- **真隔离**：让 bash 以另一个 UID 运行，或把 server 与命令执行分到不同容器 / 用户；本章不展开。

无论选哪条，都不应再保留“API Key 不会漏进去”的无条件表述。

### F3（P2）脱离的进程与继承管道

**事实**

- 轮询循环（`bash.py:342-353`）在 `proc.poll()` 不为空时直接跳出；`_kill_tree` 在 `bash.py:186-188` 对已退出的直接子进程直接返回。于是**shell 先退出、后台进程留下**的形态不会被停止/超时回收。
- 本机探针（Windows / Git Bash）：

```text
(sleep 3; echo survived > survived.txt) & disown; echo parent-done
  -> 工具返回 success；4.5s 后 survived.txt 存在（子进程存活）

(sleep 8; echo done > marker.txt) & disown; echo parent-done
  -> 工具调用耗时 8.09s（shell 已退出，但子进程仍持有 stdout/stderr 管道）

(echo $$ > child.pid; sleep 60) & disown; echo parent-done
  -> 工具调用耗时 10.12s；返回后仍有 2 个 pump 线程存活，直到子进程被杀
```

- 两个 `pump.join(timeout=5)`（`bash.py:359-360`）是串行等：stdout 5s + stderr 5s，所以最坏约 10s。`_TailBuffer` 只限制**已捕获字节**，不限制这段时间。

**影响**：停止一个任务后可能留下后台进程；反复触发可累积读线程/句柄（资源耗尽）；单次调用可被拖长最多约 10s。停止/超时对**仍在运行的调用树**是有效的（现有 stop 测试通过，Windows `taskkill /T` 与 POSIX `killpg` 都覆盖到后代）。

**修法（T3）**：shell 退出后只给一个很短的 drain 窗口（如 0.5–1s），随后关闭管道让 pump 线程退出；把“脱离进程不受停止/超时管理”写进 `bash.py` 文档与 README。可选的更强做法是 Windows Job Object / POSIX cgroup，按任务管理整棵树——本章不建议展开。

### F4（P2）没有 CPU / 内存 / 磁盘 / 进程数上限

**事实**

- 只有单次命令的墙钟超时（默认 30s，钳在 1–600s，`bash.py:258-273`）与每流 30KB 的输出尾部缓冲（`bash.py:34-35,43-63`）。
- 没有 cgroup、rlimit、Windows Job Object 或进程数限制；命令可以吃满内存、写满磁盘、fork 大量进程、持续占 CPU 直到超时。
- 超时只管 shell 进程本身；F3 的继承管道可把工具调用再拖长最多约 10s。多任务/多命令并发时上限是叠加的。
- API 也没有速率限制；本机调用者可以无限建任务（与 F5 的本地信任面叠加）。

**口径**：ADR 0009 明确“隔离靠容器”（`docs/adr/0009-minimal-agent-chapter.md:72`），本章不承诺资源隔离。**要修的是文档**：README 只写了“可配超时”和“输出截断”，读者容易把它当成资源上限。T5 补一句“只限时，不限 CPU/内存/磁盘/进程数”。

### F5（P2）登录默认关；回环不是本机信任边界

**事实**

- `config.py:93-96` `auth_enabled=false`；`auth.py:101-103` 在关闭时直接返回 `"local"`，所有 `Depends(verify_token)` 路由全部放行。
- 回环是**机器级**的：同一台机器上的其他进程、其他本地用户都可能连上（取决于系统与权限）。Host 检查只认 Host 名，不认调用者；CORS 只管浏览器读响应。DNS 重绑定已被 Host 检查挡住，这一条是成立的。
- 本章的立场是单用户本机 demo；但 README 没有把“默认无认证”写成一条边界。
- 相邻的 P3：开启 auth 后，`verify_token` 在所有受保护路由都接受 `?token=`（`auth.py:92,109`），不只是 SSE；URL 查询串可能进日志 / Referer。默认 auth 关，本章不阻塞；若未来做远程/共享主机，建议 REST 只认 header、query 只留给 SSE。

**dsh 先例与决定**：dsh 每个进程生成随机 launch token，打印在启动 URL 的 `?token=` 里，只允许在根路径交换为 `HttpOnly` / `SameSite=Strict` 的签名 cookie，API 路径不接受 query token，并做 Host/Origin 检查；`dsh web --host 0.0.0.0` **明确不支持**（[dsh connection README](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/client/connection/README.md)）。本仓已在 G-20 拍板：“dsh 式每次启动随机令牌**留作后续加固**”（`docs/capability-registry-rescope.md:34`）。因此本审计**不建议**在 C4–C7 里顺手加；若要，另开 T6，形态照 dsh，但要接受它只挡“无凭据的本地进程”，挡不住同账号恶意代码，也不替代容器。

**修法（T5）**：README「这不是安全边界」补“默认无认证，本机其他进程/用户可直接驱动 API”；`.env.example` 的 auth 注释删掉 “public deployments”（远程访问不在本章范围，见 G-26）。

### F6（P3）`cors_origins=""` 回退为 `*`

`config.py:192-194`：

```python
if self.cors_origins in ("*", ""):
    return ["*"]
```

本机探针：`Settings(cors_origins="")` → `["*"]`。默认值是四个本机来源，安全；但“清空字符串”是很自然的“关掉跨域”写法，结果反而是全开。配合 `allow_credentials=True` 与 `allow_methods=["*"]`，任何网页都能发起完整的跨域调用（本应用用 Bearer token 而非 cookie，实际风险低一些，但不应依赖这一点）。

**修法（T4）**：空字符串 → `[]`（无跨域来源）或启动报错；只有显式 `*` 才通配；补一条测试与 `.env.example` 注释。

### F7（P3）安全表述说过头

| 位置 | 现文 | 问题 |
|---|---|---|
| `backend/utils/sandbox.py:5-6` | “API keys and provider endpoints never leak into a command” | 只挡继承；文件与 Linux `/proc` 均可读（F2） |
| `README.md:53` | “API Key 不会漏进去” | 同上 |
| `README.md:40` | “停止杀进程树” | 只对仍在运行的调用树成立；shell 先退出的后台进程不受管（F3） |
| `.env.example:57-59` | “本章只支持本机访问” | 是**默认**，不是强制；Docker compose / `.env` 改 host 可越过（F1、F5） |
| `.env.example:63` | “enable on public deployments” | 本章不支持远程访问（G-26）；Host 允许名单也不会因开 auth 而扩展 |
| `docs/architecture.md:566` | “P1-6 仅在公网时开启” | 同上；“公网”不是本章支持的形态 |
| `docs/adr/0009:34` | “密钥不进 bash” | 决策（白名单）不变，措辞应收窄为“白名单只挡继承，不是密钥边界” |

ADR 是决策记录，不建议改写原句；在 T5 里加一条审计注记或让 README 指向本文件即可。`subagent_tool.py:62` 仍写 “code execution, HTTP requests”（这两个工具已删），属于 C7 的文档残影，本审计只记录。

### F8（P3）工作区根之外的可写面

默认 cwd 是 `Settings.artifacts_path`（`bash.py:305`），默认即 `data/artifacts`。本机探针：

```text
pwd                         -> /e/code/demo/langgraph-agent/data/artifacts
../../.env                  -> 可读
../../data                  -> 可读写（trace / checkpoint / snapshots）
../../.git                  -> 可读写
```

README:52 已经说“工作目录只是起点，不是围墙”，所以这不是新缺陷；但审计面（trace）、其他任务的状态（checkpoint）和仓库源码都在可写范围内，**trace 不是防篡改审计日志**。`start.py` 还用 `--reload`，改 `backend/` 源码会在热重载时执行——这仍是同账号权限，不是提权，但说明源码树不是只读的。T5 把这个例子写进 README，避免读者把“工作区根”误当成受限目录。

### F9（P3）bash 发现与 taskkill 的加固点

- `bash_path` 显式指向 `C:\Windows\System32\bash.exe` 时，`_is_system32_stub` 会跳过它并继续回退（`bash.py:152-157`），只在日志里 warning；用户以为自己指定了 WSL，实际跑的是 Git Bash 或直接失败。更清晰的行为是：**显式配置命中 stub 就报错**，不要静默换一个 shell。
- `_kill_tree` 用 `["taskkill", ...]`（`bash.py:190-196`），按父进程 PATH 解析。用 `%SystemRoot%\System32\taskkill.exe` 绝对路径更稳，也避免 PATH 被改写时杀错程序。
- 这两条都不改变默认行为，可并入 T3 的“bash 收尾加固”，或留给 C7。

## 成立的控制（不要改回去）

- **工具面收窄**：计划模式只拿 `explore` 档（`nodes.py:99-101,453-462`）；子代理 `explore` / `execute` 两档都不含 `bash`、MCP、`spawn_subagent`（`subagent.py:40-62`）。`bash` 只在主 agent 的执行模式可达。
- **命令执行形态**：`shell=False` + `[bash, "-c", command]`（`bash.py:312-325`）；`stdin=DEVNULL`；不存在把模型输入拼进宿主 shell 的第二层注入。
- **活树回收**：POSIX `start_new_session` + `killpg`；Windows `taskkill /T`；现有 stop 测试覆盖“后台子 shell 不存活”。
- **超时与输出**：1–600s 钳制；每流 30KB 尾部缓冲，内存不随输出线性增长；`truncated` 如实回给模型。
- **环境继承（仅指继承）**：白名单确实生效（本机 `env` 探针无 `LLM_API_KEY`）；它不挡 `/proc` 与文件读取，见 F2。`PYTHONUNBUFFERED=1` 只影响缓冲。
- **本机默认面**：`host=127.0.0.1`、CORS 自身来源、Host 检查挡 DNS 重绑定、auth 占位符拒绝启动（`main.py:24-30`）。
- **危险规则钩子**：`nodes.py:405-448` 在派发前判定，命中即 `skipped` + `{block, reason}`，不改参数、不重入；默认空表是 D1(b) 的定案，**不是遗漏**（`docs/archive/research-danger-policy-across-agents.md` §6.4）。

## 不修 / 不在本章范围

- **网络出站**：`curl` / `wget` / `/dev/tcp` 均可出网；README:52 已写“网络都可达”。不设 egress 策略，容器是边界。
- **规则表不是沙箱**：正则可被编码/变量绕过；它只是护栏。空表 = 不拦，已定案。
- **输出截断不是脱敏**：命令打印的密钥会进模型上下文与 trace；截断只限长度。
- **资源隔离**：不做 cgroup / Job Object / rlimit（F4 只修文档）。
- **F3 的脱离进程**：本章只修“管道拖尾与线程泄漏”，不承诺按任务追踪所有脱离进程。
- **C2 遗留**：`tool_node` 的 `except Exception` 转换点缺直接测试；非 bash 面，留待后续。
- **C3 遗留非安全项**：`start.py --port` 与 `frontend/vite.config.ts` 硬编码 `localhost:8000` 的代理不一致；属功能问题，归 C7 或单独小票。
- **文档残影**：`subagent_tool.py:62`、`docs/class-diagram.mermaid` 的 `CodeExecTool` / `HttpTool`、`pyproject.toml` 的 `resilience.py` 注释——归 C7。

## 建议票

> 以下是审计当时的建议清单（覆盖 Docker/Linux/分发形态）。2026-10-19 复核后用户决定：**暂不开跟进票**；
> 当前 Windows 自用的实际处置见文末「附：当前适用形态与下一步修复」。T1/T2/T6 在当前形态不触发；
> T3（F3）与 T4（F6）是当前要修的两处代码项。

| 票 | 级别 | 建议标题 | 范围 / 判据 | 标签 |
|---|---|---|---|---|
| **T1** | P1 | `[Bug] Docker 部署默认对所有网卡开放：compose 端口改回环 + 文档写明 Host 检查不是访问控制` | `docker-compose.yml` → `127.0.0.1:8000:8000`；README / architecture 写清 Docker 必须绑回环；可选：入口对“非回环 + auth 关”告警；容器非 root 可另议 | `bug` |
| **T2** | P1 | `[Bug] Linux/Docker 下 bash 可读父进程 environ，环境白名单不是密钥边界` | 拍板：接受并写清（文档）／`prctl(PR_SET_DUMPABLE, 0)` 加固（Linux-only 测试）／换 UID；判据：不再有无条件的“密钥不进 bash”表述，且若选加固，Linux 探针证明 `$PPID/environ` 不可读 | `bug`（设计选择未定，暂不打 `ready-for-agent`） |
| **T3** | P2 | `[Bug] bash：shell 退出后仍等待继承管道（最多 10s），脱离的子进程不被回收` | shell 退出后短 drain + 关管道；补“继承管道下调用有界返回 + pump 线程退出”的回归测试；脱离进程的存活写进文档；顺手：显式 System32 配置报错、taskkill 绝对路径 | `bug` |
| **T4** | P3 | `[Bug] cors_origins 空字符串回退为通配符 *` | 空字符串 → `[]` 或报错；只有显式 `*` 才通配；补测试与 `.env.example` 注释 | `bug` |
| **T5** | P2/P3 | `[Docs] 修正 bash 安全口径：停止只杀活树、本机不等于有认证、无资源上限` | README:40,47-54、`.env.example:57-64`、`architecture.md:566`、ADR 0009 注记；补 `.env` / 家目录 / `data/` 可达、“默认无认证”、无 CPU/内存/磁盘上限；若 T2 选“接受”，把“只挡继承”的措辞一并落这里 | `documentation` |
| **T6**（可选） | P3 | `[Enhancement] 本机随机启动令牌（dsh 式，G-20 后续加固）` | 每进程随机 token → URL → HttpOnly/SameSite cookie；只挡无凭据的本地进程，不替代容器；本章不做 | `enhancement` |

**原建议顺序（适用于仓库随附形态 / 将来分发）**：T1 → T2 → T5 → T3 → T4；T6 等 G-20 的后续时机。
**当前 Windows 自用不适用 T1/T2/T6**；实际顺序见文末附记：先 F3、F6，再随 C4/C7 落文档。

## 验证与证据

- **已运行**：`pytest backend/tests/test_bash_tool.py backend/tests/test_local_only.py -q` → 全绿（含 stop 杀树、超时、输出截断、Windows 发现、Host/CORS 默认）。
- **已运行（本机探针，临时脚本在 `%TEMP%`，未入库）**：F1 的 Host 伪造与 CORS、F2 的继承环境过滤、F3 的脱离进程/耗时/线程、F6 的空 CORS、F8 的路径可达。
- **已运行（Linux）**：在 Docker Desktop 的 `docker-desktop` WSL 发行版（root、Yama=1）里复现 F2：子进程 `unset SECRET` 后仍能读 `/proc/$PPID/environ`；把 `ptrace_scope` 临时改为 0 再跑结果相同，随后恢复为 1。
- **未运行**：完整的 `python:3.11-slim` 容器（本机无 docker CLI / 守护进程；WSL 里只有 `docker-desktop` 发行版）；全量 pytest / ruff / mypy（本审计不改代码，不重复跑门禁）；真实网络下的局域网访问（用 TestClient 的 Host 头模拟）。
- **产物**：本文件（docs-only；`docs/**` 不在 CI `paths` 白名单，除 `docs/architecture.md` 外不触发 CI）。

## 附：当前适用形态与下一步修复（2026-10-19 追加）

> **入库注记（2026-10-20）**：本附记的 §3「后续规划」与 §4「当前流程状态」是审计当时的快照，已被
> [ADR 0010](adr/0010-launch-dir-workspace-and-model-profiles.md) 与 [实施规划](specs/qianfan-daily-form-plan.md) §4 取代：
> F3 → 切片 S1（含 F7 / F8 / F4 文档项），F6 与 Docker 删除 → C4；本文随 D0 文档 PR 入库，#148 待 S1 合并后关闭。正文不改。

### 1. 适用形态与重分级

**当前实际使用（2026-10-19 拍板）**：

- **Windows 10/11 + Git Bash**；`uv tool install -e .` 自用；终端敲 `qianfan` 启动；以后可选 exe，不在当前计划。
- **不用 Docker、不远程访问、不对外分发、单用户**。

**结论**：C3 的票面验收**通过**；安全审计按当前形态是 **“有条件通过”**——F3、F6 要修，F7/F8/F4 要写清，F1/F2/F5 不触发。

按这个形态重排：

| 发现 | 仓库随附形态 | 当前 Windows 自用 | 下一步 |
|---|---|---|---|
| F1 Docker 暴露 | P1 | **N/A**（不用 compose） | 删 `docker-compose.yml` 或标“不支持”；不必改代码 |
| F2 Linux `/proc` 密钥 | P1 | **N/A**（MSYS `/proc` 不提供 environ） | 若改用 WSL/Docker 则重新生效，按 T2 处理 |
| F3 脱离进程/管道拖尾 | P2 | **P2，现在修** | 短 drain + 关管道；补测试；文档写清限制 |
| F4 无 CPU/内存/磁盘上限 | P2 | P2，文档 | README 写清；不做 cgroup/Job Object |
| F5 默认无认证 | P2 | **N/A**（单用户本机） | README 一句；共享机器/分发时再评估 |
| F6 空 CORS → `*` | P3 | P3，现在修 | 空串 → `[]` 或报错；补测试 |
| F7 安全表述过头 | P3 | P3，文档 | C4 启动段 + C7 收尾 |
| F8 工作区外可写 | P3 | P3，文档 | README 写清；默认工作区仍是仓库 `data/artifacts` |
| F9 System32/taskkill | P3 | 可选 | 并入 F3 或 C7 |

### 2. 下一步修复（按顺序）

**A. 现在（不阻塞 C4）**

1. **F3（`backend/core/tools/bash.py`）**：shell 退出后给 pump 一个很短的 drain 窗口（建议总时长 ≤1s），随后关闭 `stdout`/`stderr` 让读线程退出；不要串行等两个 5s。测试补：
   - 后台子进程继承管道时，工具调用有界返回；
   - 返回后 pump 线程退出（线程数回到基线）；
   - “脱离父子关系的后台进程不被停止/超时回收”写进文档，**不要**把存活写成“已清理”。
2. **F6（`backend/config.py`）**：`cors_origins=""` → `[]`（无跨域来源）或启动报错；只有显式 `*` 才通配；补测试与 `.env.example` 注释。
3. **可选 F9**：显式 `bash_path` 指向 System32 stub 时报错；Windows `taskkill` 用 `%SystemRoot%\System32\taskkill.exe` 绝对路径。

**B. 随 C4 落（不是安全修复，不要塞进同一个 diff）**

- `pyproject.toml` 包信息 + `[project.scripts] qianfan`；`qianfan` 起 uvicorn（127.0.0.1）、托管 `frontend/dist`、开浏览器；
- 第一件事做 `uv tool install -e .` 的依赖组合实验（`mcp 1.29` → `uvicorn>=0.31.1` vs 仓库 `<0.31`；`--with-requirements`/`--overrides`/`--excludes` 哪种可行）；
- 可选加 `--workspace <目录>`（当前默认工作区是仓库 `data/artifacts`）；
- README 启动段写清：需要 Git Bash、先 `npm ci && npm run build` 一次、`qianfan` 用法。

**C. 随 C7 落**

- F7/F8/F4 文档：README:40,47-54、`.env.example:57-64`、`architecture.md:566`、ADR 0009 注记；补 `.env` / 家目录 / `data/` 可达、“停止只杀活树”、无 CPU/内存/磁盘上限；
- `start.py --port` 与 `frontend/vite.config.ts` 的 8000 代理不一致；
- `subagent_tool.py:62` 的过期描述、`docs/class-diagram.mermaid` 与 `architecture.md:152` 的 `CodeExecTool`/`HttpTool` 残影；
- `tool_node` 的 `except Exception` 直接测试（现有 `_FailTool` 是返回失败，不是抛异常）；
- `pyproject.toml` 的 `resilience.py` 注释；
- Docker 文案/产物：不用 Docker 就删 `docker-compose.yml` 或标“不支持”，README:19 与 CI docker-build 同步决定；
- `docs/archive/OVERVIEW.md`、`docs/incremental-*`、`docs/agent-*`、`docs/capability-registry-rescope.md` §0.5 的残影按 C7 处理；`NOTES.md` 由用户决定。

**D. 不做 / 触发条件**

- **F1 / F2 / F5 / T6**：不上 Docker/Linux、不共享机器、不对外分发就不做；
- **触发条件**：一旦用 Docker/Linux、共享机器或给别人用，按原 T1/T2/T6 与 T5 的剩余部分处理；公开分发另起 ADR；
- **exe**：只在“需要无 uv/Python 的机器”时做。要先搬数据目录、打包 `frontend/dist`、验证 PyInstaller + LangGraph/MCP/sqlite；Git Bash 仍是前提；不要混进 C4；
- **资源限制 / cgroup / Job Object**：本章不做，只写文档。

### 3. 后续规划（切片）

| 阶段 | 内容 | 验收 |
|---|---|---|
| 现在 | C4 `qianfan`（自用） | 干净环境 `uv tool install -e .`；`qianfan` 起 127.0.0.1、托管 `dist`、开浏览器；无 ImportError |
| C4 后 | C5 工作区文件；C6 计划文件；C7 收尾 | 按各自票面；上面 C 项并入 C7 清单或单独小修 |
| 可选 | exe / dsh 式令牌 / Linux/Docker 加固 | 各自触发条件；不阻塞本章 |
| 触发 | 对外分发 | 新 ADR + D1–D4（路径、打包、安全、发布）；本章不做 |

### 4. 当前流程状态

- `docs/security-audit-2026-10-19-bash.md` 本地未跟踪、未推送；#148 仍开着（已 assign）；按用户决定暂不开跟进票，T1–T6 只留档。
- 本地分支还留着 `chore/156`、`chore/157`、`feat/158`、`docs/minimal-agent-chapter-adr-0009`、`prototype/frontend-redesign`；远端同名也在，合并后可删。
- 未跟踪项 `.agents/handoffs/`、`.agents/retros/2026-10-17-langgraph-agent-s5-review.md`、`S4-test-deletions.md`、`prototype/` 按交接不要动。
- Docker 相关（`docker-compose.yml`、`backend/Dockerfile`、README:19、CI docker-build）仍在；不用 Docker 时按 C7 决定删或标“不支持”。
- 本机 `.env` 已改为 `host=127.0.0.1`，Key 只走环境变量；不提交 `.env` / `data/` / `frontend/dist`。

### 5. 后续修复的验证口径

- **F3**：`pytest backend/tests/test_bash_tool.py -q` + 新增“继承管道有界返回 / pump 线程退出”测试；改动后跑全量 pytest + ruff + mypy。
- **F6**：`pytest backend/tests/test_local_only.py -q` + 新增空串测试；改动后跑全量门禁。
- **C4**：`uv build` / 干净环境 `uv tool install -e .` / `qianfan` / `mock` / `curl /health` 与 `/`；确认 `frontend/dist` 可被托管、无 Node 运行时依赖。
- **C7**：全量门禁 + #162 的 5 个真模型网页场景；文档残影按 C7 清单逐条判读。
- 报告时区分 **通过 / 跳过 / 未运行 / 产物验收**；不写现役测试计数。

## 参考

- [Docker 端口发布](https://docs.docker.com/reference/cli/docker/container/run/)：`-p 8000:8000` 绑所有接口，`-p 127.0.0.1:8000:8000` 只绑回环。
- [proc_pid_environ(5)](https://man7.org/linux/man-pages/man5/proc_pid_environ.5.html)：`/proc/<pid>/environ` 受 ptrace 读检查约束。
- [dsh connection README](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/client/connection/README.md)：每进程随机 launch token、URL 交换 cookie、Host/Origin 检查、不支持 `--host 0.0.0.0`。
- 本仓决策：`docs/adr/0009-minimal-agent-chapter.md`（决策 4/5、三笔账）、`docs/capability-registry-rescope.md` G-20 / G-26、`docs/archive/research-danger-policy-across-agents.md` §6.4。
