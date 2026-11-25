import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { LlmErrorNotice } from "./llm-error";

const render = (status: number | null, message: string) =>
  renderToStaticMarkup(createElement(LlmErrorNotice, { error: { status, message } }));

describe("LlmErrorNotice", () => {
  it("shows the reason the API explained instead of a generic outage", () => {
    const html = render(502, "模型回傳的內容不符合預期格式（key_evidence：List should have at most 8 items），請再試一次");
    expect(html).toContain("模型回傳的內容不符合預期格式");
    expect(html).not.toContain("模型服務暫時無法回應");
  });

  it("summarises a raw upstream error as a transient outage, details folded away", () => {
    const html = render(502, "Server error '503 Service Unavailable' for url 'http://modelproxy/v1'");
    expect(html).toContain("模型服務暫時無法回應");
    expect(html).toContain("技術細節");
  });

  it("shows client-side errors as they are", () => {
    expect(render(422, "invalid symbol")).toContain("invalid symbol");
  });
});
