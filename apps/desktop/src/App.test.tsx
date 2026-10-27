import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import App from "./App";

vi.mock("./api/client", () => ({
  apiFetch: vi.fn(async (path: string) => {
    const json = (data: unknown) => ({
      ok: true,
      status: 200,
      json: async () => data,
    });
    if (path === "/health") {
      return json({ ok: true, version: "0.1.0" });
    }
    if (path === "/providers") {
      return json([]);
    }
    if (path === "/gateway/config") {
      return json({
        enabled: false,
        rag_mode: "auto",
        provider_id: "",
        mcp_enabled: true,
        token: "gateway-token",
      });
    }
    return json([]);
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
    expect(await screen.findByText("Provider 未配置")).toBeInTheDocument();
    expect(await screen.findByText("网关已关闭")).toBeInTheDocument();
    expect(screen.queryByText("Ollama 未启动")).not.toBeInTheDocument();
  });
});
