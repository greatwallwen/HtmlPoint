// @ts-expect-error -- node:fs is available to the Vitest runtime.
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const tokensCss = readFileSync("src/app/tokens.css", "utf8");
const appCss = readFileSync("src/app/app.css", "utf8");
const indexHtml = readFileSync("index.html", "utf8");
const css = `${tokensCss}\n${appCss}`;

const requiredTokens = [
  ["--color-page", "#f7f8fa"],
  ["--color-surface", "#ffffff"],
  ["--color-surface-muted", "#f4f6f8"],
  ["--color-text", "#172033"],
  ["--color-brand", "#1463ff"],
] as const;

const userFacingComponents = [
  "src/components/AssistantDock.tsx",
  "src/components/ChapterTree.tsx",
  "src/components/CourseEditor.tsx",
  "src/components/GenerateStep.tsx",
  "src/components/ImportStep.tsx",
  "src/components/LessonList.tsx",
  "src/components/PresenterView.tsx",
  "src/components/SourcePanel.tsx",
  "src/components/StageView.tsx",
  "src/components/TeachingSetup.tsx",
  "src/components/ValidationPanel.tsx",
  "src/components/WorkflowHeader.tsx",
];

interface CssBlock {
  selectors: string;
  declarations: string;
}

const darkHex = /#(?:000(?:000)?|111827|0f172a|020617)\b/i;
const darkRgb =
  /rgba?\(\s*(?:0%?\s*[, ]+\s*0%?\s*[, ]+\s*0%?|17\s*[, ]+\s*24\s*[, ]+\s*39|15\s*[, ]+\s*23\s*[, ]+\s*42|2\s*[, ]+\s*6\s*[, ]+\s*23)/i;

function withoutCssComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "");
}

function tokenValues(source: string, token: string): string[] {
  const escapedToken = token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const declarations = new RegExp(
    `(?:^|[;{])\\s*${escapedToken}\\s*:\\s*([^;}]+)`,
    "gim",
  );
  return [...withoutCssComments(source).matchAll(declarations)].map((match) =>
    match[1].trim().toLowerCase(),
  );
}

function tokenIsExact(source: string, token: string, expected: string): boolean {
  const values = tokenValues(source, token);
  return values.length > 0 && values.every((value) => value === expected);
}

function cssBlocks(source: string): CssBlock[] {
  const withoutComments = withoutCssComments(source);
  return [...withoutComments.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(
    ([, selectors, declarations]) => ({ selectors, declarations }),
  );
}

function hasSelector(block: CssBlock, selector: string): boolean {
  return block.selectors
    .split(",")
    .map((candidate) => candidate.trim())
    .includes(selector);
}

function darkProjectionSurfaces(source: string): string[] {
  const violations: string[] = [];
  for (const block of cssBlocks(source)) {
    if (!/\.(?:stage|presenter)/i.test(block.selectors)) {
      continue;
    }
    const values = [
      ...block.declarations.matchAll(
        /(?:background(?:-color)?|fill)\s*:\s*([^;]+)/gi,
      ),
    ].map((match) => match[1]);
    for (const value of values) {
      if (darkHex.test(value) || darkRgb.test(value)) {
        violations.push(`${block.selectors.trim()}: ${value.trim()}`);
      }
    }
  }
  return violations;
}

describe("course studio light-theme contract", () => {
  it("declares an explicit favicon so browsers do not emit a missing-resource error", () => {
    expect(indexHtml).toMatch(
      /<link\s+rel=["']icon["']\s+href=["']data:,["']\s*\/?\s*>/i,
    );
  });

  it.each(requiredTokens)("keeps %s at %s", (token, value) => {
    expect(tokenIsExact(tokensCss, token, value)).toBe(true);
  });

  it("rejects commented, prefixed, and conflicting token declarations", () => {
    const misleading = `
      /* :root { --color-page: #f7f8fa; } */
      :root { --fallback--color-page: #f7f8fa; }
    `;
    const conflicting = `
      :root { --color-page: #f7f8fa; --color-page: #000000; }
    `;

    expect(tokenIsExact(misleading, "--color-page", "#f7f8fa")).toBe(false);
    expect(tokenIsExact(conflicting, "--color-page", "#f7f8fa")).toBe(false);
  });

  it("contains no generated gradients", () => {
    const gradient = /(?:linear|radial|conic)-gradient/i;

    expect(withoutCssComments(css)).not.toMatch(gradient);
    expect(withoutCssComments("/* linear-gradient(white, blue) */")).not.toMatch(
      gradient,
    );
    expect(withoutCssComments(".card { background: LiNeAr-GrAdIeNt(a, b); }")).toMatch(
      gradient,
    );
  });

  it("keeps stage and presenter projections on light surfaces", () => {
    const blocks = cssBlocks(appCss);

    for (const selector of [".stage-view", ".presenter-view"]) {
      expect(
        blocks.some(
          (block) =>
            hasSelector(block, selector) &&
            /background\s*:\s*var\(--color-page\)/i.test(block.declarations),
        ),
        selector,
      ).toBe(true);
    }
    expect(darkProjectionSurfaces(appCss)).toEqual([]);
  });

  it.each([
    ".stage-view { background-color: #000; }",
    ".presenter-view { fill: #111827; }",
    ".stage-view { background: rgb(0 0 0); }",
    ".stage-view { background: rgb(0% 0% 0%); }",
    ".presenter-view { background: rgba(15, 23, 42, 0.8); }",
  ])("detects a forbidden projection fixture: %s", (fixture) => {
    expect(darkProjectionSurfaces(fixture)).toHaveLength(1);
  });

  it("retains visible focus and 44px pointer targets", () => {
    const blocks = cssBlocks(appCss);
    const focusBlock = blocks.find((block) =>
      hasSelector(block, "button:focus-visible"),
    )?.declarations;
    const buttonBlock = blocks.find(
      (block) =>
        hasSelector(block, "button") &&
        block.selectors.split(",").length === 1,
    )?.declarations;
    const iconButtonBlock = blocks.find((block) =>
      hasSelector(block, ".icon-button"),
    )?.declarations;

    expect(focusBlock).toMatch(/outline:\s*3px solid/);
    expect(buttonBlock).toMatch(/min-height:\s*44px/);
    expect(iconButtonBlock).toMatch(/(?:^|;)\s*width:\s*44px/);
    expect(iconButtonBlock).toMatch(/(?:^|;)\s*min-width:\s*44px/);
    expect(iconButtonBlock).toMatch(/(?:^|;)\s*height:\s*44px/);
  });

  it("uses component icons instead of emoji or text-glyph fallbacks", () => {
    const fallbackGlyph =
      /(?:\p{Extended_Pictographic}|[×✕✖➕➖←→▶◀⏮⏭⏯⏸⏹])/u;

    for (const componentPath of userFacingComponents) {
      expect(readFileSync(componentPath, "utf8"), componentPath).not.toMatch(
        fallbackGlyph,
      );
    }
  });
});
