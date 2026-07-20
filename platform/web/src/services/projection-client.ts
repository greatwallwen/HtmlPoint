import { z } from "zod";

import {
  jobResponseSchema,
} from "../domain/helper-contracts-schema";
import {
  projectionReceiptSchema,
  type ProjectionCommand,
  type ProjectionReceipt,
} from "../domain/projection-schema";
import type { VerifiedHelperSession } from "./helper-session";

const JOBS_PATH = "/v1/jobs";
const COMMAND_TIMEOUT_MS = 125_000;
const opaqueIdSchema = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/);
const uuidSchema = z.string().uuid();
const generationSchema = z.number().int().min(0).max(2_147_483_647);

const successSchema = jobResponseSchema(
  z.object({ receipt: projectionReceiptSchema }).strict(),
);

const failureSchema = jobResponseSchema(
  z
    .object({
      reasonCode: z.enum([
        "projection_unavailable",
        "projection_timeout",
        "projection_content_unavailable",
        "command_replay_conflict",
        "projection_command_failed",
      ]),
      status: z.literal("failed"),
    })
    .strict(),
);

export interface ProjectionCommandInput {
  commandId: string;
  sessionId: string | null;
  expectedGeneration: number;
}

export type DetectProjectionInput = ProjectionCommandInput & { sessionId: null };

export interface OpenProjectionInput extends ProjectionCommandInput {
  sessionId: string;
  courseVersionId: string;
  slideDeckId: string;
  runtimeManifestId: string;
}

export type AssignProjectionInput = ProjectionCommandInput & {
  sessionId: string;
  swap: boolean;
};
export type FullscreenProjectionInput = ProjectionCommandInput & { sessionId: string };
export type VerifyProjectionInput = ProjectionCommandInput & { sessionId: string };
export type CloseProjectionInput = ProjectionCommandInput & { sessionId: string };

export interface ProjectionClient {
  detect(input: DetectProjectionInput): Promise<ProjectionReceipt>;
  open(input: OpenProjectionInput): Promise<ProjectionReceipt>;
  assign(input: AssignProjectionInput): Promise<ProjectionReceipt>;
  fullscreen(input: FullscreenProjectionInput): Promise<ProjectionReceipt>;
  verify(input: VerifyProjectionInput): Promise<ProjectionReceipt>;
  close(input: CloseProjectionInput): Promise<ProjectionReceipt>;
}

export type ProjectionClientErrorCode =
  | "projection_unavailable"
  | "projection_timeout"
  | "projection_content_unavailable"
  | "command_replay_conflict"
  | "projection_command_failed"
  | "invalid_response";

export class ProjectionClientError extends Error {
  constructor(
    readonly code: ProjectionClientErrorCode,
    readonly retryable: boolean,
  ) {
    super("双屏服务请求未完成");
    this.name = "ProjectionClientError";
  }
}

type ProjectionFetch = (url: string, init: RequestInit) => Promise<Response>;

interface ProjectionJobBody {
  type: string;
  commandId: string;
  sessionId: string | null;
  expectedGeneration: number;
  payload: Record<string, unknown>;
}

const commandForJob = (type: string): ProjectionCommand["command"] => {
  const mapping = {
    projection_detect_displays: "detect_displays",
    projection_open_session: "open_projection_session",
    projection_assign_window: "assign_projection_window",
    projection_enter_fullscreen: "enter_projection_fullscreen",
    projection_verify_assignment: "verify_projection_assignment",
    projection_close_session: "close_projection_session",
  } as const;
  const command = mapping[type as keyof typeof mapping];
  if (command === undefined) throw new ProjectionClientError("invalid_response", false);
  return command;
};

const validateBase = (input: ProjectionCommandInput): void => {
  if (
    !uuidSchema.safeParse(input.commandId).success ||
    (input.sessionId !== null && !uuidSchema.safeParse(input.sessionId).success) ||
    !generationSchema.safeParse(input.expectedGeneration).success
  ) {
    throw new ProjectionClientError("invalid_response", false);
  }
};

