'use client';

import { useMemo, useState } from 'react';
import { Camera, CloudSun, Languages, MapPin, Radio, Users } from 'lucide-react';
import { apiJson } from '../lib/apiClient';

type Props = { tripId: string; destination?: string; routes?: any[] };

const phraseOptions = ['请问洗手间在哪里', '谢谢', '请帮帮我'];

const languageLabels: Record<string, string> = { en: '英语', ja: '日语' };

function localDate() {
  const now = new Date();
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

function sourceLabel(source: any) {
  if (!source) return '未提供';
  if (typeof source === 'string') return source;
  return String(source.provider || source.name || '未提供');
}

function errorText(error: unknown, fallback: string) {
  const code = String((error as any)?.code || '');
  if (code === 'TRIP_ID_REQUIRED') return '当前行程还没有保存标识，请先保存方案后再使用该能力';
  if (code === 'AUTH_REQUIRED') return '登录状态已失效，请返回大厅重新登录';
  return error instanceof Error ? error.message : fallback;
}

function levelLabel(level: string) {
  return ({ low: '低', medium: '中', high: '高' } as Record<string, string>)[level] || level || '未知';
}

export default function FieldAssistPanel({ tripId, destination, routes = [] }: Props) {
  const [crowdDate, setCrowdDate] = useState(localDate());
  const [crowdHour, setCrowdHour] = useState(() => new Date().getHours());
  const [crowdWeekend, setCrowdWeekend] = useState(() => [0, 6].includes(new Date().getDay()));
  const [crowd, setCrowd] = useState<any>(null);
  const [crowdBusy, setCrowdBusy] = useState(false);
  const [crowdError, setCrowdError] = useState('');

  const [language, setLanguage] = useState('en');
  const [pickedPhrases, setPickedPhrases] = useState<string[]>(['谢谢']);
  const [customPhrase, setCustomPhrase] = useState('');
  const [guide, setGuide] = useState<any>(null);
  const [guideBusy, setGuideBusy] = useState(false);
  const [guideError, setGuideError] = useState('');

  const [anchors, setAnchors] = useState<any>(null);
  const [anchorBusy, setAnchorBusy] = useState(false);
  const [anchorError, setAnchorError] = useState('');

  const anchorCandidates = useMemo(
    () => (Array.isArray(routes) ? routes : []).filter((node) => Array.isArray(node?.lnglat) && node.lnglat.length >= 2),
    [routes],
  );

  const togglePhrase = (phrase: string) => {
    setPickedPhrases((previous) => (previous.includes(phrase) ? previous.filter((item) => item !== phrase) : [...previous, phrase]));
  };

  const predictCrowd = async () => {
    if (!tripId) return;
    setCrowdBusy(true);
    setCrowdError('');
    try {
      const payload = await apiJson<any>(`/api/v1/planning/crowd/predict`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trip_id: tripId, destination: destination || '', date: crowdDate, hour: Number(crowdHour), weekend: crowdWeekend }),
      });
      setCrowd(payload?.data || payload);
    } catch (error) {
      setCrowd(null);
      setCrowdError(errorText(error, '拥挤预测暂时不可用'));
    } finally {
      setCrowdBusy(false);
    }
  };

  const translatePhrases = async () => {
    if (!tripId) return;
    const phrases = [...pickedPhrases, customPhrase.trim()].filter(Boolean);
    if (phrases.length === 0) {
      setGuideError('请至少选择一个短语');
      return;
    }
    setGuideBusy(true);
    setGuideError('');
    try {
      const payload = await apiJson<any>(`/api/v1/planning/guide/multilingual`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trip_id: tripId, destination: destination || '', target_language: language, phrases }),
      });
      setGuide(payload?.data || payload);
    } catch (error) {
      setGuide(null);
      setGuideError(errorText(error, '多语言导览暂时不可用'));
    } finally {
      setGuideBusy(false);
    }
  };

  const buildArAnchors = async () => {
    if (!tripId) return;
    setAnchorBusy(true);
    setAnchorError('');
    try {
      const route = anchorCandidates.map((node) => ({ name: node.name, day: Number(node.day) || 1, lnglat: node.lnglat }));
      const payload = await apiJson<any>(`/api/v1/planning/ar/guide`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trip_id: tripId, destination: destination || '', route }),
      });
      setAnchors(payload?.data || payload);
    } catch (error) {
      setAnchors(null);
      setAnchorError(errorText(error, 'AR 锚点数据暂时不可用'));
    } finally {
      setAnchorBusy(false);
    }
  };

  if (!tripId) return null;

  const crowdForecast = crowd?.forecast;
  const providerOptions = Array.isArray(crowdForecast) ? crowdForecast : [];
  const estimate = !Array.isArray(crowdForecast) && crowdForecast && typeof crowdForecast === 'object' ? crowdForecast : null;
  const explanation = crowd?.explanation && typeof crowd.explanation === 'object' ? crowd.explanation : null;
  const translations = guide?.translations && typeof guide.translations === 'object' ? guide.translations : {};
  const untranslated = Array.isArray(guide?.untranslated) ? guide.untranslated.filter(Boolean) : [];

  return <section className="mt-3 rounded-xl border border-slate-200 bg-white p-4 sm:p-5" aria-label="旅中辅助工具">
    <div>
      <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-900"><Radio className="h-4 w-4 text-sky-700" />旅中辅助</h3>
      <p className="mt-1 text-xs text-slate-500">拥挤预测、短语翻译与 AR 锚点会标明数据来自供应商还是规则/模板，不做无依据的实时承诺。</p>
    </div>
    <div className="mt-4 grid gap-3 lg:grid-cols-3">
      <div className="rounded-lg border border-slate-200 p-3">
        <div className="flex items-center justify-between">
          <h4 className="flex items-center gap-1.5 text-xs font-semibold text-slate-800"><Users className="h-3.5 w-3.5 text-orange-600" />拥挤预测</h4>
          <button type="button" disabled={crowdBusy} onClick={() => void predictCrowd()} className="inline-flex min-h-8 items-center gap-1 rounded-md bg-slate-900 px-2.5 text-[11px] font-semibold text-white transition-colors hover:bg-slate-700 disabled:opacity-50">{crowdBusy ? '查询中' : '查询'}</button>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-600">
          <label htmlFor="crowd-date" className="sr-only">日期</label>
          <input id="crowd-date" type="date" value={crowdDate} onChange={(event) => setCrowdDate(event.target.value)} className="h-8 rounded-md border border-slate-300 px-2 text-[11px]" />
          <label htmlFor="crowd-hour" className="sr-only">时段</label>
          <input id="crowd-hour" type="number" min={0} max={23} value={crowdHour} onChange={(event) => setCrowdHour(Math.max(0, Math.min(23, Number(event.target.value) || 0)))} className="h-8 w-16 rounded-md border border-slate-300 px-2 text-[11px]" />
          <label className="inline-flex items-center gap-1"><input type="checkbox" checked={crowdWeekend} onChange={(event) => setCrowdWeekend(event.target.checked)} className="h-3.5 w-3.5 rounded border-slate-300" />周末</label>
        </div>
        {crowdError && <p role="alert" className="mt-2 rounded-md bg-rose-50 px-2 py-1.5 text-[11px] text-rose-700">{crowdError}</p>}
        {estimate && <div className="mt-2 rounded-md border border-sky-100 bg-sky-50/60 px-2.5 py-2 text-[11px] text-slate-700">
          <p className="flex items-center gap-1.5 font-semibold text-slate-800"><CloudSun className="h-3.5 w-3.5 text-sky-600" />预计客流：{levelLabel(String(estimate.level || ''))}<span className="font-normal text-slate-500">（{Math.round(Number(estimate.score || 0) * 100)} 分）</span>{crowd?.estimated ? <span className="rounded bg-amber-100 px-1 py-0.5 text-[10px] font-normal text-amber-700">估算</span> : null}</p>
          <p className="mt-1 text-slate-500">来源：{sourceLabel(crowd?.source)} · 置信度 {Math.round(Number(estimate.confidence || 0) * 100)}%</p>
          {explanation?.recommended_window && <p className="mt-1">建议错峰窗口：<strong className="text-slate-800">{String(explanation.recommended_window)}</strong></p>}
          {explanation?.diversion_strategy && <p className="mt-0.5 text-slate-500">{String(explanation.diversion_strategy)}</p>}
          {Array.isArray(explanation?.reasons) && explanation.reasons.length > 0 && <p className="mt-0.5 text-slate-400">依据：{explanation.reasons.join('、')}</p>}
        </div>}
        {providerOptions.length > 0 && <div className="mt-2 rounded-md border border-emerald-100 bg-emerald-50/60 px-2.5 py-2 text-[11px] text-slate-700">
          <p className="font-semibold text-emerald-800">供应商实时数据 · {sourceLabel(crowd?.source)}</p>
          <ul className="mt-1 space-y-0.5">{providerOptions.slice(0, 5).map((option: any, index: number) => <li key={`${option?.name || option?.poi || index}`}>{String(option?.name || option?.poi || option?.location || `候选 ${index + 1}`)} · {levelLabel(String(option?.level || ''))}{typeof option?.score === 'number' ? ` · ${Math.round(option.score * 100)} 分` : ''}</li>)}</ul>
        </div>}
        {!crowd && !crowdError && <p className="mt-2 text-[11px] text-slate-400">尚未查询。未配置客流供应商时会返回估算结果，并标注估算来源。</p>}
      </div>

      <div className="rounded-lg border border-slate-200 p-3">
        <div className="flex items-center justify-between">
          <h4 className="flex items-center gap-1.5 text-xs font-semibold text-slate-800"><Languages className="h-3.5 w-3.5 text-indigo-600" />多语言导览</h4>
          <button type="button" disabled={guideBusy} onClick={() => void translatePhrases()} className="inline-flex min-h-8 items-center gap-1 rounded-md bg-slate-900 px-2.5 text-[11px] font-semibold text-white transition-colors hover:bg-slate-700 disabled:opacity-50">{guideBusy ? '翻译中' : '翻译'}</button>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <label htmlFor="guide-language" className="sr-only">目标语言</label>
          <select id="guide-language" value={language} onChange={(event) => setLanguage(event.target.value)} className="h-8 rounded-md border border-slate-300 px-2 text-[11px]">
            <option value="en">英语</option>
            <option value="ja">日语</option>
          </select>
          {phraseOptions.map((phrase) => <button key={phrase} type="button" onClick={() => togglePhrase(phrase)} className={`rounded-md border px-2 py-1 text-[11px] transition-colors ${pickedPhrases.includes(phrase) ? 'border-indigo-300 bg-indigo-50 text-indigo-700' : 'border-slate-200 bg-white text-slate-600 hover:border-indigo-200'}`}>{phrase}</button>)}
        </div>
        <label htmlFor="guide-custom" className="sr-only">自定义短语</label>
        <input id="guide-custom" value={customPhrase} onChange={(event) => setCustomPhrase(event.target.value)} placeholder="也可以输入一句自定义短语" className="mt-2 h-8 w-full rounded-md border border-slate-300 px-2 text-[11px]" />
        {guideError && <p role="alert" className="mt-2 rounded-md bg-rose-50 px-2 py-1.5 text-[11px] text-rose-700">{guideError}</p>}
        {guide && <div className="mt-2 rounded-md border border-indigo-100 bg-indigo-50/60 px-2.5 py-2 text-[11px] text-slate-700">
          <p className="flex items-center justify-between font-semibold text-slate-800"><span>{languageLabels[guide.language] || guide.language}</span><span className="rounded bg-amber-100 px-1 py-0.5 text-[10px] font-normal text-amber-700">{guide.translation_status === 'template_only' ? '模板短语，非实时翻译' : String(guide.translation_status || '来源未知')}</span></p>
          <div className="mt-1 space-y-1">{Object.entries(translations).map(([phrase, value]) => <p key={phrase} className="flex flex-wrap items-baseline gap-x-2"><span className="text-slate-500">{phrase}</span><span className={String(value) ? 'font-semibold text-slate-800' : 'text-rose-600'}>{String(value) || '未收录'}</span></p>)}</div>
          {untranslated.length > 0 && <p className="mt-1 text-rose-600">未收录：{untranslated.join('、')}</p>}
          <p className="mt-1 text-slate-400">来源：{sourceLabel(guide.source)}</p>
        </div>}
      </div>

      <div className="rounded-lg border border-slate-200 p-3">
        <div className="flex items-center justify-between">
          <h4 className="flex items-center gap-1.5 text-xs font-semibold text-slate-800"><Camera className="h-3.5 w-3.5 text-emerald-600" />AR 锚点</h4>
          <button type="button" disabled={anchorBusy || anchorCandidates.length === 0} onClick={() => void buildArAnchors()} className="inline-flex min-h-8 items-center gap-1 rounded-md bg-slate-900 px-2.5 text-[11px] font-semibold text-white transition-colors hover:bg-slate-700 disabled:opacity-50">{anchorBusy ? '生成中' : '生成'}</button>
        </div>
        <p className="mt-2 text-[11px] text-slate-500">{anchorCandidates.length > 0 ? `当前行程有 ${anchorCandidates.length} 个带坐标的节点可作为锚点。` : '当前行程节点都缺少坐标，无法生成 AR 锚点。'}</p>
        {anchorError && <p role="alert" className="mt-2 rounded-md bg-rose-50 px-2 py-1.5 text-[11px] text-rose-700">{anchorError}</p>}
        {anchors && <div className="mt-2 rounded-md border border-emerald-100 bg-emerald-50/60 px-2.5 py-2 text-[11px] text-slate-700">
          <p className="flex flex-wrap items-center gap-1.5 font-semibold text-slate-800">锚点 {Array.isArray(anchors.anchors) ? anchors.anchors.length : 0} 个{anchors.estimated ? <span className="rounded bg-amber-100 px-1 py-0.5 text-[10px] font-normal text-amber-700">估算</span> : null}</p>
          <p className="mt-1 text-slate-500">渲染模式：{String(anchors.render_mode || '未知')} · 摄像头：{anchors.camera_permission === 'client_required' ? '需客户端授权' : String(anchors.camera_permission || '未知')}</p>
          <ul className="mt-1 space-y-0.5">{Array.isArray(anchors.anchors) && anchors.anchors.slice(0, 6).map((anchor: any) => <li key={anchor.id} className="flex items-start gap-1.5"><MapPin className="mt-0.5 h-3 w-3 shrink-0 text-emerald-600" /><span>D{Number(anchor.day) || 1} · {String(anchor.name || '未命名节点')} · {Array.isArray(anchor.lnglat) ? anchor.lnglat.map((value: any) => Number(value).toFixed(4)).join(', ') : '坐标缺失'}</span></li>)}</ul>
          {Array.isArray(anchors.anchors) && anchors.anchors.length > 6 && <p className="mt-1 text-slate-400">…还有 {anchors.anchors.length - 6} 个</p>}
          <p className="mt-1 text-slate-400">该接口只提供 Web AR 锚点数据，不会自动打开摄像头；实际渲染需要浏览器具备相机能力并得到你的授权。</p>
        </div>}
      </div>
    </div>
  </section>;
}
