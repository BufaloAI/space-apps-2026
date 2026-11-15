export const api = (import.meta.env.VITE_API_BASE_URL || "/api").replace(
  /\/$/,
  "",
);
export async function request(path: string, method = "GET", body?: object) {
  const res = await fetch(api + path, {
    method,
    cache: "no-store",
    signal: AbortSignal.timeout(12000),
    ...(body
      ? {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : {}),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))).detail;
    const messages: Record<string, string> = {
      busy: "Wait for the current job to finish.",
      no_analysis: "Analyze a field first.",
      queued: "Your session is queued.",
      ended: "Your session ended.",
      "Poligon kendini kesiyor; sınırları kesişmeyecek şekilde çizin.":
        "The boundary crosses itself. Move the intersecting corners.",
      "Parsel çok küçük.": "The field must cover at least 0.01 ha.",
    };
    const message =
      typeof detail === "string"
        ? messages[detail] || detail
        : messages[detail?.status] ||
          (res.status === 422
            ? "Check the boundary, date or question."
            : "Request failed (" + res.status + ").");
    throw Object.assign(new Error(message), { status: res.status, detail });
  }
  return res.json();
}
