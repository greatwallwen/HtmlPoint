import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { VerifiedHelperSession } from "./helper-session";
import { ArtifactClient } from "./artifact-client";
import { SAFE_HELPER_FAILURE_MESSAGE } from "./knowledge-client";

const helperOrigin = "http://127.0.0.1:8765";
const token = "t".repeat(43);
const session = { helperOrigin, sessionToken: token } as VerifiedHelperSession;

function artifactResponse(
  bytes: Uint8Array,
  options: { status?: number; mediaType?: string; contentLength?: string } = {},
) {
  const status = options.status ?? 200;
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({
      "Content-Type": options.mediaType ?? "image/png",
      "Content-Length": options.contentLength ?? String(bytes.byteLength),
    }),
    arrayBuffer: vi.fn().mockResolvedValue(
      bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
    ),
  };
}

beforeEach(() => {
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: vi.fn()
      .mockReturnValueOnce("blob:first")
      .mockReturnValueOnce("blob:second"),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: vi.fn(),
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("ArtifactClient", () => {
  it("loads only a bounded authenticated artifact and never puts the token in the URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      artifactResponse(new Uint8Array([1, 2, 3, 4])),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = new ArtifactClient(session);

    await expect(client.fetchArtifact("artifact-1")).resolves.toEqual({
      artifactId: "artifact-1",
      mediaType: "image/png",
      byteSize: 4,
      objectUrl: "blob:first",
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${helperOrigin}/v1/artifacts/artifact-1`);
    expect(url).not.toContain(token);
    expect(init.credentials).toBe("omit");
    expect(new Headers(init.headers).get("X-Course-Session")).toBe(token);
  });

  it("revokes the previous Blob URL on replacement and the current URL on disposal", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(artifactResponse(new Uint8Array([1])))
      .mockResolvedValueOnce(
        artifactResponse(new Uint8Array([2, 3]), { mediaType: "image/webp" }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const client = new ArtifactClient(session);

    await client.fetchArtifact("artifact-1");
    await client.fetchArtifact("artifact-2");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:first");
    expect(client.current?.objectUrl).toBe("blob:second");

    client.dispose();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:second");
    expect(client.current).toBeUndefined();
    client.dispose();
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2);
  });

  it.each([
    artifactResponse(new Uint8Array([1]), { status: 404 }),
    artifactResponse(new Uint8Array([1]), { mediaType: "text/html" }),
    artifactResponse(new Uint8Array([1]), { contentLength: "0" }),
    artifactResponse(new Uint8Array([1]), { contentLength: "2" }),
    artifactResponse(new Uint8Array([1]), { contentLength: String(32 * 1024 * 1024 + 1) }),
  ])("fails closed for invalid artifact responses", async (invalidResponse) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(invalidResponse));
    const client = new ArtifactClient(session);
    await expect(client.fetchArtifact("artifact-1")).rejects.toThrow(
      SAFE_HELPER_FAILURE_MESSAGE,
    );
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it("supports caller abort and keeps user-visible failures scrubbed", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn((_url: string, init?: RequestInit) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () =>
          reject(new Error("D:/private/token=" + token)),
        );
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = new ArtifactClient(session);

    const pending = client.fetchArtifact("artifact-1", controller.signal);
    controller.abort();
    await expect(pending).rejects.toThrow(SAFE_HELPER_FAILURE_MESSAGE);
    await expect(pending).rejects.not.toThrow(token);
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it("rejects non-opaque identities before any network request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const client = new ArtifactClient(session);
    await expect(client.fetchArtifact("../../private.png")).rejects.toThrow(
      SAFE_HELPER_FAILURE_MESSAGE,
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
