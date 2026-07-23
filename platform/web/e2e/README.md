# Playwright browser policy maintenance

Playwright evidence runs fail closed when the installed Chrome no longer matches
the committed platform policy. Never copy values from an unverified browser,
accept a path from an environment variable, or update a policy automatically.

## macOS audit

Choose exactly one existing fixed candidate:

```sh
app='/Applications/Google Chrome.app'
# For the user-level fixed candidate, use:
# app="$HOME/Applications/Google Chrome.app"
executable="$app/Contents/MacOS/Google Chrome"
plist="$app/Contents/Info.plist"
```

Run every check against that same bundle:

```sh
/usr/bin/codesign --verify --strict "$app"
/usr/bin/codesign -d --verbose=4 "$app"
/usr/bin/plutil -extract CFBundleIdentifier raw "$plist"
/usr/bin/plutil -extract CFBundleShortVersionString raw "$plist"
/usr/bin/plutil -extract CFBundleVersion raw "$plist"
/usr/bin/lipo -archs "$executable"
/usr/bin/shasum -a 256 "$executable"
```

Stop if `codesign --verify` is nonzero, the identifier is not
`com.google.Chrome`, the Team Identifier is not the committed Google identity,
the architecture set is unexpected, or any inspected path is a symbolic link.
Update `browser-policy.darwin.json` only after all checks pass.

## Windows audit

Use one of the fixed candidates already encoded in
`browser-policy-windows.mjs`; do not search `PATH`. In a non-elevated
PowerShell session, run:

```powershell
$p = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$item = Get-Item -LiteralPath $p -ErrorAction Stop
$signature = Get-AuthenticodeSignature -LiteralPath $p
$hash = Get-FileHash -Algorithm SHA256 -LiteralPath $p
$item.FullName
$item.VersionInfo.ProductVersion
$item.VersionInfo.FileVersion
$signature.Status
$signature.SignerCertificate.Subject
$hash.Hash.ToLowerInvariant()
```

Stop unless the resolved path is the selected fixed candidate, signature status
is `Valid`, publisher is the expected Google subject, both versions are
non-empty and expected, and the SHA-256 was computed from that same executable.
Only then update `browser-policy.json`.

## Evidence and review

For each policy update, save a small JSON audit receipt under
`platform/web/evidence/browser-policy-audit/` named
`<platform>-chrome-<version>.json`. Do not include a username or absolute Home
path. Record:

- candidate class (`system` or `user`), platform and architecture;
- bundle/product/file version values;
- bundle identifier and Team Identifier on macOS, or Authenticode status and
  publisher on Windows;
- executable SHA-256;
- every command exit status;
- policy file SHA-256;
- reviewer and review date.

The policy diff and audit receipt require a second-person review. After review,
run:

```sh
npm --prefix platform/web test -- --run src/browser-policy.test.mjs src/global-teardown.test.mjs
npm --prefix platform/web test -- --run --exclude "**/*.projection-integration.test.ts"
npm --prefix platform/web run typecheck
npm --prefix platform/web run build
.venv/bin/python platform/qa/run.py focused
npm --prefix platform/web run test:e2e
```

The final command must use the fully verified system browser and produce the
normal E2E receipt. Mocked policy tests are not release-browser evidence, and a
single-screen run never certifies physical dual-screen behavior.
