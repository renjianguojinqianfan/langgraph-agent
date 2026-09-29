#!/usr/bin/env node
/**
 * #92 防回归断言：SSE 事件词汇表四处必须逐条相等。
 *
 * 为什么要有这个脚本：EventSource 只把带 `event:` 字段的消息派发给 `addEventListener`
 * 注册过的类型，没注册的类型浏览器直接丢。所以注册表漂了不报错、不告警，只是时间线
 * 静默少一类事件（`verification` / `task_resumed` / `task_rollback` / `tool_result_evicted`
 * 就这么漂了整整数周）。此前唯一的对照手段是 docs/architecture.md §3.4 旁边那句
 * 「未知类型自然忽略」，靠人自觉，于是没人对照。这里把「对照」变成一条机械断言。
 *
 * 权威源：`docs/architecture.md` §3.4 的事件表（以表为准、不写计数）。
 *
 * 断言（任一不成立 exit 1）：
 *   1. 后端 SSE 发布点集合 == §3.4 表行集合
 *   2. 后端 SSE 发布点集合 == `frontend/src/hooks/useSSE.ts` 的 `EVENT_TYPES`
 *      —— 少了 = 前端静默收不到（#92 的 bug）；多了 = 零发布点的死订阅，
 *         会误导下一个读代码的人以为那条事件流还在（退役的 subtask_* 就是这样）。
 *   3. `frontend/src/types/index.ts` 的 `SSEventType` 联合 == `EVENT_TYPES` ∪ trace 文件独有类型
 *      —— `trace_end` 由 `TraceRecorder.close()` 直写 JSONL、不经 EventBus，所以它进联合
 *         （trace 回放面要认它）、不进订阅表（SSE 流上永远不会出现它）。
 *
 * 发布点怎么认：grep 三种现役写法的字符串字面量——
 *   `self._publish("x", …)` / `….publish(task_id, "x", …)` / `sse_format({"type": "x", …})`
 * 类型参数是变量名的调用认不出来；那种事件必须同时补进 §3.4 表，否则断言 1 与 2 会因
 * 两边不一致而变红（不会静默放过一个新类型进不了前端）。
 * `backend/tests/` 不参与统计：#56 退役的三个类型只在测试里被断言「不许出现」，
 * 只有非测试码真发的才算发布点。
 *
 * 跑：`npm run check:sse`（CI frontend job 同一条命令，cwd 无关——路径按本文件位置解析）。
 */

import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const read = (rel) => readFileSync(path.join(ROOT, rel), "utf8");
/** 去掉行注释：注释里出现过 `"…"` 会让字面量解析多抓一条。 */
const stripLineComments = (src) => src.replace(/\/\/[^\n]*/g, "");

/** 后端非测试码的 .py 文件（跳过 tests/ 与 __pycache__）。 */
function* backendPyFiles(dir) {
  for (const entry of readdirSync(path.join(ROOT, dir), { withFileTypes: true })) {
    const rel = `${dir}/${entry.name}`;
    if (entry.isDirectory()) {
      if (entry.name === "tests" || entry.name === "__pycache__") continue;
      yield* backendPyFiles(rel);
    } else if (entry.name.endsWith(".py")) {
      yield rel;
    }
  }
}

