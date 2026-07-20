# Dual-screen Task 3 report

- State: complete; Supergrill checkpoint closed without input drift.
- Native read: `QueryDisplayConfig`, `DisplayConfigGetDeviceInfo`, `EnumDisplayMonitors`, and `GetMonitorInfo` are used read-only under an effective PerMonitorV2 thread context.
- Boundary: raw adapter, GDI device, friendly, and PnP path values remain internal; public displays carry only per-session HMAC-SHA256 identifiers and bounded geometry/DPI metadata.
- Eligibility: only exactly two distinct local hardware candidates in extended mode pass; duplicate, remote, three-display, missing, overflow, and known virtual/indirect shapes fail closed.
- Safety: the source gate found none of the six forbidden display-mutating API names.
- Current-machine smoke: two anonymous displays were read in extended topology; the reducer stopped at `Candidate` with both certification booleans false.
- Verification: 11 total tests and the dedicated read-only smoke passed with `--no-restore`; `git diff --check` passed.
