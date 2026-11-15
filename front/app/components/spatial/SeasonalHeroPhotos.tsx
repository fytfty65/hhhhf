'use client';

/**
 * SeasonalHeroPhotos — 摄影背景层（登录门户的"专业级"背景方案）
 *
 * 参考稿那种质感来自渲染/绘制的图像素材，而不是用代码"画"出来的。
 * 因此这里预留了标准的图片背景位：
 *
 *   把 4 张风景照放到 front/public/hero/ 目录，命名为
 *     spring.jpg / summer.jpg / autumn.jpg / winter.jpg
 *   （也支持 .png / .webp；四张都就位后本层自动启用并开始四季交叉淡化）
 *
 * - 全部读取同源静态资源（CSP img-src 已含 'self'，零配置改动）；
 * - 四张缺任意一张就完全不启用，避免"照片/手绘"两种画风混用；
 * - 减少动态时不做淡入淡出，直接切换。
 */

import { useEffect, useState } from 'react';
import type { Season } from './PaintedLandscape';

const PHOTO_SEASONS: Season[] = ['spring', 'summer', 'autumn', 'winter'];
const EXTENSIONS = ['jpg', 'webp', 'png'] as const;

export default function SeasonalHeroPhotos({ season }: { season: Season }) {
  const [urls, setUrls] = useState<Record<Season, string | null>>({
    spring: null,
    summer: null,
    autumn: null,
    winter: null,
  });

  useEffect(() => {
    let cancelled = false;
    const resolved: Record<Season, string | null> = {
      spring: null,
      summer: null,
      autumn: null,
      winter: null,
    };
    let settled = 0;

    const tryLoad = (seasonName: Season, index: number) => {
      if (cancelled || index >= EXTENSIONS.length) {
        settled += 1;
        if (!cancelled && settled === PHOTO_SEASONS.length && PHOTO_SEASONS.every((item) => resolved[item])) {
          // 四张全部就位才启用，避免画风混杂
          setUrls({ ...resolved });
        }
        return;
      }
      const url = `/hero/${seasonName}.${EXTENSIONS[index]}`;
      const image = new Image();
      image.onload = () => {
        if (cancelled) return;
        resolved[seasonName] = url;
        settled += 1;
        if (settled === PHOTO_SEASONS.length && PHOTO_SEASONS.every((item) => resolved[item])) {
          setUrls({ ...resolved });
        }
      };
      image.onerror = () => tryLoad(seasonName, index + 1);
      image.src = url;
    };

    PHOTO_SEASONS.forEach((item) => tryLoad(item, 0));
    return () => {
      cancelled = true;
    };
  }, []);

  const ready = PHOTO_SEASONS.every((item) => urls[item]);
  if (!ready) return null;

  return (
    <div className="photo-hero" aria-hidden="true">
      {PHOTO_SEASONS.map((item) => (
        <div
          key={item}
          className="photo-hero-layer"
          data-active={item === season ? 'true' : 'false'}
          style={{ backgroundImage: `url(${urls[item]})` }}
        />
      ))}
    </div>
  );
}
