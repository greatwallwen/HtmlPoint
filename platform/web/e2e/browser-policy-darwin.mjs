import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { lstatSync, readFileSync, realpathSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";

import { isDarwinBrowserPolicy } from "./browser-policy-schema.mjs";

const executableRelativePath = join("Contents", "MacOS", "Google Chrome");
const plistRelativePath = join("Contents", "Info.plist");

function run(command, args, execute) {
  const result = execute(command, args, { encoding: "utf8", timeout: 10_000, windowsHide: true });
  if (result.error || result.status !== 0) throw new Error("command failed");
  return `${result.stdout ?? ""}\n${result.stderr ?? ""}`.trim();
}

export function verifyDarwinChrome(policy, dependencies = {}) {
  if (!isDarwinBrowserPolicy(policy)) throw new Error("E2E_BROWSER_POLICY_MISMATCH");
  const execute = dependencies.spawnSync ?? spawnSync;
  const stat = dependencies.lstatSync ?? lstatSync;
  const realpath = dependencies.realpathSync ?? realpathSync;
  const read = dependencies.readFileSync ?? readFileSync;
  const home = dependencies.home ?? homedir();
  const candidates = dependencies.candidates ?? ["/Applications/Google Chrome.app", resolve(home, "Applications", "Google Chrome.app")];

  for (const candidate of candidates) {
    try {
      const bundlePath = resolve(candidate);
      const executablePath = resolve(bundlePath, executableRelativePath);
      const plistPath = resolve(bundlePath, plistRelativePath);
      if (stat(bundlePath).isSymbolicLink() || stat(executablePath).isSymbolicLink() || stat(plistPath).isSymbolicLink()) continue;
      if (!stat(bundlePath).isDirectory() || !stat(executablePath).isFile() || !stat(plistPath).isFile()) continue;
      if (realpath(bundlePath) !== bundlePath || realpath(executablePath) !== executablePath || realpath(plistPath) !== plistPath) continue;

      // macOS: verify the complete bundle before trusting metadata or its executable.
      run("/usr/bin/codesign", ["--verify", "--strict", bundlePath], execute);
      const signature = run("/usr/bin/codesign", ["-d", "--verbose=4", bundlePath], execute);
      const productVersion = run("/usr/bin/plutil", ["-extract", "CFBundleShortVersionString", "raw", plistPath], execute);
      const bundleVersion = run("/usr/bin/plutil", ["-extract", "CFBundleVersion", "raw", plistPath], execute);
      const bundleIdentifier = run("/usr/bin/plutil", ["-extract", "CFBundleIdentifier", "raw", plistPath], execute);
      const architectures = run("/usr/bin/lipo", ["-archs", executablePath], execute).split(/\s+/).filter(Boolean);
      const executableSha256 = createHash("sha256").update(read(executablePath)).digest("hex");
      if (
        !signature.split(/\r?\n/).includes(`TeamIdentifier=${policy.teamIdentifier}`) ||
        bundleIdentifier !== policy.bundleIdentifier ||
        productVersion !== policy.productVersion ||
        bundleVersion !== policy.bundleVersion ||
        executableSha256 !== policy.executableSha256 ||
        architectures.length === 0 ||
        architectures.some((arch) => !policy.allowedArchitectures.includes(arch))
      ) continue;
      return {
        platform: "darwin",
        browserFamily: "chrome",
        executablePath,
        productVersion,
        provenance: "macos-codesign-system-installation",
        policyVersion: policy.schemaVersion,
        executableSha256,
        verificationStatus: "verified",
      };
    } catch {
      // Fixed candidates only. Errors are intentionally redacted by the dispatcher.
    }
  }
  throw new Error("E2E_BROWSER_POLICY_MISMATCH");
}
