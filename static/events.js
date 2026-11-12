// SSE przez POST/fetch: bez automatycznego ponawiania kosztownego pytania.
export async function readEventReply(response, onProgress, signal) {
  if (!response.body?.getReader) throw new Error("Przeglądarka nie obsługuje odbioru postępu.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  const abort = () => { reader.cancel().catch(() => {}); };
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) abort();
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      if (buffer.length > 2_000_000) throw new Error("Zdarzenie odpowiedzi jest zbyt duże.");
      let boundary;
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        const frame = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        let kind = "message";
        const data = [];
        for (const line of frame.split(/\r?\n/)) {
          if (line.startsWith("event:")) kind = line.slice(6).trim();
          if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
        }
        if (!data.length) continue;
        let payload;
        try { payload = JSON.parse(data.join("\n")); }
        catch { throw new Error("Otrzymano niepoprawne zdarzenie odpowiedzi."); }
        if (kind === "progress") onProgress(payload);
        if (kind === "result") return payload;
        if (kind === "error") {
          const detail = payload?.error?.message;
          throw new Error(typeof detail === "string" ? detail.slice(0, 1000)
            : "Nie udało się uzyskać odpowiedzi. Spróbuj ponownie.");
        }
      }
      if (done) throw new Error("Przerwano połączenie przed otrzymaniem pełnej odpowiedzi. Spróbuj ponownie.");
    }
  } finally {
    signal?.removeEventListener("abort", abort);
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
