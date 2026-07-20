# Dual-screen Task 4 report

- State: complete; Supergrill checkpoint closed without input drift.
- Role windows: one Host-owned HWND-backed WPF window per role, external-first Stage and internal-first Presenter defaults, one Swap action, generation checks, exact physical rectangles, and full rollback on partial open/fullscreen failure.
- Fullscreen: original style and placement are saved, `WS_POPUP` plus `SWP_FRAMECHANGED` is applied, and evidence checks window rect, DWM frame rect, monitor rect, visibility, minimize, and cloak state.
- Invalidation: Escape, user close, move, DPI change, minimize, cloak, stale generation, and role collision fail closed.
- Witness: two independent six-character codes, 90-second lifetime, salted HMAC-only retention, fixed-time comparison, one attempt, replay rejection, zeroization, and a guarded native proof constructor.
- UI: light role-colored non-focus overlays plus one native Presenter input dialog; all test paths use fakes and show no visible windows.
- Verification: 21 solution tests passed with `--no-restore`; `git diff --check` passed.
