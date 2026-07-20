# Dual-screen Task 5 report

- State: implementation and automated verification complete; no WPF role window was opened and no physical certification was asserted.
- Trusted entry: the React projection path is selected only when `window.chrome.webview` exists together with an immutable Host-injected role, channel, and fixed-origin handshake. Native rendering bypasses `WorkspaceProvider`, browser workspace restoration, and local/session storage.
- Commit protocol: the renderer reports `projection_ready` once, validates bootstrap and ordered frames, reports `message_accepted`, and reports `frame_committed` only from a layout effect followed by two animation frames. The Host independently binds exact role, channel, session, course version, manifest, navigation, generation, sequence, and frame digest.
- Host policy: one exact built bundle is inventoried without hidden metadata, source maps, or reparse points and mapped to `https://projection.course-studio.test/index.html`. Unknown origins, paths, assets, worker sources, popups, downloads, permissions, external URI schemes, DevTools, browser extensions, host objects, storage, and service-worker registration fail closed.
- Assets: native visuals use opaque same-origin session URLs; the Host serves only manifest-known image types whose bytes match the registered SHA-256 inside the canonical session root.
- Runtime: Stable Evergreen WebView2 version, unique canonical executable paths, Microsoft Authenticode identity, and streaming SHA-256 values are bound twice at initialization and rechecked on process inventory changes.
- Build: Vite targets explicit `es2022`; React and Zod are split into stable vendor chunks. The largest production JavaScript chunk is 437.32 kB, down from the initial 505.06 kB warning.
- Verification: 18 focused Web tests passed, TypeScript typecheck passed, the production bundle built without the large-chunk warning, 29 non-integration .NET tests passed, and `git diff --check` passed.
