'use client';

/**
 * ReferenceRoutePanel —— 「参考路线」学习入口。
 *
 * 用户需求原话：粘贴一条自己喜欢的路线（如小红书帖子）→ 智能体学习成画像 →
 * 当用户没提出更多需求时，按这份偏好生成新路线。
 *
 * 界面口径（与后端一致，刻意"去 AI 化"）：
 * - 报表式排印（细分割线、等宽数字、小字号标签），不写"AI 已学习你的品味"这类措辞；
 * - 如实说明**存了什么**：默认"特征 + 出处片段"，可勾选只存特征；整篇原文不保存；
 * - 抽不到的特征显示"未识别"，不拿默认值冒充；
 * - 明确写出**诉求优先**：当轮说清楚的要求会覆盖画像。
 */

import React, { useCallback, useEffect, useState } from 'react';
import { Bookmark, ChevronDown, ImageUp, RefreshCw, Trash2 } from 'lucide-react';
import { apiJson, API_BASE, getAuthToken } from '../lib/utils';
import {
  buildOcrPayload,
  buildSavePayload,
  confidenceLabel,
  describeOcrFailure,
  OCR_PRIVACY_NOTE,
  readOcrResult,
  REFERENCE_ROUTE_ENDPOINT,
  REFERENCE_ROUTE_OCR_ENDPOINT,
  storageDisclosure,
  summarizeProfile,
  validateImageFile,
  type ReferenceProfile,
} from '../lib/referenceProfile';

type SavedState = { profile: ReferenceProfile | null; referenceCount: number };

