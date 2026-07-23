import { createHash } from "node:crypto";
import { describe, expect, it, vi } from "vitest";

import { browserPolicyFileName, verifyBrowserPolicy } from "../e2e/browser-policy.mjs";

const windowsPolicy = {
  schemaVersion: 1,
  channel: "chrome",
  productName: "Google Chrome",
  productVersion: "150.0.0.1",
  fileVersion: "150.0.0.1",
  executableSha256: "a".repeat(64),
  publisher: "CN=Google LLC",
  allowedBasename: "chrome.exe",
};

const binary = Buffer.from("verified chrome executable");
const darwinPolicy = {
  schemaVersion: 1,
  platform: "darwin",
  channel: "chrome",
  productName: "Google Chrome",
  bundleIdentifier: "com.google.Chrome",
  productVersion: "150.0.0.2",
  bundleVersion: "8000.2",
  teamIdentifier: "EQHXZ8M8AV",
  executableRelativePath: "Contents/MacOS/Google Chrome",
  executableSha256: createHash("sha256").update(binary).digest("hex"),
  allowedArchitectures: ["arm64", "x86_64"],
};

function windowsDependencies(overrides = {}) {
  const actual = {
    Path: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    ProductVersion: windowsPolicy.productVersion,
    FileVersion: windowsPolicy.fileVersion,
    Publisher: windowsPolicy.publisher,
    SignatureStatus: "Valid",
    Sha256: windowsPolicy.executableSha256,
    ...overrides.actual,
  };
  return {
    candidates: [actual.Path],
    execFileSync: vi.fn(() => JSON.stringify(actual)),
    ...overrides.dependencies,
  };
}

function darwinDependencies(overrides = {}) {
  const bundle = overrides.bundle ?? "/Applications/Google Chrome.app";
  const executable = `${bundle}/Contents/MacOS/Google Chrome`;
  const plist = `${bundle}/Contents/Info.plist`;
  const stat = (path) => ({
    isSymbolicLink: () => overrides.symlink === path,
    isDirectory: () => path === bundle,
    isFile: () => path === executable || path === plist,
  });
  const spawn = vi.fn((command, args) => {
    const key = `${command} ${args.join(" ")}`;
    if (overrides.commandFailure?.(command, args)) return { status: 1, stdout: "", stderr: `/Users/private/${key}` };
    if (overrides.timeout?.(command, args)) return { status: null, error: new Error("timed out /Users/private") };
    let stderr = "";
    let stdout = "";
    if (command.endsWith("codesign") && args[0] === "-d") stderr = `Identifier=com.google.Chrome\nTeamIdentifier=${overrides.teamIdentifier ?? darwinPolicy.teamIdentifier}`;
    if (command.endsWith("plutil") && args[1] === "CFBundleShortVersionString") stdout = overrides.productVersion ?? darwinPolicy.productVersion;
    if (command.endsWith("plutil") && args[1] === "CFBundleVersion") stdout = overrides.bundleVersion ?? darwinPolicy.bundleVersion;
    if (command.endsWith("plutil") && args[1] === "CFBundleIdentifier") stdout = overrides.bundleIdentifier ?? darwinPolicy.bundleIdentifier;
    if (command.endsWith("lipo")) stdout = "arm64 x86_64";
    return { status: 0, stdout, stderr };
  });
  return {
    candidates: [bundle],
    home: "/Users/测试 User",
    lstatSync: overrides.lstatSync ?? stat,
    realpathSync: overrides.realpathSync ?? ((path) => path),
    readFileSync: () => overrides.binary ?? binary,
    spawnSync: spawn,
  };
}

