#!/usr/bin/env node
/**
 * #91 确认面收尾的防回归断言：AC1（关框 / 切任务判据）+ AC2（五档终态文案表完整、
 * resolved 的终态并回 step 记录）。
 *
 * 为什么不用测试框架：本仓前端无 vitest（引入新依赖另案）。这里复用 `check-sse` 的
 * 路子——一个 plain Node 脚本，直接跑 `confirmSlot.ts` / `confirmOutcome.ts` 的纯函数与
 * `types` 的文案表（纯类型 + 一个 const，只 `import type` 的部分被 Node 类型擦除吃下），把
 * 票面点名的用例钉成机械断言：
 *   AC1「A 任务开框 → 切到 B → 收到 A 的 human_confirm_resolved」不留僵尸框、不误收别的框；
 *   AC2 后端五档 CONFIRM_* 每档都有中文落点（StepDetail 新消费者 + TraceTab 共用这一张），
 *       且 resolved 带的终态能并回 step 记录（`aborted` 不发 tool_result，只能靠这条）。
 *
 * 覆盖范围的实话：判据与变换的权威实现在那两个纯函数模块里，store 与这里共用同一份，
 * 所以不存在「测试一套、线上另一套」；但 **store 调用点这条接线不在下列断言的覆盖范围内**
 * ——`taskStore.ts` 顶层 `create()` 带 zustand/React，plain-Node 跑不了。删掉 store 里的调用点，
 * 这里照样全绿（评审侧做过这个变异）。接线那一环靠人工核，别把这份清单当成端到端保证。
 *
 * 跑：`npm run check:confirm`（CI frontend job 与 check:sse 同处；Node ≥ 22.6）。
 */

import assert from "node:assert/strict";
import process from "node:process";

const { shouldCloseConfirmOnResolved, confirmAfterSwitch } = await import(
  "../src/store/confirmSlot.ts"
);
const { withConfirmOutcome } = await import("../src/store/confirmOutcome.ts");
const { CONFIRM_OUTCOME_LABEL } = await import("../src/types/index.ts");

let n = 0;
const ok = (name, fn) => {
  n += 1;
  try {
    fn();
    console.log(`  ✓ ${name}`);
  } catch (err) {
    console.log(`  ✗ ${name}\n      ${err.message}`);
    process.exitCode = 1;
  }
};

console.log("#91 AC1 确认框关框 / 切任务判据");

// —— shouldCloseConfirmOnResolved：三条同时成立才关 ——
ok("同任务 + 同 tool_call → 关框", () => {
  const confirm = { open: true, task_id: "A", tool_call_id: "tc1" };
  assert.equal(shouldCloseConfirmOnResolved(confirm, "A", "tc1"), true);
});
ok("同任务 + 别的 tool_call → 不关（不能收掉别的调用的框）", () => {
  const confirm = { open: true, task_id: "A", tool_call_id: "tc1" };
  assert.equal(shouldCloseConfirmOnResolved(confirm, "A", "tc2"), false);
});
ok("跨任务：A 的框收到记在 B 名下的 resolved → 不关（#91 修的就是这条）", () => {
  const confirm = { open: true, task_id: "A", tool_call_id: "tc1" };
  assert.equal(shouldCloseConfirmOnResolved(confirm, "B", "tc1"), false);
});
ok("框本就没开 → 不关（幂等）", () => {
  assert.equal(shouldCloseConfirmOnResolved({ open: false }, "A", "tc1"), false);
});

// —— confirmAfterSwitch：切走时清掉属于别的任务的框 ——
ok("A 的开着框 → 切到 B：清框", () => {
  assert.deepEqual(confirmAfterSwitch({ open: true, task_id: "A", tool_call_id: "tc1" }, "B"), {
    open: false,
  });
});
ok("A 的开着框 → 又选中 A：保持（刷新回同一任务不该误清）", () => {
  const confirm = { open: true, task_id: "A", tool_call_id: "tc1" };
  assert.equal(confirmAfterSwitch(confirm, "A"), confirm);
});
ok("没开框 → 切任务：原样返回（不无谓造新对象）", () => {
  const confirm = { open: false };
  assert.equal(confirmAfterSwitch(confirm, "B"), confirm);
});

