import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { knowledgeSummarySchema } from "../domain/knowledge-schema";
import {
  HELPER_SESSION_STORAGE_KEY,
  bootstrapHelperSession,
  resetHelperSessionBootstrapForTests,
} from "./helper-session";
import * as helperSessionModule from "./helper-session";
import { KnowledgeClient } from "./knowledge-client";

const nonce = "n".repeat(43);
const token = "t".repeat(43);
const helperOrigin = "http://127.0.0.1:8765";
const validSummary = {
  schemaVersion: 1,
  sourceCount: 5,
  publishedCardCount: 12,
  reviewTaskCount: 2,
  retrievalMode: "fts-degraded",
  tagLabels: ["大语言模型", "数据分析"],
  updatedAt: "2026-07-17T02:00:00Z",
} as const;

function response(body: unknown, status = 200): Pick<Response, "ok" | "status" | "json"> {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  };
}

function launchFragment(base = helperOrigin, launchNonce = nonce): string {
  return `/#helper=${encodeURIComponent(base)}&nonce=${encodeURIComponent(launchNonce)}`;
}

beforeEach(() => {
  resetHelperSessionBootstrapForTests();
  window.history.replaceState(null, "", "/");
  window.sessionStorage.clear();
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  resetHelperSessionBootstrapForTests();
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
});

describe("knowledge summary schema", () => {
  it("accepts only the strict version-one summary contract", () => {
    expect(knowledgeSummarySchema.parse(validSummary)).toEqual(validSummary);
    expect(() => knowledgeSummarySchema.parse({ ...validSummary, extra: true })).toThrow();
  });

  it.each([
    { ...validSummary, schemaVersion: 2 },
    { ...validSummary, sourceCount: -1 },
    { ...validSummary, publishedCardCount: 1.5 },
    { ...validSummary, reviewTaskCount: -1 },
    { ...validSummary, retrievalMode: "vector" },
    { ...validSummary, tagLabels: [""] },
    { ...validSummary, updatedAt: "2026-07-17" },
    { ...validSummary, updatedAt: "not-a-date" },
  ])("rejects an invalid summary boundary", (candidate) => {
    expect(() => knowledgeSummarySchema.parse(candidate)).toThrow();
  });
});

