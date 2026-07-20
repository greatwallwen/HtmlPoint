import { describe, expect, it } from "vitest";

import fixture from "../../../contracts/projection/v1/fixtures/detect-displays.json";
import { projectionCommandSchema } from "./projection-schema";

describe("projectionCommandSchema", () => {
  it("round-trips the canonical detect fixture and rejects extras", () => {
    expect(projectionCommandSchema.parse(fixture)).toEqual(fixture);
    for (const unsafeField of [
      "sourcePath",
      "url",
      "token",
      "hwnd",
      "executablePath",
      "courseBody",
    ]) {
      expect(() =>
        projectionCommandSchema.parse({ ...fixture, [unsafeField]: "unsafe" }),
      ).toThrow();
    }
  });

  it.each([
    ["schemaVersion", 2],
    ["commandId", "not-a-uuid"],
    ["expectedGeneration", -1],
    ["command", "run_shell"],
  ])("rejects invalid %s", (field, value) => {
    expect(() =>
      projectionCommandSchema.parse({ ...fixture, [field]: value }),
    ).toThrow();
  });
});
