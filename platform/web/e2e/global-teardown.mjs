import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const runtimeRoot = resolve(webRoot, ".e2e-runtime");
const stateFile = resolve(runtimeRoot, "lifecycle.json");

const wait = (milliseconds) => new Promise((resolveWait) => setTimeout(resolveWait, milliseconds));

function signalGroup(pid, signal, kill) {
  try {
    kill(-pid, signal);
    return true;
  } catch (error) {
    if (error?.code === "ESRCH") return false;
    throw error;
  }
}

async function waitForGroupExit(pid, options) {
  for (let attempt = 0; attempt < options.maxChecks; attempt += 1) {
    if (!signalGroup(pid, 0, options.kill)) return true;
    await options.wait(options.intervalMs);
  }
  return !signalGroup(pid, 0, options.kill);
}

export async function terminatePosixProcessGroup(pid, dependencies = {}) {
  const options = {
    kill: dependencies.kill ?? process.kill.bind(process),
    wait: dependencies.wait ?? wait,
    intervalMs: dependencies.intervalMs ?? 50,
    maxChecks: dependencies.maxChecks ?? 20,
  };
  if (!signalGroup(pid, "SIGTERM", options.kill)) return;
  if (await waitForGroupExit(pid, options)) return;
  if (!signalGroup(pid, "SIGKILL", options.kill)) return;
  if (!await waitForGroupExit(pid, options)) {
    throw new Error("E2E_PROCESS_GROUP_STUCK");
  }
}

export async function terminatePosixProcessGroups(pids, dependencies = {}) {
  await Promise.all(pids.map((pid) => terminatePosixProcessGroup(pid, dependencies)));
}

export default async function globalTeardown() {
  if (!runtimeRoot.startsWith(`${webRoot}${sep}`) || !existsSync(stateFile)) return;
  const state = JSON.parse(readFileSync(stateFile, "utf8"));
  const pids = [state.helperPid, state.webPid].filter((pid) => Number.isInteger(pid) && pid > 0);
  if (process.platform === "win32") {
    for (const pid of pids) {
      // Windows detached processes are terminated as trees by the native launcher.
      spawnSync("taskkill.exe", ["/PID", String(pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" });
    }
    return;
  }
  // POSIX setup uses detached process groups; wait and escalate before returning.
  await terminatePosixProcessGroups(pids);
}
