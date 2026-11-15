export function geometry(points) {
  if (points.length < 3) return null;
  const [x0, y0] = [points[0].lng, points[0].lat];
  let crossSum = 0,
    xSum = 0,
    ySum = 0;
  for (let i = 0; i < points.length; i++) {
    const a = points[i],
      b = points[(i + 1) % points.length];
    const x = a.lng - x0,
      y = a.lat - y0,
      u = b.lng - x0,
      v = b.lat - y0,
      c = x * v - u * y;
    crossSum += c;
    xSum += (x + u) * c;
    ySum += (y + v) * c;
  }
  if (Math.abs(crossSum) < 1e-14) return null;
  const lat = y0 + ySum / (3 * crossSum),
    lon = x0 + xSum / (3 * crossSum);
  return {
    lat,
    lon,
    area:
      (Math.abs(crossSum) * 111320 * 110540 * Math.cos((lat * Math.PI) / 180)) /
      20000,
  };
}