describe("cross-platform browser policy", () => {
  it("dispatches Windows and preserves the Authenticode policy", () => {
    const dependencies = windowsDependencies();
    const result = verifyBrowserPolicy({
      platform: "win32",
      loadPolicy: (name) => {
        expect(name).toBe("browser-policy.json");
        return windowsPolicy;
      },
      windows: dependencies,
    });
    expect(result).toEqual(expect.objectContaining({
      platform: "win32",
      browserFamily: "chrome",
      provenance: "windows-authenticode-system-installation",
      verificationStatus: "verified",
      executableSha256: windowsPolicy.executableSha256,
    }));
    expect(dependencies.execFileSync).toHaveBeenCalledWith(
      "powershell.exe",
      expect.arrayContaining(["-NoProfile", "-NonInteractive"]),
      expect.objectContaining({ timeout: 10_000 }),
    );
  });

  it("dispatches macOS and returns the same stable descriptor shape", () => {
    const dependencies = darwinDependencies({ bundle: "/Applications/课程 Browser/Google Chrome.app" });
    const result = verifyBrowserPolicy({ platform: "darwin", loadPolicy: () => darwinPolicy, darwin: dependencies });
    expect(result).toEqual({
      platform: "darwin",
      browserFamily: "chrome",
      executablePath: "/Applications/课程 Browser/Google Chrome.app/Contents/MacOS/Google Chrome",
      productVersion: darwinPolicy.productVersion,
      provenance: "macos-codesign-system-installation",
      policyVersion: 1,
      executableSha256: darwinPolicy.executableSha256,
      verificationStatus: "verified",
    });
    expect(dependencies.spawnSync).toHaveBeenCalledWith(
      "/usr/bin/codesign",
      ["--verify", "--strict", "/Applications/课程 Browser/Google Chrome.app"],
      expect.objectContaining({ timeout: 10_000 }),
    );
  });

  it("fails closed on unsupported platforms and invalid policy JSON", () => {
    expect(() => browserPolicyFileName("linux")).toThrow("E2E_BROWSER_POLICY_MISMATCH");
    expect(() => verifyBrowserPolicy({ platform: "darwin", loadPolicy: () => { throw new SyntaxError("/Users/name/policy"); } }))
      .toThrowError(/^E2E_BROWSER_POLICY_MISMATCH$/);
    try {
      verifyBrowserPolicy({ platform: "darwin", loadPolicy: () => { throw new Error("/Users/private/policy"); } });
    } catch (error) {
      expect(error.stack).toBe("Error: E2E_BROWSER_POLICY_MISMATCH");
      expect(error.stack).not.toContain("/Users/");
    }
  });

  it.each([
    ["an empty architecture list", { ...darwinPolicy, allowedArchitectures: [] }],
    ["duplicate architectures", { ...darwinPolicy, allowedArchitectures: ["arm64", "arm64"] }],
    ["an empty version", { ...darwinPolicy, productVersion: "" }],
    ["a missing field", Object.fromEntries(Object.entries(darwinPolicy).filter(([key]) => key !== "bundleVersion"))],
    ["an additional field", { ...darwinPolicy, deprecatedField: true }],
    ["a hybrid platform policy", { ...darwinPolicy, allowedBasename: "chrome.exe", publisher: "CN=Google LLC" }],
    ["a platform mismatch", { ...darwinPolicy, platform: "win32" }],
  ])("rejects a Darwin policy with %s", (_name, policy) => {
    expect(() => verifyBrowserPolicy({
      platform: "darwin",
      loadPolicy: () => policy,
      darwin: darwinDependencies(),
    })).toThrowError(/^E2E_BROWSER_POLICY_MISMATCH$/);
  });

  it.each([
    ["an empty version", { ...windowsPolicy, productVersion: "" }],
    ["a missing field", Object.fromEntries(Object.entries(windowsPolicy).filter(([key]) => key !== "fileVersion"))],
    ["an additional field", { ...windowsPolicy, deprecatedField: true }],
    ["Darwin fields", { ...windowsPolicy, platform: "darwin", bundleIdentifier: "com.google.Chrome" }],
  ])("rejects a Windows policy with %s", (_name, policy) => {
    expect(() => verifyBrowserPolicy({
      platform: "win32",
      loadPolicy: () => policy,
      windows: windowsDependencies(),
    })).toThrowError(/^E2E_BROWSER_POLICY_MISMATCH$/);
  });

  it.each([
    ["missing browser", { lstatSync: () => { throw new Error("ENOENT /Users/name"); } }],
    ["unexpected real path", { realpathSync: (path) => `${path}.replacement` }],
  ])("rejects %s without leaking its path", (_name, override) => {
    expect(() => verifyBrowserPolicy({
      platform: "darwin",
      loadPolicy: () => darwinPolicy,
      darwin: darwinDependencies(override),
    })).toThrowError(/^E2E_BROWSER_POLICY_MISMATCH$/);
  });

  it("rejects symlinks", () => {
    const path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
    expect(() => verifyBrowserPolicy({
      platform: "darwin",
      loadPolicy: () => darwinPolicy,
      darwin: darwinDependencies({ symlink: path }),
    })).toThrow("E2E_BROWSER_POLICY_MISMATCH");
  });

  it.each([
    ["version mismatch", { productVersion: "151.0.0.0" }],
    ["provenance mismatch", { teamIdentifier: "AAAAAAAAAA" }],
    ["hash mismatch", { binary: Buffer.from("different") }],
    ["external command failure", { commandFailure: (command) => command.endsWith("codesign") }],
    ["external command timeout", { timeout: (command) => command.endsWith("plutil") }],
  ])("rejects %s", (_name, override) => {
    expect(() => verifyBrowserPolicy({
      platform: "darwin",
      loadPolicy: () => darwinPolicy,
      darwin: darwinDependencies(override),
    })).toThrowError(/^E2E_BROWSER_POLICY_MISMATCH$/);
  });

  it.each([
    ["path mismatch", { Path: "C:\\Temp\\not-chrome.exe" }],
    ["version mismatch", { ProductVersion: "151.0.0.0" }],
    ["signature mismatch", { SignatureStatus: "NotSigned" }],
    ["publisher mismatch", { Publisher: "CN=Someone Else" }],
    ["hash mismatch", { Sha256: "b".repeat(64) }],
  ])("keeps Windows rejection for %s", (_name, actual) => {
    expect(() => verifyBrowserPolicy({
      platform: "win32",
      loadPolicy: () => windowsPolicy,
      windows: windowsDependencies({ actual }),
    })).toThrow("E2E_BROWSER_POLICY_MISMATCH");
  });
});
