const windowsKeys = Object.freeze([
  "allowedBasename",
  "channel",
  "executableSha256",
  "fileVersion",
  "productName",
  "productVersion",
  "publisher",
  "schemaVersion",
]);

const darwinKeys = Object.freeze([
  "allowedArchitectures",
  "bundleIdentifier",
  "bundleVersion",
  "channel",
  "executableRelativePath",
  "executableSha256",
  "platform",
  "productName",
  "productVersion",
  "schemaVersion",
  "teamIdentifier",
]);

function hasExactKeys(value, expected) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

const isVersion = (value) => typeof value === "string" && /^\d+(?:\.\d+)+$/.test(value);
const isSha256 = (value) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);

export function isWindowsBrowserPolicy(policy) {
  return hasExactKeys(policy, windowsKeys) &&
    policy.schemaVersion === 1 &&
    policy.channel === "chrome" &&
    policy.productName === "Google Chrome" &&
    policy.allowedBasename === "chrome.exe" &&
    isVersion(policy.productVersion) &&
    isVersion(policy.fileVersion) &&
    isSha256(policy.executableSha256) &&
    typeof policy.publisher === "string" &&
    policy.publisher.length > 0;
}

export function isDarwinBrowserPolicy(policy) {
  const architectures = policy?.allowedArchitectures;
  return hasExactKeys(policy, darwinKeys) &&
    policy.schemaVersion === 1 &&
    policy.platform === "darwin" &&
    policy.channel === "chrome" &&
    policy.productName === "Google Chrome" &&
    policy.bundleIdentifier === "com.google.Chrome" &&
    policy.executableRelativePath === "Contents/MacOS/Google Chrome" &&
    isVersion(policy.productVersion) &&
    isVersion(policy.bundleVersion) &&
    /^[A-Z0-9]{10}$/.test(policy.teamIdentifier ?? "") &&
    isSha256(policy.executableSha256) &&
    Array.isArray(architectures) &&
    architectures.length > 0 &&
    new Set(architectures).size === architectures.length &&
    architectures.every((value) => value === "arm64" || value === "x86_64");
}
