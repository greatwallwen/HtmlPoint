import { describe, expect, it } from "vitest";

import {
  LEGACY_WORKSPACE_STORAGE_KEY,
  WORKSPACE_STORAGE_KEY,
  loadWorkspace,
  saveWorkspace,
  serializeWorkspaceV2,
  type PersistedWorkspaceV2,
} from "./storage";

class MapStorage implements Pick<Storage, "getItem" | "setItem" | "removeItem"> {
  readonly values = new Map<string, string>();
  readonly calls: string[] = [];

  getItem(key: string): string | null {
    this.calls.push(`get:${key}`);
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.calls.push(`set:${key}`);
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.calls.push(`remove:${key}`);
    this.values.delete(key);
  }
}

const snapshot = (
  patch: Partial<PersistedWorkspaceV2> = {},
): PersistedWorkspaceV2 => ({
  version: 2,
  governed: {
    requirementId: "requirement-1",
    outlineVersionId: "outline-v1",
    courseVersionId: "course-v1",
    slideDeckId: "deck-v1",
    runtimeManifestId: "runtime-v1",
    cardVersionIds: ["card-v1", "card-v2"],
    visualPlacementIds: ["visual-placement-1"],
  },
  view: {
    step: "edit",
    selectedChapterId: "chapter-1",
    selectedLessonId: "lesson-1",
  },
  savedAt: "2026-07-19T02:00:00.000Z",
  ...patch,
});

const legacyV1 = () => ({
  version: 1,
  step: "edit",
  course: {
    schemaVersion: 1,
    id: "legacy-course",
    title: "Secret course title",
    audience: "Product team",
    goal: "Private goal",
    durationMinutes: 90,
    chapters: [
      {
        id: "chapter-1",
        title: "Private chapter",
        objective: "Private objective",
        lessons: [
          {
            id: "lesson-1",
            title: "Private lesson",
            summary: "Private card body",
            durationMinutes: 30,
            sourceIds: ["source-1"],
            status: "grounded",
          },
        ],
      },
    ],
    sources: [
      {
        id: "source-1",
        name: "D:\\private\\course.md",
        kind: "markdown",
        size: 128,
        status: "ready",
        extractedText: "secret extracted text and https://private.example/token",
        addedAt: "2026-07-15T08:00:00.000Z",
      },
    ],
    updatedAt: "2026-07-15T08:30:00.000Z",
  },
  brief: {
    title: "Secret course title",
    audience: "Product team",
    goal: "Private goal",
    durationMinutes: 90,
  },
  receipts: [
    {
      id: "receipt-1",
      courseId: "legacy-course",
      kind: "validation",
      createdAt: "2026-07-15T08:45:00.000Z",
      inputDigest: "sha256:legacy-course",
      summary: "Helper payload",
      checks: [{ id: "check-1", level: "pass", message: "Private result" }],
    },
  ],
  activeValidationReceiptId: "receipt-1",
  selectedChapterId: "chapter-1",
  selectedLessonId: "lesson-1",
  savedAt: "2026-07-15T09:00:00.000Z",
});

