// Shared SSE decoder for browser responses and provider streams.
export async function* readSSE(body) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  function parse(frame) {
    const data = []; let event = 'message';
    for (const line of frame.split(/\r?\n/)) {
      if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
      if (line.startsWith('event:')) event = line.slice(6).trim();
    }
    return data.length ? { event, data: data.join('\n') } : null;
  }
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let boundary;
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        const item = parse(buffer.slice(0, boundary.index));
        buffer = buffer.slice(boundary.index + boundary[0].length);
        if (item) yield item;
      }
      if (buffer.length > 8_000_000) throw new Error('流事件超过大小限制。');
      if (done) { const tail = parse(buffer); if (tail) yield tail; break; }
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}
