import { spawn } from "node:child_process";
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { dirname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const workspaceRoot = resolve(webRoot, "..", "..");
const runtimeRoot = resolve(webRoot, ".e2e-runtime");
const stateFile = resolve(runtimeRoot, "lifecycle.json");

function assertRuntimePath(path) {
  if (!resolve(path).startsWith(`${webRoot}${sep}`)) throw new Error("E2E_RUNTIME_PATH_ESCAPE");
}

async function freePort() {
  return await new Promise((resolvePort, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close(() => resolvePort(port));
    });
  });
}

async function waitFor(url, predicate = (response) => response.ok) {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { redirect: "error" });
      if (predicate(response)) return;
    } catch {}
    await new Promise((resolveWait) => setTimeout(resolveWait, 150));
  }
  throw new Error(`E2E_SERVER_NOT_READY:${url}`);
}

export default async function globalSetup() {
  for (const name of ["COURSE_REFERENCE_ROOT", "COURSE_NETWORK_VISUAL_TEST", "COURSE_EMBEDDING_MODEL_DOWNLOAD"]) {
    if (process.env[name]) throw new Error(`E2E_OFFLINE_ENV_PRESENT:${name}`);
  }
  assertRuntimePath(runtimeRoot);
  if (existsSync(runtimeRoot)) rmSync(runtimeRoot, { recursive: true, force: true });
  mkdirSync(runtimeRoot, { recursive: true });
  const webPort = await freePort();
  const helperPort = await freePort();
  const webOrigin = `http://127.0.0.1:${webPort}`;
  const helperOrigin = `http://127.0.0.1:${helperPort}`;
  const launchFile = resolve(runtimeRoot, "launch.json");
  const webLog = openSync(resolve(runtimeRoot, "web.log"), "w");
  const helperLog = openSync(resolve(runtimeRoot, "helper.log"), "w");
  const npmCli = process.env.npm_execpath;
  if (!npmCli) throw new Error("E2E_NPM_CLI_MISSING");
  const web = spawn(
    process.execPath,
    [npmCli, "run", "dev", "--", "--host", "127.0.0.1", "--port", String(webPort), "--strictPort"],
    { cwd: webRoot, detached: true, windowsHide: true, stdio: ["ignore", webLog, webLog] },
  );
  // All systems: persist each detached owner immediately for partial-setup cleanup.
  writeFileSync(
    stateFile,
    JSON.stringify({ schemaVersion: 1, webPid: web.pid }),
    "utf8",
  );
  const helper = spawn(
    "python",
    ["-m", "course_helper.e2e_server", "--runtime-root", runtimeRoot, "--web-origin", webOrigin, "--port", String(helperPort), "--launch-file", launchFile],
    {
      cwd: resolve(workspaceRoot, "platform", "helper"),
      detached: true,
      windowsHide: true,
      env: { ...process.env, COURSE_E2E_FIXTURE: "1" },
      stdio: ["ignore", helperLog, helperLog],
    },
  );
  writeFileSync(
    stateFile,
    JSON.stringify({ schemaVersion: 1, webPid: web.pid, helperPid: helper.pid }),
    "utf8",
  );
  closeSync(webLog);
  closeSync(helperLog);
  web.unref();
  helper.unref();
  await waitFor(webOrigin);
  await waitFor(`${helperOrigin}/health`);
  const deadline = Date.now() + 10_000;
  while (!existsSync(launchFile) && Date.now() < deadline) {
    await new Promise((resolveWait) => setTimeout(resolveWait, 100));
  }
  if (!existsSync(launchFile)) throw new Error("E2E_LAUNCH_FILE_MISSING");
  const launch = JSON.parse(readFileSync(launchFile, "utf8"));
  writeFileSync(stateFile, JSON.stringify({ schemaVersion: 1, webPid: web.pid, helperPid: helper.pid, ...launch }), "utf8");
}
