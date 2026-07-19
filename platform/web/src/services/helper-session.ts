import { z } from "zod";

export const HELPER_SESSION_STORAGE_KEY = "course-studio:helper-session:v1";

const urlSafeSecretSchema = z.string().regex(/^[A-Za-z0-9_-]{43,}$/);
const exchangeResponseSchema = z
  .object({ sessionToken: urlSafeSecretSchema })
  .strict();
const storedSessionSchema = z
  .object({
    helperOrigin: z.string(),
    sessionToken: urlSafeSecretSchema,
  })
  .strict();

declare const verifiedHelperSession: unique symbol;

export interface VerifiedHelperSession {
  readonly helperOrigin: string;
  readonly sessionToken: string;
  readonly [verifiedHelperSession]: true;
}

let bootstrapPromise: Promise<VerifiedHelperSession | undefined> | undefined;

type PreparedLaunchState =
  | { status: "unprepared" }
  | { status: "none" }
  | { status: "invalid" }
  | { status: "valid"; helperOrigin: string; nonce: string };

let preparedLaunchState: PreparedLaunchState = { status: "unprepared" };

function verifiedSession(
  helperOrigin: string,
  sessionToken: string,
): VerifiedHelperSession {
  return {
    helperOrigin,
    sessionToken,
  } as VerifiedHelperSession;
}

function parseHelperOrigin(value: string): string | undefined {
  const match = /^http:\/\/127\.0\.0\.1:([1-9]\d{0,4})$/.exec(value);
  if (match === null) {
    return undefined;
  }
  const port = Number(match[1]);
  return port <= 65_535 ? value : undefined;
}

function safeRemoveStoredSession(): void {
  try {
    window.sessionStorage.removeItem(HELPER_SESSION_STORAGE_KEY);
  } catch {
    // Storage denial is already a fail-closed state.
  }
}

function restoreStoredSession(): VerifiedHelperSession | undefined {
  try {
    const stored = window.sessionStorage.getItem(HELPER_SESSION_STORAGE_KEY);
    if (stored === null) {
      return undefined;
    }
    const parsed = storedSessionSchema.safeParse(JSON.parse(stored));
    const helperOrigin = parsed.success
      ? parseHelperOrigin(parsed.data.helperOrigin)
      : undefined;
    if (!parsed.success || helperOrigin === undefined) {
      safeRemoveStoredSession();
      return undefined;
    }
    return verifiedSession(helperOrigin, parsed.data.sessionToken);
  } catch {
    safeRemoveStoredSession();
    return undefined;
  }
}

function captureAndClearFragment(): string {
  const fragment = window.location.hash;
  if (fragment !== "") {
    window.history.replaceState(
      window.history.state,
      "",
      `${window.location.pathname}${window.location.search}`,
    );
  }
  return fragment;
}

function parseLaunchMaterial(
  fragment: string,
): { helperOrigin: string; nonce: string } | undefined {
  if (!fragment.startsWith("#")) {
    return undefined;
  }
  const entries = Array.from(new URLSearchParams(fragment.slice(1)).entries());
  if (
    entries.length !== 2 ||
    entries.filter(([key]) => key === "helper").length !== 1 ||
    entries.filter(([key]) => key === "nonce").length !== 1
  ) {
    return undefined;
  }
  const parameters = new Map(entries);
  const helperOrigin = parseHelperOrigin(parameters.get("helper") ?? "");
  const nonce = parameters.get("nonce") ?? "";
  return helperOrigin === undefined || !urlSafeSecretSchema.safeParse(nonce).success
    ? undefined
    : { helperOrigin, nonce };
}

export function prepareHelperSessionLaunch(): void {
  const fragment = captureAndClearFragment();
  if (preparedLaunchState.status !== "unprepared") {
    return;
  }
  if (fragment === "") {
    preparedLaunchState = { status: "none" };
    return;
  }
  const material = parseLaunchMaterial(fragment);
  preparedLaunchState =
    material === undefined
      ? { status: "invalid" }
      : { status: "valid", ...material };
}

async function bootstrapPreparedSession(
  launch: PreparedLaunchState,
): Promise<VerifiedHelperSession | undefined> {
  if (launch.status === "none") {
    return restoreStoredSession();
  }
  if (launch.status !== "valid") {
    safeRemoveStoredSession();
    return undefined;
  }

  try {
    const response = await fetch(
      `${launch.helperOrigin}/v1/session/exchange`,
      {
        method: "POST",
        credentials: "omit",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nonce: launch.nonce }),
      },
    );
    if (!response.ok) {
      safeRemoveStoredSession();
      return undefined;
    }
    const parsed = exchangeResponseSchema.safeParse(await response.json());
    if (!parsed.success) {
      safeRemoveStoredSession();
      return undefined;
    }
    const session = verifiedSession(
      launch.helperOrigin,
      parsed.data.sessionToken,
    );
    window.sessionStorage.setItem(
      HELPER_SESSION_STORAGE_KEY,
      JSON.stringify({
        helperOrigin: session.helperOrigin,
        sessionToken: session.sessionToken,
      }),
    );
    return session;
  } catch {
    safeRemoveStoredSession();
    return undefined;
  }
}

export function bootstrapHelperSession(): Promise<
  VerifiedHelperSession | undefined
> {
  prepareHelperSessionLaunch();
  if (bootstrapPromise !== undefined) {
    return bootstrapPromise;
  }
  bootstrapPromise = bootstrapPreparedSession(preparedLaunchState);
  return bootstrapPromise;
}

export function restoreHelperSessionForProjection(): VerifiedHelperSession | undefined {
  prepareHelperSessionLaunch();
  return preparedLaunchState.status === "none" ? restoreStoredSession() : undefined;
}

export function resetHelperSessionBootstrapForTests(): void {
  bootstrapPromise = undefined;
  preparedLaunchState = { status: "unprepared" };
}
