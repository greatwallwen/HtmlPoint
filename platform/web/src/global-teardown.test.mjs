import { describe, expect, it, vi } from "vitest";

import {
  terminatePosixProcessGroup,
  terminatePosixProcessGroups,
} from "../e2e/global-teardown.mjs";

function processError(code) {
  return Object.assign(new Error(code), { code });
}

describe("POSIX E2E process-group teardown", () => {
  it("waits for a process group to exit after SIGTERM", async () => {
    let probes = 0;
    const kill = vi.fn((_pid, signal) => {
      if (signal === 0 && probes++ > 0) throw processError("ESRCH");
    });

    await terminatePosixProcessGroup(41, { kill, wait: async () => {}, maxChecks: 3 });

    expect(kill).toHaveBeenCalledWith(-41, "SIGTERM");
    expect(kill).not.toHaveBeenCalledWith(-41, "SIGKILL");
  });

  it("accepts a process group that already exited", async () => {
    const kill = vi.fn(() => {
      throw processError("ESRCH");
    });

    await terminatePosixProcessGroup(42, { kill, wait: async () => {} });

    expect(kill).toHaveBeenCalledTimes(1);
    expect(kill).toHaveBeenCalledWith(-42, "SIGTERM");
  });

  it("escalates an ignored SIGTERM to SIGKILL and confirms exit", async () => {
    let killed = false;
    const kill = vi.fn((_pid, signal) => {
      if (signal === "SIGKILL") killed = true;
      if (signal === 0 && killed) throw processError("ESRCH");
    });

    await terminatePosixProcessGroup(43, {
      kill,
      wait: async () => {},
      maxChecks: 2,
    });

    expect(kill).toHaveBeenCalledWith(-43, "SIGKILL");
  });

  it("does not hide permission errors", async () => {
    const kill = vi.fn(() => {
      throw processError("EPERM");
    });

    await expect(terminatePosixProcessGroup(44, { kill, wait: async () => {} }))
      .rejects.toMatchObject({ code: "EPERM" });
  });

  it("cleans multiple process groups", async () => {
    const kill = vi.fn((_pid, signal) => {
      if (signal === 0) throw processError("ESRCH");
    });

    await terminatePosixProcessGroups([45, 46], { kill, wait: async () => {} });

    expect(kill).toHaveBeenCalledWith(-45, "SIGTERM");
    expect(kill).toHaveBeenCalledWith(-46, "SIGTERM");
  });

  it("fails if a process group survives SIGKILL", async () => {
    const kill = vi.fn();

    await expect(terminatePosixProcessGroup(47, {
      kill,
      wait: async () => {},
      maxChecks: 1,
    })).rejects.toThrow("E2E_PROCESS_GROUP_STUCK");
  });
});
