import { useEffect, useState } from "react";
import { apiFetch, getServerInfo } from "./api/client";

async function waitForHealth(retries = 20): Promise<{ ollama: boolean }> {
  let lastError: unknown;
  for (let i = 0; i < retries; i += 1) {
    try {
      const response = await apiFetch("/health");
      if (response.ok) {
        return (await response.json()) as { ollama: boolean };
      }
      lastError = new Error(`health returned ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw lastError ?? new Error("health check failed");
}

export default function App() {
  const [status, setStatus] = useState("starting...");

  useEffect(() => {
    (async () => {
      try {
        await getServerInfo();
        const data = await waitForHealth();
        setStatus(`sidecar ok · ollama=${data.ollama}`);
      } catch (error) {
        setStatus(`error: ${String(error)}`);
      }
    })();
  }, []);

  return (
    <main style={{ padding: 24, fontFamily: "Segoe UI, sans-serif" }}>
      <h1>Local KB</h1>
      <p>{status}</p>
    </main>
  );
}