describe("helper session bootstrap", () => {
  it("prepares and scrubs launch material synchronously before exchanging it", async () => {
    window.history.replaceState(null, "", launchFragment());
    const fetchMock = vi.fn().mockResolvedValue(response({ sessionToken: token }));
    vi.stubGlobal("fetch", fetchMock);

    expect(helperSessionModule).toHaveProperty("prepareHelperSessionLaunch");
    const prepare = (
      helperSessionModule as typeof helperSessionModule & {
        prepareHelperSessionLaunch(): void;
      }
    ).prepareHelperSessionLaunch;
    prepare();

    expect(window.location.hash).toBe("");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(window.sessionStorage).toHaveLength(0);

    const first = bootstrapHelperSession();
    const second = bootstrapHelperSession();
    expect(second).toBe(first);
    await expect(first).resolves.toMatchObject({ helperOrigin, sessionToken: token });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("clears launch material synchronously and exchanges it once without setting Origin", async () => {
    window.history.replaceState(null, "", launchFragment());
    const fetchMock = vi.fn().mockResolvedValue(response({ sessionToken: token }));
    vi.stubGlobal("fetch", fetchMock);

    const pending = bootstrapHelperSession();

    expect(window.location.hash).toBe("");
    const session = await pending;
    expect(session).toMatchObject({ helperOrigin, sessionToken: token });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${helperOrigin}/v1/session/exchange`);
    expect(init).toMatchObject({
      method: "POST",
      credentials: "omit",
      body: JSON.stringify({ nonce }),
    });
    expect(new Headers(init.headers).has("Origin")).toBe(false);
    expect(window.localStorage).toHaveLength(0);
    expect(window.location.href).not.toContain(nonce);
    expect(window.location.href).not.toContain(token);
    expect(window.sessionStorage.getItem(HELPER_SESSION_STORAGE_KEY)).toContain(token);
  });

  it("reuses one in-flight exchange across concurrent StrictMode-style bootstraps", async () => {
    window.history.replaceState(null, "", launchFragment());
    let resolveExchange!: (value: Pick<Response, "ok" | "status" | "json">) => void;
    const fetchMock = vi.fn().mockReturnValue(
      new Promise((resolve) => {
        resolveExchange = resolve;
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const first = bootstrapHelperSession();
    const second = bootstrapHelperSession();

    expect(second).toBe(first);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    resolveExchange(response({ sessionToken: token }));
    await expect(first).resolves.toMatchObject({ sessionToken: token });
    await expect(second).resolves.toMatchObject({ sessionToken: token });
  });

  it("restores a validated base and token after the fragment has been cleared", async () => {
    window.history.replaceState(null, "", launchFragment());
    const fetchMock = vi.fn().mockResolvedValue(response({ sessionToken: token }));
    vi.stubGlobal("fetch", fetchMock);
    await bootstrapHelperSession();
    resetHelperSessionBootstrapForTests();
    fetchMock.mockClear();

    const restored = await bootstrapHelperSession();

    expect(restored).toMatchObject({ helperOrigin, sessionToken: token });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    "#helper=http%3A%2F%2Flocalhost%3A8765&nonce=" + nonce,
    "#helper=http%3A%2F%2F%5B%3A%3A1%5D%3A8765&nonce=" + nonce,
    "#helper=https%3A%2F%2F127.0.0.1%3A8765&nonce=" + nonce,
    "#helper=http%3A%2F%2Fuser%3Apass%40127.0.0.1%3A8765&nonce=" + nonce,
    "#helper=http%3A%2F%2F127.0.0.1%3A8765%2Fpath&nonce=" + nonce,
    "#helper=http%3A%2F%2F127.0.0.1%3A8765%3Fx%3D1&nonce=" + nonce,
    "#helper=http%3A%2F%2F127.0.0.1%3A0&nonce=" + nonce,
    "#helper=http%3A%2F%2F127.0.0.1%3A65536&nonce=" + nonce,
    "#helper=" + encodeURIComponent(helperOrigin) + "&helper=" + encodeURIComponent(helperOrigin) + "&nonce=" + nonce,
    "#helper=" + encodeURIComponent(helperOrigin) + "&nonce=" + nonce + "&nonce=" + nonce,
    "#helper=" + encodeURIComponent(helperOrigin) + "&nonce=" + nonce + "&extra=x",
    "#helper=" + encodeURIComponent(helperOrigin),
    "#helper=%ZZ&nonce=" + nonce,
    "#helper=" + encodeURIComponent(helperOrigin) + "&nonce=",
    "#helper=" + encodeURIComponent(helperOrigin) + "&nonce=short",
    "#helper=" + encodeURIComponent(helperOrigin) + "&nonce=" + encodeURIComponent("+".repeat(43)),
  ])("clears and rejects malformed launch material: %s", async (fragment) => {
    window.history.replaceState(null, "", `/${fragment}`);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(bootstrapHelperSession()).resolves.toBeUndefined();

    expect(window.location.hash).toBe("");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(window.sessionStorage).toHaveLength(0);
    expect(window.localStorage).toHaveLength(0);
  });

  it("stays offline without launch material and fails closed on bad storage", async () => {
    window.sessionStorage.setItem(HELPER_SESSION_STORAGE_KEY, "not-json");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(bootstrapHelperSession()).resolves.toBeUndefined();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(window.sessionStorage.getItem(HELPER_SESSION_STORAGE_KEY)).toBeNull();
  });

  it("does not persist an invalid or rejected exchange response", async () => {
    window.history.replaceState(null, "", launchFragment());
    const fetchMock = vi.fn().mockResolvedValue(response({ sessionToken: "short" }, 401));
    vi.stubGlobal("fetch", fetchMock);

    await expect(bootstrapHelperSession()).resolves.toBeUndefined();

    expect(window.sessionStorage).toHaveLength(0);
    expect(window.localStorage).toHaveLength(0);
  });
});

describe("KnowledgeClient", () => {
  async function validatedSession() {
    window.history.replaceState(null, "", launchFragment());
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ sessionToken: token })));
    const session = await bootstrapHelperSession();
    expect(session).toBeDefined();
    return session!;
  }

  it("uses only the fixed summary path, session header, and omitted credentials", async () => {
    const session = await validatedSession();
    const fetchMock = vi.fn().mockResolvedValue(response(validSummary));
    vi.stubGlobal("fetch", fetchMock);
    const client = new KnowledgeClient(session);

    await expect(client.getSummary()).resolves.toEqual(validSummary);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${helperOrigin}/v1/knowledge/summary`);
    expect(url).not.toContain(token);
    expect(init.credentials).toBe("omit");
    expect(new Headers(init.headers).get("X-Course-Session")).toBe(token);
    expect(new Headers(init.headers).has("Origin")).toBe(false);
  });

  it("fails safely for HTTP, JSON, and strict-schema errors", async () => {
    const session = await validatedSession();
    const client = new KnowledgeClient(session);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    fetchMock.mockResolvedValueOnce(response({ detail: "private-path" }, 500));
    await expect(client.getSummary()).rejects.toThrow("本地知识服务暂不可用");
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: vi.fn().mockRejectedValue(new Error("raw-json-error")),
    });
    await expect(client.getSummary()).rejects.toThrow("本地知识服务暂不可用");
    fetchMock.mockResolvedValueOnce(response({ ...validSummary, extra: token }));
    await expect(client.getSummary()).rejects.toThrow("本地知识服务暂不可用");
  });

  it("aborts after five seconds and always clears its timeout", async () => {
    const session = await validatedSession();
    vi.useFakeTimers();
    const clearTimeoutSpy = vi.spyOn(globalThis, "clearTimeout");
    const fetchMock = vi.fn((_url: string, init?: RequestInit) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted")));
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = new KnowledgeClient(session);

    const pending = client.getSummary();
    const rejection = expect(pending).rejects.toThrow("本地知识服务暂不可用");
    await vi.advanceTimersByTimeAsync(5_000);

    await rejection;
    expect(clearTimeoutSpy).toHaveBeenCalled();
  });
});
