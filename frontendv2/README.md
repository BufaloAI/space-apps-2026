# Field Atlas

An English field analysis workspace inspired by Vekt Motion. Drag the opening divider (or use its arrow keys) to compare the local Çumra satellite image with a sketch derived from the same image.

Start the FastAPI service following [the backend setup](../backend/README.md). Inside this directory run `npm ci` and `npm run dev`. Open http://localhost:5175. The map, comparison and boundary editing are also available with the analysis service offline.

Vite proxies `/api` to port 8000. Use `npm run build` for a static production build. In production proxy `/api` to FastAPI with SSE buffering disabled, or configure `VITE_API_BASE_URL` and the backend's CORS origins. Returned analyst names, replies and provider notes retain the server's language. The current ML provider returns demonstration measurements.

Satellite imagery: Esri and its imagery contributors. The sketch is a graphical treatment of the basemap, independent of the selected analysis date.
