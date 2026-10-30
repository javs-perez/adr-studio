export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    try {
      const error = JSON.parse(text) as { detail?: unknown };
      detail = typeof error.detail === 'string' ? error.detail : JSON.stringify(error.detail);
    } catch {
      // A reverse proxy may return plain text instead of the API's JSON error format.
    }
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}
