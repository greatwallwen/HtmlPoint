import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { basename, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const policy = JSON.parse(readFileSync(resolve(here, "browser-policy.json"), "utf8"));
const candidates = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  resolve(process.env.LOCALAPPDATA ?? "", "Google", "Chrome", "Application", "chrome.exe"),
];

export function verifySystemChrome() {
  for (const candidate of candidates) {
    try {
      const script = [
        `$p=${JSON.stringify(candidate)}`,
        "$i=Get-Item -LiteralPath $p -ErrorAction Stop",
        "$s=Get-AuthenticodeSignature -LiteralPath $p",
        "$h=Get-FileHash -Algorithm SHA256 -LiteralPath $p",
        "[pscustomobject]@{Path=$i.FullName;ProductVersion=$i.VersionInfo.ProductVersion;FileVersion=$i.VersionInfo.FileVersion;Publisher=$s.SignerCertificate.Subject;SignatureStatus=[string]$s.Status;Sha256=$h.Hash.ToLowerInvariant()}|ConvertTo-Json -Compress",
      ].join(";");
      const raw = execFileSync(
        "powershell.exe",
        ["-NoProfile", "-NonInteractive", "-Command", script],
        { encoding: "utf8", windowsHide: true, timeout: 10_000 },
      );
      const actual = JSON.parse(raw);
      const matches =
        basename(actual.Path).toLowerCase() === policy.allowedBasename &&
        actual.ProductVersion === policy.productVersion &&
        actual.FileVersion === policy.fileVersion &&
        actual.Publisher === policy.publisher &&
        actual.SignatureStatus === "Valid" &&
        actual.Sha256 === policy.executableSha256;
      if (!matches) throw new Error("policy mismatch");
      return { executablePath: actual.Path, policy };
    } catch {
      // Try the next fixed system installation path.
    }
  }
  throw new Error("E2E_BROWSER_POLICY_MISMATCH");
}
