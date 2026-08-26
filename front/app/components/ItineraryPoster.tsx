'use client';

import { useCallback, useRef, useState } from 'react';

// 行程节点结构（与后端 final_route 的 route 节点对齐）
export interface PosterNode {
  day?: number;
  time?: string;
  location?: string;
  name?: string;
  cost_estimate?: string;
  tags?: string[];
  crowdedness?: { label?: string; level?: string; percent?: number };
}

export type PosterTemplate = 'minimal' | 'sunset' | 'midnight';

const TEMPLATES: Record<PosterTemplate, { name: string; bg: [string, string]; accent: string; text: string; sub: string }> = {
  minimal: { name: '极简白', bg: ['#ffffff', '#f5f7fa'], accent: '#111827', text: '#111827', sub: '#6b7280' },
  sunset: { name: '落日橙', bg: ['#ff7e5f', '#feb47b'], accent: '#fff7ed', text: '#ffffff', sub: '#ffe4d6' },
  midnight: { name: '深夜蓝', bg: ['#0f2027', '#2c5364'], accent: '#e0f2fe', text: '#f8fafc', sub: '#a5b4cf' },
};

const W = 1080;
const H = 1920;

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

export default function ItineraryPoster({ route, city }: { route: PosterNode[]; city?: string }) {
  const canvasPreviewRef = useRef<HTMLCanvasElement>(null);
  const [template, setTemplate] = useState<PosterTemplate>('minimal');
  const [status, setStatus] = useState<string>('');

  const drawPoster = useCallback(
    (targetCanvas: HTMLCanvasElement, tpl: PosterTemplate) => {
      const ctx = targetCanvas.getContext('2d');
      if (!ctx) return;
      const s = TEMPLATES[tpl];

      // 背景渐变
      const grad = ctx.createLinearGradient(0, 0, 0, H);
      grad.addColorStop(0, s.bg[0]);
      grad.addColorStop(1, s.bg[1]);
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, W, H);

      // 顶部装饰圆
      ctx.globalAlpha = 0.12;
      ctx.fillStyle = s.accent;
      ctx.beginPath();
      ctx.arc(W - 160, 120, 260, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;

      // 头部
      ctx.fillStyle = s.text;
      ctx.font = 'bold 76px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText(city ? `${city}·旅行路书` : '我的旅行路书', 80, 190);

      ctx.fillStyle = s.sub;
      ctx.font = '38px "PingFang SC", "Microsoft YaHei", sans-serif';
      const dateStr = new Date().toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' });
      ctx.fillText(dateStr, 84, 270);

      // 分隔线
      ctx.strokeStyle = s.accent;
      ctx.globalAlpha = 0.5;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(80, 330);
      ctx.lineTo(W - 80, 330);
      ctx.stroke();
      ctx.globalAlpha = 1;

      // 行程节点
      let y = 430;
      let lastDay = -1;
      const maxNodes = Math.min(route.length, 14);
      for (let i = 0; i < maxNodes; i++) {
        const n = route[i];
        const day = n.day ?? Math.floor(i / 5) + 1;
        if (day !== lastDay) {
          if (y > 430) y += 60;
          ctx.fillStyle = s.accent;
          ctx.font = 'bold 52px "PingFang SC", "Microsoft YaHei", sans-serif';
          ctx.fillText(`Day ${day}`, 80, y);
          y += 70;
          lastDay = day;
        }

        const loc = n.location || n.name || '特色目的地';
        const cost = n.cost_estimate || '';
        const crowd = n.crowdedness?.label ? ` · 拥挤度${n.crowdedness.label}` : '';

        // 时间点
        ctx.fillStyle = s.sub;
        ctx.font = '30px "PingFang SC", "Microsoft YaHei", sans-serif';
        const time = n.time ? n.time.split('|')[1]?.trim() || n.time : '';
        ctx.fillText(time, 80, y);

        // 地点
        ctx.fillStyle = s.text;
        ctx.font = 'bold 42px "PingFang SC", "Microsoft YaHei", sans-serif';
        ctx.fillText(loc.slice(0, 16), 80, y + 56);

        // 花费 + 拥挤度
        ctx.fillStyle = s.sub;
        ctx.font = '30px "PingFang SC", "Microsoft YaHei", sans-serif';
        ctx.fillText(`${cost}${crowd}`, 80, y + 104);

        // 卡片底部色条
        ctx.globalAlpha = 0.25;
        ctx.fillStyle = s.accent;
        roundRect(ctx, 80, y + 122, W - 160, 6, 3);
        ctx.fill();
        ctx.globalAlpha = 1;

        y += 180;
      }

      // 底部标语
      ctx.fillStyle = s.text;
      ctx.font = 'bold 44px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText('愿每一次出发，都不负期待。', 80, H - 180);
      ctx.fillStyle = s.sub;
      ctx.font = '30px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText('OmniRoute · 智能旅行规划', 80, H - 100);
    },
    [city]
  );

  const generate = useCallback(() => {
    setStatus('正在生成海报...');
    const started = performance.now();
    const canvas = document.createElement('canvas');
    canvas.width = W;
    canvas.height = H;
    drawPoster(canvas, template);

    // 同步预览
    const preview = canvasPreviewRef.current;
    if (preview) {
      preview.width = W / 3;
      preview.height = H / 3;
      const pctx = preview.getContext('2d');
      if (pctx) pctx.drawImage(canvas, 0, 0, W / 3, H / 3);
    }
    const elapsed = Math.round(performance.now() - started);
    setStatus(`生成完成（${elapsed}ms，高清 1080×1920），点击下载即可分享`);
  }, [drawPoster, template]);

  const download = useCallback(() => {
    const canvas = document.createElement('canvas');
    canvas.width = W;
    canvas.height = H;
    drawPoster(canvas, template);
    const a = document.createElement('a');
    a.href = canvas.toDataURL('image/png');
    a.download = `omniroute_${city || 'itinerary'}_${template}.png`;
    a.click();
  }, [drawPoster, template, city]);

  const share = useCallback(async () => {
    const canvas = document.createElement('canvas');
    canvas.width = W;
    canvas.height = H;
    drawPoster(canvas, template);
    const blob: Blob | null = await new Promise((res) => canvas.toBlob(res, 'image/png'));
    const text = `我在用 OmniRoute 规划的${city || '旅行'}路书，一起看看吧！`;

    if (navigator.share && blob) {
      try {
        const file = new File([blob], 'omniroute.png', { type: 'image/png' });
        await navigator.share({ title: 'OmniRoute 行程海报', text, files: [file] });
        setStatus('已唤起系统分享（可直接发送至微信/朋友圈）');
        return;
      } catch (e) {
        if ((e as Error).name === 'AbortError') return;
      }
    }
    // 微信/不支持 Web Share 的降级：复制文案，海报图片由下载保存后长按分享
    await navigator.clipboard.writeText(text);
    setStatus('已复制分享文案；请同时点击「下载海报」保存图片后发送至微信');
  }, [drawPoster, template, city]);

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-lg font-semibold text-slate-800">一键分享行程海报</h3>
        <div className="flex gap-2">
          {(Object.keys(TEMPLATES) as PosterTemplate[]).map((t) => (
            <button
              key={t}
              onClick={() => setTemplate(t)}
              className={`rounded-full px-3 py-1 text-xs font-medium transition ${
                template === t ? 'bg-indigo-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
              }`}
            >
              {TEMPLATES[t].name}
            </button>
          ))}
        </div>
      </div>

      <div className="flex flex-col items-center gap-4 sm:flex-row">
        <canvas ref={canvasPreviewRef} className="h-80 w-45 rounded-lg border border-slate-200 bg-slate-50" />
        <div className="flex w-full flex-col gap-2">
          <button onClick={generate} className="rounded-lg bg-indigo-600 py-2 text-sm font-medium text-white hover:bg-indigo-700">
            生成海报
          </button>
          <button onClick={download} className="rounded-lg border border-slate-300 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">
            下载高清长图 (1080×1920)
          </button>
          <button onClick={share} className="rounded-lg bg-green-600 py-2 text-sm font-medium text-white hover:bg-green-700">
            分享至微信/朋友圈
          </button>
          <p className="text-xs text-slate-500">{status || '选择模板后点击生成，支持 3 种风格、高清导出'}</p>
        </div>
      </div>
    </div>
  );
}