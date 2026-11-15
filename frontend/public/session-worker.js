const pending = new Map();
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) =>
  event.waitUntil(self.clients.claim()),
);
self.addEventListener("message", (event) => {
  const { resume, leave, url } = event.data || {};
  if (resume) {
    pending.get(resume)?.();
    return;
  }
  if (!leave) return;
  const endpoint = new URL(url);
  if (
    !["http:", "https:"].includes(endpoint.protocol) ||
    !endpoint.pathname.endsWith(
      "/session/" + encodeURIComponent(leave) + "/leave",
    )
  )
    return;
  pending.get(leave)?.();
  event.waitUntil(
    new Promise((resolve) => {
      const timer = setTimeout(async () => {
        pending.delete(leave);
        try {
          await fetch(endpoint, { method: "POST", keepalive: true });
        } catch {}
        resolve();
      }, 4000);
      pending.set(leave, () => {
        clearTimeout(timer);
        pending.delete(leave);
        resolve();
      });
    }),
  );
});