export class HelperProjectionClient implements ProjectionClient {
  readonly #helperOrigin: string;
  readonly #sessionToken: string;
  readonly #fetcher: ProjectionFetch;

  constructor(
    session: VerifiedHelperSession,
    fetcher: ProjectionFetch = (url, init) => fetch(url, init),
  ) {
    this.#helperOrigin = session.helperOrigin;
    this.#sessionToken = session.sessionToken;
    this.#fetcher = fetcher;
  }

  detect(input: DetectProjectionInput): Promise<ProjectionReceipt> {
    return this.#submit({
      type: "projection_detect_displays",
      ...input,
      payload: {},
    });
  }

  open(input: OpenProjectionInput): Promise<ProjectionReceipt> {
    for (const value of [
      input.courseVersionId,
      input.slideDeckId,
      input.runtimeManifestId,
    ]) {
      if (!opaqueIdSchema.safeParse(value).success) {
        return Promise.reject(new ProjectionClientError("invalid_response", false));
      }
    }
    return this.#submit({
      type: "projection_open_session",
      commandId: input.commandId,
      sessionId: input.sessionId,
      expectedGeneration: input.expectedGeneration,
      payload: {
        courseVersionId: input.courseVersionId,
        slideDeckId: input.slideDeckId,
        runtimeManifestId: input.runtimeManifestId,
      },
    });
  }

  assign(input: AssignProjectionInput): Promise<ProjectionReceipt> {
    return this.#submit({
      type: "projection_assign_window",
      commandId: input.commandId,
      sessionId: input.sessionId,
      expectedGeneration: input.expectedGeneration,
      payload: { swap: input.swap },
    });
  }

  fullscreen(input: FullscreenProjectionInput): Promise<ProjectionReceipt> {
    return this.#submit({
      type: "projection_enter_fullscreen",
      ...input,
      payload: {},
    });
  }

  verify(input: VerifyProjectionInput): Promise<ProjectionReceipt> {
    return this.#submit({
      type: "projection_verify_assignment",
      ...input,
      payload: {},
    });
  }

  close(input: CloseProjectionInput): Promise<ProjectionReceipt> {
    return this.#submit({
      type: "projection_close_session",
      ...input,
      payload: {},
    });
  }

  async #submit(body: ProjectionJobBody): Promise<ProjectionReceipt> {
    validateBase(body);
    const controller = new AbortController();
    const timeout = globalThis.setTimeout(() => controller.abort(), COMMAND_TIMEOUT_MS);
    try {
      const response = await this.#fetcher(`${this.#helperOrigin}${JOBS_PATH}`, {
        method: "POST",
        credentials: "omit",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Course-Session": this.#sessionToken,
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      let value: unknown;
      try {
        value = await response.json();
      } catch {
        throw new ProjectionClientError("invalid_response", false);
      }
      const success = successSchema.safeParse(value);
      if (success.success) {
        const receipt = success.data.result.receipt;
        const expectedCommand = commandForJob(body.type);
        const expectedGeneration =
          receipt.accepted && expectedCommand === "assign_projection_window"
            ? body.expectedGeneration + 1
            : body.expectedGeneration;
        if (
          receipt.commandId !== body.commandId ||
          receipt.sessionId !== body.sessionId ||
          receipt.command !== expectedCommand ||
          (receipt.accepted ? !response.ok : response.status !== 409) ||
          (receipt.accepted && receipt.generation !== expectedGeneration)
        ) {
          throw new ProjectionClientError("invalid_response", false);
        }
        return receipt;
      }
      const failure = failureSchema.safeParse(value);
      if (failure.success && !response.ok) {
        const code = failure.data.result.reasonCode;
        throw new ProjectionClientError(
          code,
          code === "projection_unavailable" || code === "projection_timeout",
        );
      }
      throw new ProjectionClientError("invalid_response", false);
    } catch (error) {
      if (error instanceof ProjectionClientError) throw error;
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new ProjectionClientError("projection_timeout", true);
      }
      throw new ProjectionClientError("projection_unavailable", true);
    } finally {
      globalThis.clearTimeout(timeout);
    }
  }
}
