export type SseEvent = { event: string; data: string };

/** Read a fetch Response body as Server-Sent Events, calling onEvent per block. */
export async function readSse(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: SseEvent) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const processBlock = (block: string) => {
    const lines = block.split("\n");
    const event = lines
      .find((line) => line.startsWith("event:"))
      ?.slice("event:".length)
      .trim();
    const dataLine = lines.find((line) => line.startsWith("data:"));
    if (!event || !dataLine) return;
    onEvent({ event, data: dataLine.slice("data:".length).trim() });
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer = (buffer + decoder.decode(value, { stream: !done })).replace(
      /\r\n/g,
      "\n",
    );
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      processBlock(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
}