// —— 票面点名的端到端序列：开框 A → 切到 B → 收到 A 的 resolved ——
ok("票面用例：开框 A → 切到 B → 收到 A 的 resolved，视图无残留框", () => {
  // 前半：A 的框在切走那一步就被清掉（僵尸框的来源就在这里）。
  let confirm = { open: true, task_id: "A", tool_call_id: "tc1" }; // A 的框开着
  confirm = confirmAfterSwitch(confirm, "B"); // 用户切到任务 B
  assert.equal(confirm.open, false, "切到 B 时 A 的框应被清掉");
  // 后半：B 视图下收到 A 的 resolved（ev.data 无 task_id，故归属落到 currentTaskId = "B"）。
  // 「误收」只在 B 自己开着一格时才可能发生，所以这条要造那一格来验——此时 task_id 两边都是
  // B、比的是 tool_call_id；task_id 那条比对单独由上面的「跨任务」断言盯住，两把锁各有钉子。
  const bBox = { open: true, task_id: "B", tool_call_id: "tc2" };
  assert.equal(
    shouldCloseConfirmOnResolved(bBox, "B", "tc1"),
    false,
    "A 的 resolved 不该收掉 B 自己开着的框"
  );
  assert.equal(
    shouldCloseConfirmOnResolved(confirm, "B", "tc1"),
    false,
    "已无开着的框，不该再动确认态"
  );
});

// —— AC2 实时面：resolved 的终态并回 step 记录 ——
// aborted 档到不了 tool 节点、不发 tool_result（后端 nodes.py 自述），所以 step 记录上的
// 终态只能由这条事件回填。不回填的话实时视图停在「待确认」，要等重新选中任务读快照。
console.log("\n#91 AC2 实时面：human_confirm_resolved 回填 step 记录");
const pendingGate = () => [
  {
    index: 1,
    thought: "",
    status: "running",
    tool_calls: [
      {
        id: "tc1",
        tool_name: "code_exec",
        input: {},
        output: null,
        status: "pending",
        need_confirm: true,
        confirmed: false,
        confirm_outcome: "",
      },
    ],
  },
];
ok("aborted 回填到匹配的那条调用，need_confirm 不被抹掉", () => {
  const tc = withConfirmOutcome(pendingGate(), "tc1", "aborted")[0].tool_calls[0];
  assert.equal(tc.confirm_outcome, "aborted");
  assert.equal(tc.need_confirm, true, "回填只写终态，不该顺手改掉「进过闸门」");
});
ok("不改原对象（store 靠引用变化重渲染；就地改会让 React 看不见这一档）", () => {
  const steps = pendingGate();
  const next = withConfirmOutcome(steps, "tc1", "timed_out");
  assert.equal(steps[0].tool_calls[0].confirm_outcome, "", "原记录保持回填前那份");
  assert.notEqual(next, steps);
  assert.notEqual(next[0].tool_calls[0], steps[0].tool_calls[0]);
});
ok("一条都不匹配（切到 B 后收到 A 的 resolved）→ 原数组引用，别的任务不被写脏", () => {
  const steps = pendingGate();
  assert.equal(withConfirmOutcome(steps, "tc-belongs-to-A", "approved"), steps);
});

// —— AC2：五档终态文案表完整（StepDetail 新消费者读的就是这张） ——
// CONFIRM_OUTCOME_LABEL 刻意是 Record<string, string>（TraceTab 要对认不出的档原样回显，
// 故没有编译期穷尽性）。下面这份 FIVE 是手抄的第二份成员表：它只防「某一档的文案被删或
// 被清空」，后端真加第六档、前端忘了配文案，这里不会红——要机械盯住上游那份常量，得等
// #104 的发布点 AST 枚举。
console.log("\n#91 AC2 五档终态文案表完整");
const FIVE = ["approved", "denied", "timed_out", "aborted", "auto_approved"];
ok("后端五档 CONFIRM_* 每档都有非空中文文案", () => {
  for (const k of FIVE) {
    assert.ok(CONFIRM_OUTCOME_LABEL[k]?.trim(), `${k} 缺文案`);
  }
});
ok('"" 不是某一档：它是「没进过闸门」，由组件渲染成「待确认」，不该进终态表', () => {
  assert.ok(!("" in CONFIRM_OUTCOME_LABEL), "空串不该出现在终态文案表里");
});

if (process.exitCode) {
  console.error(`\n确认面判据断言失败（#91 AC1/AC2）：${n} 条里有不通过的，见上。`);
  process.exit(1);
}
console.log(`确认面判据 ${n} 条全部通过（#91 AC1/AC2）。`);
