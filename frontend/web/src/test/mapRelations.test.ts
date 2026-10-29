import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

describe("the map writes no text on its own", () => {
  const files = (dir: string): string[] => readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? (name === "test" ? [] : files(path)) : /\.tsx?$/.test(name) ? [path] : [];
  });
  it("has no caller of the card-words or line-note routes", () => {
    const source = files(join(__dirname, "..")).map((path) => readFileSync(path, "utf8")).join("\n");
    expect(source).not.toMatch(/\/api\/map-cards|\/api\/map-lines|useMapWords|useMapLines/);
  });
});