export default function ReferenceRoutePanel({ defaultOpen = false }: { defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const [text, setText] = useState('');
  const [sourceLabel, setSourceLabel] = useState('');
  const [keepEvidence, setKeepEvidence] = useState(true);
  const [saved, setSaved] = useState<SavedState>({ profile: null, referenceCount: 0 });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  // 截图识别：识别中 / 识别说明 / 识别失败（失败原因一定要说出来，不能让用户干等）
  const [ocrBusy, setOcrBusy] = useState(false);
  const [ocrNote, setOcrNote] = useState('');
  const [ocrError, setOcrError] = useState('');
  const [pickedName, setPickedName] = useState('');

  const load = useCallback(async () => {
    try {
      const payload = await apiJson<any>(`${API_BASE}${REFERENCE_ROUTE_ENDPOINT}`);
      const profile = (payload?.data?.profile ?? payload?.profile ?? null) as ReferenceProfile | null;
      const count = Number(payload?.data?.reference_count ?? payload?.reference_count ?? 0);
      setSaved({ profile, referenceCount: Number.isFinite(count) ? count : 0 });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '参考路线读取失败');
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    const built = buildSavePayload(text, sourceLabel, keepEvidence);
    if (built.ok === false) {
      setMessage(built.reason);
      return;
    }
    setBusy(true);
    setMessage('');
    try {
      const payload = await apiJson<any>(`${API_BASE}${REFERENCE_ROUTE_ENDPOINT}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getAuthToken()}` },
        body: JSON.stringify(built.payload),
      });
      const profile = (payload?.data?.profile ?? payload?.profile ?? null) as ReferenceProfile | null;
      const count = Number(payload?.data?.reference_count ?? payload?.reference_count ?? 0);
      setSaved({ profile, referenceCount: Number.isFinite(count) ? count : 0 });
      setMessage(String(payload?.data?.note ?? payload?.note ?? '已保存抽取出的偏好特征。'));
      setText('');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '参考路线保存失败');
    } finally {
      setBusy(false);
    }
  };

  /**
   * 截图 → 文字。两条硬口径：
   *  1. 识别出的文字只**填进输入框**，绝不自动学习 —— 识别错一个地名不会被静默学成偏好；
   *  2. 识别不了就把原因说出来（格式/体积/没文字/服务不可用），并给出去路（粘贴文字）。
   */
  const recognizeImage = useCallback(async (file: File | null | undefined) => {
    const check = validateImageFile(file);
    if (check.ok === false) {
      setOcrError(check.reason);
      setOcrNote('');
      return;
    }
    setPickedName(file?.name ? `${file.name}（${Math.max(1, Math.round(Number(file.size) / 1024))}KB）` : '已选择截图');
    setOcrBusy(true);
    setOcrError('');
    setOcrNote('');
    try {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ''));
        reader.onerror = () => reject(new Error('图片读取失败，请重新选择文件'));
        reader.readAsDataURL(file as File);
      });
      const payload = await apiJson<any>(`${API_BASE}${REFERENCE_ROUTE_OCR_ENDPOINT}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getAuthToken()}` },
        body: JSON.stringify(buildOcrPayload(dataUrl, String(file?.type || ''))),
      });
      const result = readOcrResult(payload);
      if (result.ok === false) {
        setOcrError(result.reason);
        return;
      }
      setText(result.text);
      setSourceLabel((current) => current || '截图识别');
      setOcrNote([result.note, ...(result.warnings || [])].filter(Boolean).join(' '));
    } catch (error) {
      const code = (error as { code?: string } | null)?.code
        || (/REFERENCE_OCR_[A-Z_]+/.exec(error instanceof Error ? error.message : '') || [])[0]
        || null;
      setOcrError(describeOcrFailure(code, error instanceof Error ? error.message : undefined));
    } finally {
      setOcrBusy(false);
    }
  }, []);

  const handlePaste = useCallback(
    (event: React.ClipboardEvent<HTMLDivElement>) => {
      const items = Array.from(event.clipboardData?.items || []);
      const imageItem = items.find((item) => String(item.type || '').startsWith('image/'));
      if (!imageItem) return; // 纯文字粘贴照旧走输入框，不拦截
      const file = imageItem.getAsFile();
      if (!file) return;
      event.preventDefault();
      void recognizeImage(file);
    },
    [recognizeImage],
  );

  const clear = async () => {
    setBusy(true);
    setMessage('');
    try {
      await apiJson<any>(`${API_BASE}${REFERENCE_ROUTE_ENDPOINT}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${getAuthToken()}` },
      });
      setSaved({ profile: null, referenceCount: 0 });
      setMessage('已清除参考路线画像，之后的方案不再使用它。');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '清除失败');
    } finally {
      setBusy(false);
    }
  };

  const summary = summarizeProfile(saved.profile);
  const confidence = confidenceLabel(saved.profile);

  return (
    <section
      className="rounded-xl border border-slate-200 bg-white p-4 text-left sm:p-5"
      aria-label="参考路线学习"
      data-testid="reference-route-panel"
    >
      <div className="flex items-start justify-between gap-3">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          data-testid="reference-route-toggle"
          className="flex flex-1 items-start gap-2 text-left"
        >
          <ChevronDown className={`mt-0.5 h-4 w-4 shrink-0 text-slate-400 transition-transform ${open ? '' : '-rotate-90'}`} />
          <span>
            <span className="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <Bookmark className="h-4 w-4 text-orange-700" />
              参考路线（可选）
            </span>
            <span className="mt-1 block text-xs leading-relaxed text-slate-500">
              {summary.lines.length > 0
                ? `已学 ${saved.referenceCount} 条：${summary.lines[0]}${summary.lines.length > 1 ? ' 等' : ''}；你没提要求时按它排`
                : '粘贴一条你喜欢的路线（如小红书帖子）。当你没提出更多要求时，就按它的特点排。'}
            </span>
          </span>
        </button>
        <button
          type="button"
          onClick={() => void load()}
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-slate-200 text-slate-600 transition-colors hover:bg-slate-50"
          title="重新读取已保存的画像"
          aria-label="重新读取已保存的画像"
        >
          <RefreshCw className="h-4 w-4" />
        </button>
      </div>

      {open && (
        <div className="mt-3 border-t border-slate-100 pt-3">
          <p className="text-xs leading-relaxed text-slate-500">
            <strong className="text-slate-700">你在诉求里说清楚的要求优先于它。</strong>
            所以这里适合放"我平时就喜欢这么玩"的参考，而不是这一次的具体要求。
          </p>

      <label className="mt-3 block text-[11px] font-bold text-slate-500" htmlFor="reference-route-text">
        参考路线正文
      </label>

      {/* 截图入口：小红书路线常常是一张图，复制正文很麻烦。识别结果只填进输入框，由用户核对后再学习。 */}
      <div
        className="mt-1.5 rounded-lg border border-dashed border-slate-200 bg-slate-50/60 px-3 py-2.5"
        data-testid="reference-route-image-block"
        onPaste={handlePaste}
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <label
            className={`inline-flex min-h-8 cursor-pointer items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 text-[12px] font-bold text-slate-700 transition-colors hover:bg-slate-50 ${ocrBusy ? 'opacity-60' : ''}`}
          >
            <ImageUp className="h-3.5 w-3.5 text-orange-700" />
            {ocrBusy ? '识别中…' : '上传截图识别文字'}
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp"
              className="sr-only"
              data-testid="reference-route-image-input"
              disabled={ocrBusy}
              onChange={(event) => {
                const file = event.target.files?.[0];
                // 清空 value：同一张图再选一次也要能触发（否则第二次没有任何反应）
                event.target.value = '';
                void recognizeImage(file);
              }}
            />
          </label>
          <span className="text-[11px] text-slate-400">也可以把截图直接粘贴到这里（Ctrl+V）</span>
          {pickedName && !ocrBusy && (
            <span className="font-mono text-[11px] tabular-nums text-slate-500" data-testid="reference-route-image-name">
              {pickedName}
            </span>
          )}
          {ocrBusy && (
            <span className="text-[11px] text-slate-500" data-testid="reference-route-ocr-status" role="status">
              正在识别截图里的文字…
            </span>
          )}
        </div>
        {ocrError && (
          <p
            className="mt-2 rounded-lg border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-[12px] leading-relaxed text-rose-700"
            data-testid="reference-route-ocr-error"
            role="alert"
          >
            {ocrError}
          </p>
        )}
        {ocrNote && !ocrError && (
          <p className="mt-2 text-[11.5px] leading-relaxed text-slate-600" data-testid="reference-route-ocr-note">
            {ocrNote}
          </p>
        )}
        <p className="mt-1.5 text-[11px] leading-relaxed text-slate-400" data-testid="reference-route-ocr-privacy">
          {OCR_PRIVACY_NOTE}
        </p>
      </div>

      <textarea
        id="reference-route-text"
        data-testid="reference-route-text"
        value={text}
        onChange={(event) => setText(event.target.value)}
        rows={5}
        placeholder="把帖子正文粘贴到这里（含路线、天数、吃了什么、住了哪里、节奏怎样…）"
        className="mt-1 w-full resize-y rounded-lg border border-slate-200 px-3 py-2 text-[12.5px] leading-relaxed text-slate-700 focus:border-orange-400 focus:outline-none"
      />

      <div className="mt-2 flex flex-wrap items-center gap-3">
        <input
          data-testid="reference-route-source"
          value={sourceLabel}
          onChange={(event) => setSourceLabel(event.target.value)}
          placeholder="来源备注（可选，如：小红书-乌鲁木齐4天）"
          className="min-h-9 flex-1 rounded-lg border border-slate-200 px-3 text-[12px] text-slate-700 focus:border-orange-400 focus:outline-none"
        />
        <label className="flex items-center gap-1.5 text-[12px] text-slate-600">
          <input
            type="checkbox"
            data-testid="reference-route-keep-evidence"
            checked={!keepEvidence}
            onChange={(event) => setKeepEvidence(!event.target.checked)}
          />
          只保存特征（不留出处片段）
        </label>
        <button
          type="button"
          data-testid="reference-route-save"
          onClick={() => void save()}
          disabled={busy}
          className="min-h-9 rounded-lg bg-orange-600 px-3.5 text-[12px] font-bold text-white transition-colors hover:bg-orange-700 disabled:opacity-60"
        >
          {busy ? '处理中…' : '学习这条路线'}
        </button>
      </div>
      <p className="mt-1.5 text-[11px] leading-relaxed text-slate-400">{storageDisclosure(keepEvidence)}</p>

      {message && (
        <p className="mt-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-[12px] leading-relaxed text-slate-600" data-testid="reference-route-message">
          {message}
        </p>
      )}

      <div className="mt-3 border-t border-slate-100 pt-2.5">
        <div className="flex items-center justify-between">
          <p className="text-[11px] font-bold text-slate-500">
            目前已学到{summary.lines.length > 0 ? `（参考 ${saved.referenceCount} 条）` : ''}
          </p>
          {confidence && <span className="font-mono text-[11px] tabular-nums text-slate-500">{confidence}</span>}
        </div>
        {summary.lines.length > 0 ? (
          <ul className="mt-1.5 space-y-1" data-testid="reference-route-summary">
            {summary.lines.map((line) => (
              <li key={line} className="text-[12.5px] leading-relaxed text-slate-600">
                · {line}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-500">还没有参考路线。粘贴一条后这里会列出学到的特点。</p>
        )}
        {summary.unknown.length > 0 && (
          <p className="mt-1.5 text-[12px] leading-relaxed text-slate-500">
            未识别（不会拿默认值冒充）：{summary.unknown.join('、')}
          </p>
        )}
        {summary.lines.length > 0 && (
          <button
            type="button"
            onClick={() => void clear()}
            disabled={busy}
            data-testid="reference-route-clear"
            className="mt-2 inline-flex min-h-8 items-center gap-1.5 rounded-lg border border-rose-200 px-3 text-[12px] font-bold text-rose-600 transition-colors hover:bg-rose-50 disabled:opacity-60"
          >
            <Trash2 className="h-3.5 w-3.5" />
            清除参考路线画像
          </button>
        )}
        {saved.profile?.source_label && (
          <p className="mt-2 text-[11px] text-slate-400">最近一条来源备注：{saved.profile.source_label}</p>
        )}
        </div>
      </div>
      )}
    </section>
  );
}
