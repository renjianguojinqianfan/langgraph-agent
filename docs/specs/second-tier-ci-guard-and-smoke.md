# 第二梯队优化：CI 守卫 + 无 Key 冒烟检查

## Summary

两项改动，约 1 小时：
- **A. CI 受保护文件守卫**：把 AGENTS.md 的"熔断层零改动"从文档约束升级为 CI 机械化闸门（`[OVERRIDE]` 逃生舱）。
- **B. live_e2e.py `--check` 离线冒烟**：不调用真实 LLM、不依赖 secret，验证脚本依赖链可加载、配置可解析、内核可装配；接入 CI 全量运行。

## A. 受保护文件守卫（.github/workflows/ci.yml）

新增独立 job（与 backend-test 并行，无相互依赖，最小侵入）：

```yaml
  guard-protected-files:
    name: Protected files guard (resilience/registry)
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0        # 保证 PR base sha 可 diff
      - name: Detect changes to protected files
        env:
          PR_TITLE: ${{ github.event.pull_request.title }}
        run: |
          set -e
          protected="backend/core/tools/resilience.py backend/core/tools/registry.py"
          if [ "${{ github.event_name }}" = "pull_request" ]; then
            base="${{ github.event.pull_request.base.sha }}"
            changed=$(git diff --name-only "$base"...HEAD -- $protected)
          elif [ -n "${{ github.event.before }}" ] && git cat-file -e "${{ github.event.before }}" 2>/dev/null; then
            changed=$(git diff --name-only "${{ github.event.before }}" HEAD -- $protected)
          else
            changed=""   # workflow_dispatch / 首次 push：无可比基线，放行
          fi
          if [ -z "$changed" ]; then
            echo "No protected-file changes."; exit 0
          fi
          echo "Changed protected files:"; echo "$changed"
          title="${PR_TITLE:-$(git log -1 --pretty=%s)}"
          case "$title" in *"[OVERRIDE]"*)
            echo "::warning::Protected files changed with [OVERRIDE] declared — allowing."; exit 0 ;;
          esac
          echo "::error::受保护文件被修改（AGENTS.md：熔断层零改动）。PR/commit 标题需包含 [OVERRIDE] 才能通过。"
          exit 1
```

设计要点：
- **守卫对象**：`backend/core/tools/resilience.py` + `backend/core/tools/registry.py`（AGENTS.md"熔断层零改动"对象；nodes.py 的 `_needs_confirm` 无法按文件守卫，保持文档约束不变）。
- **事件覆盖**：PR（对比 base.sha）、push（对比 `github.event.before`）、workflow_dispatch/首次 push（无基线，放行）。
- **逃生舱**：PR 标题或 push 的 commit message 含 `[OVERRIDE]` 时警告放行——显式声明覆盖意图。
- 建议在 repo Settings 将该 job 标记为 required check（口述，不在代码内）。

## B. live_e2e.py `--check` 模式（scripts/live_e2e.py）

1. **argparse 接入**：`main()` 前解析 `--check` 标志。
2. **新增 `run_check(settings)`**（离线、零网络、零 Key）：
   - 强制 `os.environ["USE_MOCK_LLM"] = "true"`（覆盖式，非 setdefault，防止本地 .env 污染）；
   - **路径全量重定向**：仅覆盖 `DATA_DIR` 不够——`artifacts_dir`/`trace_dir`/`git_repo_dir`/`kb_dir` 是独立字段，相对路径各自解析到 `PROJECT_ROOT`，不跟随 `data_dir`；而 `TaskManager` 构造会经 `Persistence` mkdir `artifacts_path`、经 `TraceRecorder` 指向 `data/traces`、经 Git 工具 mkdir `data/repos`。因此将 `DATA_DIR`、`ARTIFACTS_DIR`、`TRACE_DIR`、`GIT_REPO_DIR`、`KB_DIR` 一并指向同一个 `tempfile.mkdtemp()`，避免污染 `data/`；
   - **`@lru_cache` 处理**：`get_settings()` 带 `lru_cache`，环境变量必须在首次调用前写入，并在写入后调用一次 `get_settings.cache_clear()` 再取配置，防止缓存导致覆盖静默失效（另注意模块 import 时的 `os.environ.setdefault("USE_MOCK_LLM", "false")`，覆盖式赋值可压过它）；
   - 检查链：`get_settings()` 解析成功 → `build_tools(settings)` 返回非空工具集 → `create_llm_client(settings)`（mock 路径）可用 → `TaskManager` 构造 + `shutdown()` 生命周期完整；
   - 逐项打印 `[PASS]/[FAIL]`，返回 0/1。
3. **CI 接入**：在 `backend-test` job 测试步骤后追加：

```yaml
      - name: Offline smoke of live_e2e wiring (--check)
        run: python scripts/live_e2e.py --check
```

4. **文档同步**：更新 live_e2e.py 模块 docstring（新增 `--check` 用法）；AGENTS.md「常用命令」追加一行 `--check` 命令示例。

## Test Plan

| 验证项 | 命令 | 预期 |
|---|---|---|
| 全量回归 | `.\.venv311\Scripts\python.exe -m pytest backend/tests/ -q` | 351 passed |
| --check 本地运行 | `.\.venv311\Scripts\python.exe scripts\live_e2e.py --check` | 全 PASS，exit 0，无网络调用，且运行后 `data/` 无新增文件（验证路径重定向生效） |
| CI 语法 | `.venv311` pyyaml `yaml.safe_load(ci.yml)` | 解析通过，job 数 4→5 |
| 守卫逻辑（本地模拟） | PowerShell 里用 `git diff --name-only` 对最近一次提交模拟检测 | 逻辑可读、分支覆盖三种事件 |

## Assumptions

- 不修改 `resilience.py`/`registry.py` 本身，不触碰 conftest 隔离与 `_needs_confirm` 逻辑（硬规则）。
- 不新增第三方依赖；`--check` 复用现有 requirements。
- 守卫 job 不改变现有 job 依赖关系（docker-build 仍只 needs backend-test）；是否设为 required check 由你在 GitHub Settings 决定。