import { vertices } from "./map.js";

export function initIntro(map) {
  const frame = document.querySelector("#compare");
  const range = document.querySelector("#split");
  const points = vertices.map(([x, y]) => [x * 1600, y * 1024]);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 1600 1024");
  svg.setAttribute("preserveAspectRatio", "xMidYMid slice");
  svg.setAttribute("aria-hidden", "true");
  const polygon = document.createElementNS(svg.namespaceURI, "polygon");
  polygon.setAttribute(
    "points",
    points.map((pair) => pair.join(",")).join(" "),
  );
  svg.append(polygon);
  for (const [x, y] of points) {
    const square = document.createElementNS(svg.namespaceURI, "rect");
    square.setAttribute("x", x - 4);
    square.setAttribute("y", y - 4);
    square.setAttribute("width", 8);
    square.setAttribute("height", 8);
    svg.append(square);
  }
  document.querySelector("#outline").append(svg);
  function update(value) {
    range.value = String(value);
    frame.style.setProperty("--split", range.value + "%");
    range.setAttribute("aria-valuetext", range.value + "% field sketch");
  }
  let animation;
  const stop = () => cancelAnimationFrame(animation);
  range.oninput = () => {
    stop();
    update(range.value);
  };
  range.onkeydown = stop;
  // Track the full plate, with no offset jump when grabbing the visible divider.
  let offset = 0;
  function drag(event) {
    const rect = frame.getBoundingClientRect();
    update(
      Math.min(
        100,
        Math.max(0, ((event.clientX - rect.left - offset) / rect.width) * 100),
      ),
    );
  }
  range.onpointerdown = (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    stop();
    range.focus({ preventScroll: true });
    range.setPointerCapture(event.pointerId);
    const rect = frame.getBoundingClientRect();
    const distance =
      event.clientX - rect.left - (rect.width * Number(range.value)) / 100;
    offset = Math.abs(distance) < 25 ? distance : 0;
    drag(event);
  };
  range.onpointermove = (event) => {
    if (range.hasPointerCapture(event.pointerId)) drag(event);
  };
  if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
    let start;
    function reveal(now) {
      start ??= now;
      const t = Math.min(1, (now - start) / 850);
      update(44 + 50 * (1 - t) ** 3);
      if (t < 1) animation = requestAnimationFrame(reveal);
    }
    animation = requestAnimationFrame(reveal);
  } else update(44);
  const workspace = document.querySelector("#workspace-title");
  document.querySelector("#open-field").onclick = (event) => {
    if (event.currentTarget.getAttribute("aria-disabled") === "true") {
      event.preventDefault();
      return;
    }
    map.sample();
    workspace.focus({ preventScroll: true });
  };
  document.querySelector("#draw-field").onclick = () => {
    map.beginDraw();
    location.hash = "workspace";
    workspace.focus({ preventScroll: true });
  };
}
