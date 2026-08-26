'use client';

interface WeatherData { condition?: string; forecast?: { day: string; text: string; temp: string }[] }
interface TrafficData { status_code?: string; description?: string; advice?: string }
interface WeatherAdvice { condition?: string; advice?: { label?: string; level?: string; advice?: string; prefer_indoor?: boolean }; indoor_alternatives?: { name: string; cost?: string; rating?: string; address?: string }[] }
interface CrowdNode { location?: string; name?: string; crowdedness?: { label?: string; level?: string; percent?: number } }

export default function WeatherCrowdPanel({
  weather,
  traffic,
  advice,
  route,
}: {
  weather?: WeatherData;
  traffic?: TrafficData;
  advice?: WeatherAdvice;
  route?: CrowdNode[];
}) {
  const crowdLevelCls: Record<string, string> = { low: 'bg-emerald-100 text-emerald-700', medium: 'bg-amber-100 text-amber-700', high: 'bg-rose-100 text-rose-700' };

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <h3 className="mb-4 text-lg font-semibold text-slate-800">实时天气 · 拥挤度</h3>

      {advice && (
        <div className="mb-4 rounded-xl border border-sky-200 bg-sky-50 p-4">
          <div className="flex items-center gap-2">
            <span className="text-2xl">{advice.advice?.prefer_indoor ? '🌧️' : advice.advice?.level === 'hot' ? '☀️' : '🌤️'}</span>
            <span className="font-semibold text-sky-800">{advice.condition || weather?.condition}</span>
            <span className="rounded-full bg-sky-200 px-2 py-0.5 text-xs text-sky-700">{advice.advice?.label}</span>
          </div>
          <p className="mt-2 text-sm text-sky-700">{advice.advice?.advice}</p>
        </div>
      )}

      {/* 天气预报 */}
      {weather?.forecast && weather.forecast.length > 0 && (
        <div className="mb-4 flex gap-2">
          {weather.forecast.slice(0, 3).map((f) => (
            <div key={f.day} className="flex-1 rounded-lg bg-slate-50 p-3 text-center">
              <p className="text-xs text-slate-500">{f.day}</p>
              <p className="text-sm font-medium text-slate-700">{f.text}</p>
              <p className="text-xs text-slate-500">{f.temp}</p>
            </div>
          ))}
        </div>
      )}

      {/* 实时路况 */}
      {traffic && (
        <div className="mb-4 flex items-center justify-between rounded-lg bg-slate-50 p-3">
          <span className="text-sm text-slate-600">实时路况：{traffic.description}</span>
          <span className="text-xs text-slate-500">{traffic.advice}</span>
        </div>
      )}

      {/* 雨天室内备选 */}
      {advice?.indoor_alternatives && advice.indoor_alternatives.length > 0 && (
        <div className="mb-4">
          <p className="mb-2 text-sm font-medium text-slate-700">🎪 雨天室内备选方案</p>
          <div className="grid grid-cols-2 gap-2">
            {advice.indoor_alternatives.slice(0, 6).map((p) => (
              <div key={p.name} className="rounded-lg border border-slate-200 p-2 text-xs">
                <p className="font-medium text-slate-700">{p.name}</p>
                <p className="text-slate-500">{p.cost ? `¥${p.cost}` : ''} {p.rating ? `★${p.rating}` : ''}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 景点拥挤度可视化 */}
      {route && route.length > 0 && (
        <div>
          <p className="mb-2 text-sm font-medium text-slate-700">👥 景点拥挤度</p>
          <div className="space-y-2">
            {route
              .filter((n) => n.crowdedness)
              .slice(0, 8)
              .map((n, i) => (
                <div key={i} className="flex items-center justify-between">
                  <span className="text-sm text-slate-600">{n.location || n.name}</span>
                  <div className="flex items-center gap-2">
                    <div className="h-2 w-24 rounded-full bg-slate-200">
                      <div
                        className={`h-full rounded-full ${n.crowdedness!.level === 'high' ? 'bg-rose-500' : n.crowdedness!.level === 'medium' ? 'bg-amber-500' : 'bg-emerald-500'}`}
                        style={{ width: `${n.crowdedness!.percent || 0}%` }}
                      />
                    </div>
                    <span className={`rounded-full px-2 py-0.5 text-xs ${crowdLevelCls[n.crowdedness!.level || 'low']}`}>
                      {n.crowdedness!.label}
                    </span>
                  </div>
                </div>
              ))}
          </div>
        </div>
      )}
    </div>
  );
}