export interface ClimateDay {
  date: string
  temperature: number
  rainfall: number
  humidity: number
  solar: number
  moisture: number
}
export interface ClimateService {
  getDailySeries(year: number, fieldId?: string): ClimateDay[]
}
export type Metric = Exclude<keyof ClimateDay, 'date'>
export const metrics: Record<Metric, [string, string, string]> = {
  temperature: ['Sıcaklık', 'Temperature', '°C'],
  rainfall: ['Yağış', 'Rainfall', 'mm'],
  humidity: ['Bağıl nem', 'Relative humidity', '%'],
  solar: ['Güneş radyasyonu', 'Solar radiation', 'MJ/m²/gün'],
  moisture: ['Toprak nemi', 'Soil moisture', 'm³/m³'],
}
export const demonstrationClimate: ClimateService = {
  getDailySeries(year, fieldId = 'global') {
    let seed = Array.from(fieldId).reduce(
      (sum, c) => sum + c.charCodeAt(0),
      year * 331,
    )
    const random = () => {
      seed = (seed * 16807) % 2147483647
      return seed / 2147483647
    }
    const rows: ClimateDay[] = []
    for (
      let dt = Date.UTC(year, 0, 1);
      dt < Date.UTC(year + 1, 0, 1);
      dt += 86400000
    ) {
      const seasonal = Math.sin(((rows.length - 85) * 2 * Math.PI) / 365)
      const wet = random() > 0.72 + seasonal * 0.08
      rows.push({
        date: new Date(dt).toISOString().slice(0, 10),
        temperature: 14 + 12 * seasonal + (random() - 0.5) * 7,
        rainfall: wet ? 1 + random() * 14 : 0,
        humidity: 63 - 11 * seasonal + random() * 12,
        solar: 14 + 8 * seasonal + random() * 3,
        moisture: 0.24 - seasonal * 0.07 + random() * 0.04,
      })
    }
    return rows
  },
}
export function aggregate(rows: ClimateDay[], key: Metric) {
  const total = rows.reduce((s, r) => s + r[key], 0)
  return key === 'rainfall' ? total : total / rows.length
}
export function indicators(
  rows: ClimateDay[],
  base: number,
  threshold: number,
) {
  let dry = 0,
    longest = 0,
    gdd = 0,
    hot = 0
  for (const r of rows) {
    dry = r.rainfall < 1 ? dry + 1 : 0
    longest = Math.max(dry, longest)
    gdd += Math.max(0, r.temperature - base)
    if (r.temperature >= threshold) hot++
  }
  return { longest, gdd, hot }
}
