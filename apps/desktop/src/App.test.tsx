import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import App from "./App";

vi.mock("./api/client", () => ({
  apiFetch: vi.fn(async (path: string) => {
    if (path === "/health") {
      return {
        ok: true,
        status: 200,
        json: async () => ({ ok: true, ollama: false, version: "0.1.0" }),
      };
    }
    return { ok: true, status: 200, json: async () => [] };
  }),
}));

describe("App", () => {
  it("renders the product name and the library route", async () => {
    render(<App />);

    expect(screen.getByText("Local KB")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "资料库" })).toBeInTheDocument();
    expect(
      await screen.findByText("还没有资料。选择一个文件夹开始导入。"),
    ).toBeInTheDocument();
    expect(await screen.findByText("Ollama 未启动")).toBeInTheDocument();
  });
});
