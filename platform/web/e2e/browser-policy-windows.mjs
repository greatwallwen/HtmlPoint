import { execFileSync } from "node:child_process";
import { resolve, win32 } from "node:path";

import { isWindowsBrowserPolicy } from "./browser-policy-schema.mjs";

const fixedCandidates = (localAppData = "") => [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  resolve(localAppData, "Google", "Chrome", "Application", "chrome.exe"),
];

export function verifyWindowsChrome(policy, dependencies = {}) {
  if (!isWindowsBrowserPolicy(policy)) throw new Error("E2E_BROWSER_POLICY_MISMATCH");
  const execute = dependencies.execFileSync ?? execFileSync;
  const candidates = dependencies.candidates ?? fixedCandidates(dependencies.localAppData ?? process.env.LOCALAPPDATA);
  for (const candidate of candidates) {
    try {
      const script = [
        `$p=${JSON.stringify(candidate)}`,
        "$i=Get-Item -LiteralPath $p -ErrorAction Stop",
        "$s=Get-AuthenticodeSignature -LiteralPath $p",
        "$h=Get-FileHash -Algorithm SHA256 -LiteralPath $p",
        "[pscustomobject]@{Path=$i.FullName;ProductVersion=$i.VersionInfo.ProductVersion;FileVersion=$i.VersionInfo.FileVersion;Publisher=$s.SignerCertificate.Subject;SignatureStatus=[string]$s.Status;Sha256=$h.Hash.ToLowerInvariant()}|ConvertTo-Json -Compress",
      ].join(";");
      const actual = JSON.parse(execute(
        "powershell.exe",
        ["-NoProfile", "-NonInteractive", "-Command", script],
        { encoding: "utf8", windowsHide: true, timeout: 10_000 },
      ));
      if (
        win32.normalize(actual.Path).toLowerCase() !== win32.normalize(candidate).toLowerCase() ||
        win32.basename(actual.Path).toLowerCase() !== policy.allowedBasename ||
        actual.ProductVersion !== policy.productVersion ||
        actual.FileVersion !== policy.fileVersion ||
        actual.Publisher !== policy.publisher ||
        actual.SignatureStatus !== "Valid" ||
        actual.Sha256 !== policy.executableSha256
      ) continue;
      return {
        platform: "win32",
        browserFamily: "chrome",
        executablePath: actual.Path,
        productVersion: actual.ProductVersion,
        provenance: "windows-authenticode-system-installation",
        policyVersion: policy.schemaVersion,
        executableSha256: actual.Sha256,
        verificationStatus: "verified",
      };
    } catch {
      // Try only the next fixed installation path; never PATH or caller input.
    }
  }
  throw new Error("E2E_BROWSER_POLICY_MISMATCH");
}
