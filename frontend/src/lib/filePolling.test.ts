import { describe, expect, it } from "vitest";
import { createFilePollLoop, type PollTimers } from "./filePolling";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function manualTimers() {
  let next = 1;
  const pending = new Map<number, () => void>();
  const timers: PollTimers = {
    setTimeout: (fn) => {
      const handle = next++;
      pending.set(handle, fn);
      return handle;
    },
    clearTimeout: (handle) => {
      pending.delete(handle);
    },
  };
  return {
    timers,
    pendingCount: () => pending.size,
    fire() {
      const entry = pending.entries().next().value;
      if (!entry) throw new Error("no timer scheduled");
      pending.delete(entry[0]);
      entry[1]();
    },
  };
}

/** Let promise continuations run (the loop uses no real timers here). */
const settle = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

describe("createFilePollLoop", () => {
  it("慢响应不再按固定间隔叠加：下一次只在请求结束后安排", async () => {
    const timers = manualTimers();
    const loads: ReturnType<typeof deferred<number>>[] = [];
    const applied: number[] = [];
    const loop = createFilePollLoop({
      intervalMs: 2000,
      run: () => {
        const pending = deferred<number>();
        loads.push(pending);
        return pending.promise;
      },
      shouldContinue: () => true,
      apply: (value) => applied.push(value),
      onError: () => undefined,
      timers: timers.timers,
    });

    loop.start();
    expect(loads).toHaveLength(1);
    expect(timers.pendingCount()).toBe(0);

    // Time passing while the request is in flight must not start another one:
    // there is no interval timer to fire at all.
    await settle();
    expect(loads).toHaveLength(1);

    loads[0].resolve(1);
    await settle();
    expect(applied).toEqual([1]);
    expect(timers.pendingCount()).toBe(1);

    timers.fire();
    expect(loads).toHaveLength(2);
    expect(timers.pendingCount()).toBe(0);

    loads[1].resolve(2);
    await settle();
    expect(applied).toEqual([1, 2]);
  });

  it("终态 / 隐藏后请求结束也不再排下一次", async () => {
    const timers = manualTimers();
    const loads: ReturnType<typeof deferred<number>>[] = [];
    let continuePolling = true;
    const loop = createFilePollLoop({
      intervalMs: 2000,
      run: () => {
        const pending = deferred<number>();
        loads.push(pending);
        return pending.promise;
      },
      shouldContinue: () => continuePolling,
      apply: () => undefined,
      onError: () => undefined,
      timers: timers.timers,
    });

    loop.start();
    continuePolling = false;
    loads[0].resolve(1);
    await settle();

    expect(timers.pendingCount()).toBe(0);
  });

  it("在途请求被 stop 后结果不落地，也不再排下一次", async () => {
    const timers = manualTimers();
    const loads: ReturnType<typeof deferred<number>>[] = [];
    const applied: number[] = [];
    const loop = createFilePollLoop({
      intervalMs: 2000,
      run: () => {
        const pending = deferred<number>();
        loads.push(pending);
        return pending.promise;
      },
      shouldContinue: () => true,
      apply: (value) => applied.push(value),
      onError: () => undefined,
      timers: timers.timers,
    });

    loop.start();
    loop.stop();
    loads[0].resolve(1);
    await settle();

    expect(applied).toEqual([]);
    expect(timers.pendingCount()).toBe(0);
  });

  it("stop 后在途请求被取消（拒绝）也不报错", async () => {
    const timers = manualTimers();
    const loads: ReturnType<typeof deferred<number>>[] = [];
    const errors: unknown[] = [];
    const loop = createFilePollLoop({
      intervalMs: 2000,
      run: () => {
        const pending = deferred<number>();
        loads.push(pending);
        return pending.promise;
      },
      shouldContinue: () => true,
      apply: () => undefined,
      onError: (error) => errors.push(error),
      timers: timers.timers,
    });

    loop.start();
    loop.stop();
    loads[0].reject(new Error("aborted"));
    await settle();

    expect(errors).toEqual([]);
    expect(timers.pendingCount()).toBe(0);
  });

  it("stop 清掉已排定的下一次请求", async () => {    const timers = manualTimers();
    const loads: ReturnType<typeof deferred<number>>[] = [];
    const loop = createFilePollLoop({
      intervalMs: 2000,
      run: () => {
        const pending = deferred<number>();
        loads.push(pending);
        return pending.promise;
      },
      shouldContinue: () => true,
      apply: () => undefined,
      onError: () => undefined,
      timers: timers.timers,
    });

    loop.start();
    loads[0].resolve(1);
    await settle();
    expect(timers.pendingCount()).toBe(1);

    loop.stop();
    expect(timers.pendingCount()).toBe(0);
    expect(loads).toHaveLength(1);
  });

  it("失败也算一次请求结束：报错并安排下一次尝试", async () => {
    const timers = manualTimers();
    const errors: unknown[] = [];
    let calls = 0;
    const loop = createFilePollLoop({
      intervalMs: 2000,
      run: () => {
        calls += 1;
        return Promise.reject(new Error(`boom ${calls}`));
      },
      shouldContinue: () => true,
      apply: () => undefined,
      onError: (error) => errors.push(error),
      timers: timers.timers,
    });

    loop.start();
    await settle();
    expect(errors).toHaveLength(1);
    expect(timers.pendingCount()).toBe(1);

    timers.fire();
    await settle();
    expect(calls).toBe(2);
    expect(errors).toHaveLength(2);
  });

  it("重启后旧循环的在途响应被代次挡掉，新循环独占结果", async () => {
    const timers = manualTimers();
    const loads: ReturnType<typeof deferred<number>>[] = [];
    const applied: number[] = [];
    const loop = createFilePollLoop({
      intervalMs: 2000,
      run: () => {
        const pending = deferred<number>();
        loads.push(pending);
        return pending.promise;
      },
      shouldContinue: () => true,
      apply: (value) => applied.push(value),
      onError: () => undefined,
      timers: timers.timers,
    });

    loop.start();
    loads[0].resolve(1);
    await settle();
    expect(applied).toEqual([1]);

    // Simulate a retry / task switch: the first request is still in flight
    // when the same loop is restarted (stop + start).
    timers.fire();
    expect(loads).toHaveLength(2);
    loop.stop();
    loop.start();
    expect(loads).toHaveLength(3);

    loads[1].resolve(2);
    await settle();
    expect(applied).toEqual([1]);

    loads[2].resolve(3);
    await settle();
    expect(applied).toEqual([1, 3]);
  });
});
