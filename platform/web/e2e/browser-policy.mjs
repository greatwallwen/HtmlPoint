import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { verifyDarwinChrome } from "./browser-policy-darwin.mjs";
import { verifyWindowsChrome } from "./browser-policy-windows.mjs";

const here = dirname(fileURLToPath(import.meta.url));

const platformPolicies = Object.freeze({
  darwin: "browser-policy.darwin.json",
  win32: "browser-policy.json",
});

function policyMismatch() {
  const error = new Error("E2E_BROWSER_POLICY_MISMATCH");
  error.stack = "Error: E2E_BROWSER_POLICY_MISMATCH";
  return error;
}

export function browserPolicyFileName(platform = process.platform) {
  const fileName = platformPolicies[platform];
  if (!fileName) throw policyMismatch();
  return fileName;
}

export function verifyBrowserPolicy(options = {}) {
  const platform = options.platform ?? process.platform;
  try {
    const fileName = browserPolicyFileName(platform);
    const loadPolicy = options.loadPolicy ?? ((name) => JSON.parse(readFileSync(resolve(here, name), "utf8")));
    const policy = loadPolicy(fileName);
    if (platform === "win32") return verifyWindowsChrome(policy, options.windows);
    if (platform === "darwin") return verifyDarwinChrome(policy, options.darwin);
  } catch {
    // Keep failures stable and path-free so receipts and logs do not expose user directories.
  }
  throw policyMismatch();
}

// Compatibility for the existing Playwright configuration import.
export const verifySystemChrome = verifyBrowserPolicy;
