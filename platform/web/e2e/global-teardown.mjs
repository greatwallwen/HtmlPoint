import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const runtimeRoot = resolve(webRoot, ".e2e-runtime");
const stateFile = resolve(runtimeRoot, "lifecycle.json");

export default async function globalTeardown() {
  if (!runtimeRoot.startsWith(`${webRoot}${sep}`) || !existsSync(stateFile)) return;
  const state = JSON.parse(readFileSync(stateFile, "utf8"));
  for (const pid of [state.helperPid, state.webPid]) {
    if (!Number.isInteger(pid) || pid < 1) continue;
    spawnSync("taskkill.exe", ["/PID", String(pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" });
  }
}
