import { opaqueIdSchema } from "../domain/helper-contracts-schema";
import type { VerifiedHelperSession } from "./helper-session";
import { SAFE_HELPER_FAILURE_MESSAGE } from "./knowledge-client";

const ARTIFACT_MAX_BYTES = 32 * 1024 * 1024;
const ARTIFACT_TIMEOUT_MS = 45_000;
const allowedMediaTypes = new Set([
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/svg+xml",
]);

export interface LoadedArtifact {
  readonly artifactId: string;
  readonly mediaType: "image/png" | "image/jpeg" | "image/webp" | "image/svg+xml";
  readonly byteSize: number;
  readonly objectUrl: string;
}

export interface ProjectionArtifactReader {
  fork(): ProjectionArtifactReader;
  fetchArtifact(
    artifactId: string,
    externalSignal?: AbortSignal,
  ): Promise<LoadedArtifact>;
  dispose(): void;
}

export class ArtifactClient implements ProjectionArtifactReader {
  readonly #session: VerifiedHelperSession;
  readonly #helperOrigin: string;
  readonly #sessionToken: string;
  #current: LoadedArtifact | undefined;

  constructor(session: VerifiedHelperSession) {
    this.#session = session;
    this.#helperOrigin = session.helperOrigin;
    this.#sessionToken = session.sessionToken;
  }

  get current(): LoadedArtifact | undefined {
    return this.#current;
  }

  fork(): ArtifactClient {
    return new ArtifactClient(this.#session);
  }

  async fetchArtifact(
    artifactId: string,
    externalSignal?: AbortSignal,
  ): Promise<LoadedArtifact> {
    let verifiedArtifactId: string;
    try {
      verifiedArtifactId = opaqueIdSchema.parse(artifactId);
    } catch {
      throw new Error(SAFE_HELPER_FAILURE_MESSAGE);
    }

    const controller = new AbortController();
    const abortFromCaller = () => controller.abort();
    externalSignal?.addEventListener("abort", abortFromCaller, { once: true });
    if (externalSignal?.aborted) {
      controller.abort();
    }
    const timeout = globalThis.setTimeout(
      () => controller.abort(),
      ARTIFACT_TIMEOUT_MS,
    );

    try {
      const response = await fetch(
        `${this.#helperOrigin}/v1/artifacts/${verifiedArtifactId}`,
        {
          method: "GET",
          credentials: "omit",
          headers: {
            Accept: "image/png, image/jpeg, image/webp, image/svg+xml",
            "X-Course-Session": this.#sessionToken,
          },
          signal: controller.signal,
        },
      );
      if (!response.ok) {
        throw new Error(SAFE_HELPER_FAILURE_MESSAGE);
      }
      const mediaType = response.headers.get("Content-Type");
      const rawLength = response.headers.get("Content-Length");
      if (
        mediaType === null ||
        !allowedMediaTypes.has(mediaType) ||
        rawLength === null ||
        !/^[1-9][0-9]*$/.test(rawLength)
      ) {
        throw new Error(SAFE_HELPER_FAILURE_MESSAGE);
      }
      const expectedLength = Number(rawLength);
      if (!Number.isSafeInteger(expectedLength) || expectedLength > ARTIFACT_MAX_BYTES) {
        throw new Error(SAFE_HELPER_FAILURE_MESSAGE);
      }
      const bytes = await response.arrayBuffer();
      if (bytes.byteLength !== expectedLength || bytes.byteLength > ARTIFACT_MAX_BYTES) {
        throw new Error(SAFE_HELPER_FAILURE_MESSAGE);
      }
      const blob = new Blob([bytes], { type: mediaType });
      const objectUrl = URL.createObjectURL(blob);
      const loaded: LoadedArtifact = {
        artifactId: verifiedArtifactId,
        mediaType: mediaType as LoadedArtifact["mediaType"],
        byteSize: bytes.byteLength,
        objectUrl,
      };
      const previous = this.#current;
      this.#current = loaded;
      if (previous !== undefined) {
        URL.revokeObjectURL(previous.objectUrl);
      }
      return loaded;
    } catch {
      throw new Error(SAFE_HELPER_FAILURE_MESSAGE);
    } finally {
      globalThis.clearTimeout(timeout);
      externalSignal?.removeEventListener("abort", abortFromCaller);
    }
  }

  dispose(): void {
    if (this.#current !== undefined) {
      URL.revokeObjectURL(this.#current.objectUrl);
      this.#current = undefined;
    }
  }
}
