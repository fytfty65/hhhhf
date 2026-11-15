'use client';

import { useEffect, useRef, useState } from 'react';
import type { Season } from '../../lib/season';
import styles from './SeasonalExperience.module.css';

/** Enhancement is atomic: both aligned renders must decode before the poster is covered. */
export default function LayeredSeasonScene({ season, mobile, enabled, onReady }: { season: Season; mobile: boolean; enabled: boolean; onReady?: () => void }) {
  const host = useRef<HTMLDivElement>(null);
  const readyCallback = useRef(onReady);
  readyCallback.current = onReady;
  const [readyKey, setReadyKey] = useState('');
  const key = `${season}-${mobile ? 'mobile' : 'desktop'}`;
  const extension = season === 'spring' ? 'svg' : 'webp';
  const background = `/hero/v1/${key}-background.${extension}`;
  const subject = `/hero/v1/${key}-subject.${extension}`;
  const fog = season === 'spring' ? `/hero/v1/${key}-fog.svg` : null;
  const foreground = season === 'spring' ? `/hero/v1/${key}-foreground.svg` : null;
  const light = season === 'spring' ? `/hero/v1/${key}-light.svg` : null;
  const detail = season === 'spring' ? `/hero/v1/${key}-detail.svg` : null;
  useEffect(() => {
    const connection = (navigator as Navigator & { connection?: { saveData?: boolean } }).connection;
    if (connection?.saveData) return;
    let cancelled = false;
    const images = [background, subject, fog, foreground, light, detail].filter(Boolean).map(src => { const image = new Image(); image.src = src as string; return image; });
    Promise.all(images.map(image => image.decode())).then(() => { if (!cancelled) { setReadyKey(key); readyCallback.current?.(); } }).catch(() => { /* Keep complete poster. */ });
    return () => { cancelled = true; };
  }, [background, subject, fog, foreground, light, detail, key, enabled, mobile]);
  useEffect(() => {
    if (!enabled || readyKey !== key || mobile || !window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;
    let raf = 0;
    const move = (event: PointerEvent) => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        host.current?.style.setProperty('--scene-x', `${(event.clientX / window.innerWidth - .5) * 12}px`);
        host.current?.style.setProperty('--scene-y', `${(event.clientY / window.innerHeight - .5) * 8}px`);
      });
    };
    window.addEventListener('pointermove', move, { passive: true });
    return () => {
      window.removeEventListener('pointermove', move);
      cancelAnimationFrame(raf);
      host.current?.style.setProperty('--scene-x', '0px');
      host.current?.style.setProperty('--scene-y', '0px');
    };
  }, [enabled, mobile, readyKey, key]);
  if (readyKey !== key) return null;
  return <div ref={host} className={styles.layers}>
    <div className={styles.layerBackground} style={{ backgroundImage: `url("${background}")` }} />
    <div className={styles.layerSubject} style={{ backgroundImage: `url("${subject}")` }} />
    {fog && <div className={styles.layerFog} style={{ backgroundImage: `url("${fog}")` }} />}
    {foreground && <div className={styles.layerForeground} style={{ backgroundImage: `url("${foreground}")` }} />}
    {light && <div className={styles.layerLight} style={{ backgroundImage: `url("${light}")` }} />}
    {detail && <div className={styles.layerDetail} style={{ backgroundImage: `url("${detail}")` }} />}
  </div>;
}
