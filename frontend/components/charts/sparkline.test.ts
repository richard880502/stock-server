import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Sparkline } from "./sparkline";

const render = (values: number[]) => renderToStaticMarkup(createElement(Sparkline, { values, label: "近 20 日收盤走勢" }));

describe("Sparkline", () => {
  it("colours a rising series as up (red) and keeps the accessible label", () => {
    const html = render([10, 11, 12]);
    expect(html).toContain("tone-up");
    expect(html).toContain('aria-label="近 20 日收盤走勢"');
    expect(html).toContain('role="img"');
  });

  it("colours a falling series as down and a flat one as flat", () => {
    expect(render([12, 11, 10])).toContain("tone-down");
    expect(render([10, 12, 10])).toContain("tone-flat");
  });

  it("draws one point per value inside the box", () => {
    const html = render([1, 5, 3, 2]);
    const points = html.match(/points="([^"]+)"/)?.[1].split(" ") ?? [];
    expect(points).toHaveLength(4);
    for (const point of points) {
      const [x, y] = point.split(",").map(Number);
      expect(x).toBeGreaterThanOrEqual(0);
      expect(x).toBeLessThanOrEqual(96);
      expect(y).toBeGreaterThanOrEqual(0);
      expect(y).toBeLessThanOrEqual(28);
    }
  });

  it("shows a dash when there is not enough data", () => {
    expect(render([10])).toContain("—");
    expect(render([])).not.toContain("<svg");
  });
});