/** 三种现役发布写法（见文件头「发布点怎么认」）。 */
const PUBLISH_PATTERNS = [
  /\b_publish\(\s*"([a-z_]+)"/g,
  /\bpublish\(\s*[^)]*?"([a-z_]+)"/g,
  /\bsse_format\(\s*\{\s*"type":\s*"([a-z_]+)"/g,
];

/** type -> 发布点 `file:line` 列表（打印取证用，顺带让「谁在发这条」一眼可见）。 */
function backendSsePublishPoints() {
  const found = new Map();
  for (const rel of backendPyFiles("backend")) {
    const src = read(rel);
    for (const re of PUBLISH_PATTERNS) {
      for (const m of src.matchAll(re)) {
        const line = src.slice(0, m.index).split("\n").length;
        const at = `${rel}:${line}`;
        const list = found.get(m[1]) ?? [];
        if (!list.includes(at)) list.push(at);
        found.set(m[1], list);
      }
    }
  }
  return found;
}

/** 只写进 trace JSONL、从不进 EventBus 的类型（联合里有、订阅表里没有的那一类）。 */
function traceFileOnlyTypes() {
  const src = read("backend/services/trace.py");
  return [...src.matchAll(/\{\s*"type":\s*"([a-z_]+)"/g)].map((m) => m[1]);
}

/** §3.4 表第一列的 event 类型（表区间：`### 3.4` 到下一个 `## 4.`）。 */
function docTableTypes() {
  const src = read("docs/architecture.md");
  const start = src.indexOf("### 3.4");
  const end = src.indexOf("\n## 4.", start);
  if (start < 0 || end < 0) fail("docs/architecture.md 里找不到 §3.4 的事件表区间");
  const section = src.slice(start, end);
  return [...section.matchAll(/^\| `([a-z_]+)` \|/gm)].map((m) => m[1]);
}

/** useSSE.ts 的 EVENT_TYPES 数组成员。 */
function subscribedTypes() {
  const src = stripLineComments(read("frontend/src/hooks/useSSE.ts"));
  const block = /const EVENT_TYPES: SSEventType\[\] = \[([\s\S]*?)\];/.exec(src);
  if (!block) fail("frontend/src/hooks/useSSE.ts 里找不到 EVENT_TYPES 数组");
  return [...block[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
}

/** types/index.ts 的 SSEventType 联合成员。 */
function unionTypes() {
  const src = stripLineComments(read("frontend/src/types/index.ts"));
  const block = /export type SSEventType =([\s\S]*?);/.exec(src);
  if (!block) fail("frontend/src/types/index.ts 里找不到 SSEventType 联合");
  return [...block[1].matchAll(/\|\s*"([a-z_]+)"/g)].map((m) => m[1]);
}

const onlyIn = (a, b) => [...a].filter((x) => !b.has(x)).sort();

function fail(message) {
  console.error(`\nFATAL: ${message}`);
  process.exit(1);
}

function diff(name, left, right, leftLabel, rightLabel, hint) {
  const missing = onlyIn(right, left); // right 有 left 没有
  const extra = onlyIn(left, right);
  if (missing.length === 0 && extra.length === 0) {
    console.log(`✓ ${name}：${left.size} 条逐条相同`);
    return true;
  }
  console.log(`✗ ${name}`);
  for (const t of missing) console.log(`    只在${rightLabel}，${leftLabel}没有：${t}`);
  for (const t of extra) console.log(`    只在${leftLabel}，${rightLabel}没有：${t}`);
  if (hint) console.log(`    → ${hint}`);
  return false;
}

// ── 采集 ────────────────────────────────────────────────────────────────────
const publish = backendSsePublishPoints();
const backend = new Set(publish.keys());
const doc = new Set(docTableTypes());
const listen = new Set(subscribedTypes());
const union = new Set(unionTypes());
const traceOnly = new Set(traceFileOnlyTypes());

console.log(`后端 SSE 发布点 ${backend.size} 条 / §3.4 表 ${doc.size} 条 / ` +
  `EVENT_TYPES ${listen.size} 条 / SSEventType 联合 ${union.size} 条 / ` +
  `trace 文件独有 ${traceOnly.size} 条`);

// 取证表：后端在发的每一条，前端两处是否都认。注册了才会派发给 handler，
// handler 直通 store.applyEvent（不按类型过滤）进 state.events 时间线。
console.log("\n后端在发 → 前端是否收得到");
for (const t of [...backend].sort()) {
  const where = publish.get(t)[0];
  const ok = listen.has(t) && union.has(t);
  console.log(`  ${ok ? "✓" : "✗"} ${t.padEnd(24)} ${where}${ok ? "" : "（未注册：浏览器直接丢）"}`);
}
for (const t of traceOnly) {
  console.log(`  · ${t.padEnd(24)} 只写 trace JSONL，不经 SSE：进联合、不进订阅表`);
}

// ── 断言 ────────────────────────────────────────────────────────────────────
console.log("");
const checks = [
  diff("断言 1 后端发布点 vs §3.4 表", backend, doc, "后端代码", "§3.4 表",
    "后端发了表里没有的 = 补表；表里列了后端不发的 = 删表行（表是权威，两边都得动）。"),
  diff("断言 2 后端发布点 vs EVENT_TYPES", backend, listen, "后端代码", "EVENT_TYPES",
    "EVENT_TYPES 缺 = 该事件前端静默收不到；多 = 零发布点的死订阅。"),
  diff("断言 3 EVENT_TYPES ∪ trace 独有 vs SSEventType 联合",
    new Set([...listen, ...traceOnly]), union, "EVENT_TYPES ∪ trace 独有", "SSEventType 联合",
    "两处前端注册表必须同一份词汇，否则订阅面与类型面各漂各的。"),
];

if (checks.some((ok) => !ok)) {
  console.error("\nSSE 事件词汇表漂移（#92）：以上差异需逐条消除后重跑 npm run check:sse。");
  process.exit(1);
}
console.log("SSE 事件词汇表四处对齐。");