describe("identifier-only workspace v2 storage", () => {
  it("returns empty without mutating storage when neither key exists", () => {
    const storage = new MapStorage();
    expect(loadWorkspace(storage)).toEqual({ status: "empty" });
    expect(storage.calls).toEqual([
      `get:${WORKSPACE_STORAGE_KEY}`,
      `get:${LEGACY_WORKSPACE_STORAGE_KEY}`,
    ]);
  });

  it("round-trips exact governed IDs and skips an identical rewrite", () => {
    const storage = new MapStorage();
    const value = snapshot();
    expect(saveWorkspace(storage, value)).toEqual({ status: "saved" });
    expect(loadWorkspace(storage)).toEqual({ status: "ready", snapshot: value });
    const writes = storage.calls.filter((call) => call === `set:${WORKSPACE_STORAGE_KEY}`).length;
    expect(saveWorkspace(storage, value)).toEqual({ status: "saved" });
    expect(storage.calls.filter((call) => call === `set:${WORKSPACE_STORAGE_KEY}`)).toHaveLength(writes);
  });

  it("serializes only the explicit whitelist and rejects extra payloads", () => {
    const serialized = serializeWorkspaceV2(snapshot());
    const lowered = serialized.toLowerCase();
    for (const forbidden of [
      "extractedtext",
      "chunkbody",
      "cardbody",
      "helperpayload",
      "https://",
      "http://",
      "base64",
      "token",
      "nonce",
      "artifactbytes",
      "d:\\\\",
    ]) {
      expect(lowered).not.toContain(forbidden);
    }
    expect(() =>
      serializeWorkspaceV2({ ...snapshot(), helperPayload: { token: "secret" } } as PersistedWorkspaceV2),
    ).toThrow();
  });

  it("migrates legacy content to counts only, validates v2, then deletes v1", () => {
    const storage = new MapStorage();
    storage.values.set(LEGACY_WORKSPACE_STORAGE_KEY, JSON.stringify(legacyV1()));

    const result = loadWorkspace(storage);
    expect(result.status).toBe("ready");
    if (result.status !== "ready") {
      throw new Error("migration should be ready");
    }
    expect(result.snapshot).toMatchObject({
      version: 2,
      governed: { cardVersionIds: [], visualPlacementIds: [] },
      view: {
        step: "edit",
        selectedChapterId: "chapter-1",
        selectedLessonId: "lesson-1",
      },
      legacyUnlinked: {
        status: "legacy-unlinked",
        sourceCount: 1,
        chapterCount: 1,
        lessonCount: 1,
        receiptCount: 1,
      },
    });
    const persisted = storage.values.get(WORKSPACE_STORAGE_KEY) ?? "";
    expect(persisted).toBe(serializeWorkspaceV2(result.snapshot));
    expect(persisted).not.toContain("secret extracted text");
    expect(persisted).not.toContain("Private lesson");
    expect(storage.values.has(LEGACY_WORKSPACE_STORAGE_KEY)).toBe(false);
    const setIndex = storage.calls.indexOf(`set:${WORKSPACE_STORAGE_KEY}`);
    const verifyIndex = storage.calls.lastIndexOf(`get:${WORKSPACE_STORAGE_KEY}`);
    const deleteIndex = storage.calls.indexOf(`remove:${LEGACY_WORKSPACE_STORAGE_KEY}`);
    expect(setIndex).toBeLessThan(verifyIndex);
    expect(verifyIndex).toBeLessThan(deleteIndex);
  });

  it("preserves v1 and rolls back v2 when migration write verification fails", () => {
    const values = new Map<string, string>([
      [LEGACY_WORKSPACE_STORAGE_KEY, JSON.stringify(legacyV1())],
    ]);
    const removed: string[] = [];
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string) => values.set(key, "corrupted-after-write"),
      removeItem: (key: string) => {
        removed.push(key);
        values.delete(key);
      },
    };

    expect(loadWorkspace(storage)).toMatchObject({ status: "corrupt" });
    expect(values.has(LEGACY_WORKSPACE_STORAGE_KEY)).toBe(true);
    expect(values.has(WORKSPACE_STORAGE_KEY)).toBe(false);
    expect(removed).toEqual([WORKSPACE_STORAGE_KEY]);
  });

  it("preserves v1 when the migration write throws", () => {
    const legacy = JSON.stringify(legacyV1());
    const storage = {
      getItem: (key: string) =>
        key === LEGACY_WORKSPACE_STORAGE_KEY ? legacy : null,
      setItem: () => {
        throw new Error("quota exceeded");
      },
      removeItem: () => {
        throw new Error("must not delete legacy");
      },
    };
    expect(loadWorkspace(storage)).toMatchObject({ status: "corrupt" });
  });

  it("fails closed for corrupt v2 and invalid save input", () => {
    const storage = new MapStorage();
    storage.values.set(WORKSPACE_STORAGE_KEY, JSON.stringify({ ...snapshot(), version: 3 }));
    expect(loadWorkspace(storage)).toMatchObject({ status: "corrupt" });

    const invalid = { ...snapshot(), governed: { ...snapshot().governed, cardVersionIds: ["card-v1", "card-v1"] } };
    expect(saveWorkspace(storage, invalid)).toMatchObject({ status: "failed" });
  });
});
