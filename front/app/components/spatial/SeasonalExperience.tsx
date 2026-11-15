'use client';

import { useEffect, useRef, useState } from 'react';
import type { Season } from '../../lib/season';
import PaintedLandscape from './PaintedLandscape';
import LayeredSeasonScene from './LayeredSeasonScene';
import { seasonAssets } from './seasonAssets';
import styles from './SeasonalExperience.module.css';

interface Frame { season: Season; url: string }
interface Props {
  season: Season;
  motionEnabled: boolean;
  onDisplayedSeason?: (season: Season) => void;
  onAssetStatus?: (status: 'loading' | 'ready' | 'fallback') => void;
  retryToken?: number;
}

/** A failed or superseded decode never replaces the last successful scene. */
export default function SeasonalExperience({ season, motionEnabled, onDisplayedSeason, onAssetStatus, retryToken = 0 }: Props) {
  const [mobile, setMobile] = useState<boolean | null>(null);
  const [frame, setFrame] = useState<Frame | null>(null);
  const [previous, setPrevious] = useState<Frame | null>(null);
  const [failed, setFailed] = useState(false);
  const [layeredReady, setLayeredReady] = useState(false);
  const frameRef = useRef<Frame | null>(null);
  const callback = useRef(onDisplayedSeason);
  callback.current = onDisplayedSeason;
  const statusCallback = useRef(onAssetStatus);
  statusCallback.current = onAssetStatus;

  useEffect(() => {
    const media = window.matchMedia('(max-width: 767px)');
    const update = () => setMobile(media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);

  useEffect(() => {
    if (!frameRef.current) callback.current?.(season);
    if (mobile === null) return;
    statusCallback.current?.('loading');
    setLayeredReady(false);
    let cancelled = false;
    const candidates = mobile
      ? seasonAssets[season].mobileCandidates ?? [seasonAssets[season].mobile]
      : seasonAssets[season].desktopCandidates ?? [seasonAssets[season].desktop];
    let image: HTMLImageElement | null = null;
    let url = candidates[candidates.length - 1];
    setFailed(false);
    const retry = window.setTimeout(() => {
      if (!cancelled && !image?.complete) { setFailed(true); statusCallback.current?.('fallback'); }
    }, 12000);
    const loadCandidate = async (index: number): Promise<void> => {
      if (cancelled) return;
      const candidate = candidates[index];
      const nextImage = new Image();
      nextImage.fetchPriority = index === 0 ? 'high' : 'auto';
      nextImage.src = candidate;
      try {
        await nextImage.decode();
        image = nextImage;
        url = candidate;
      } catch {
        if (index + 1 < candidates.length) return loadCandidate(index + 1);
        throw new Error('season asset decode failed');
      }
    };
    loadCandidate(0).then(() => {
      if (cancelled) return;
      window.clearTimeout(retry);
      setPrevious(frameRef.current);
      const next = { season, url };
      frameRef.current = next;
      setFrame(next);
      setFailed(false);
      statusCallback.current?.('ready');
      callback.current?.(season);
    }).catch(() => {
      window.clearTimeout(retry);
      if (!cancelled) {
        setFailed(true);
        statusCallback.current?.('fallback');
      }
    });
    return () => { cancelled = true; window.clearTimeout(retry); };
  }, [season, mobile, retryToken]);

  useEffect(() => {
    if (!previous) return;
    const timer = window.setTimeout(() => setPrevious(null), motionEnabled ? 2200 : 0);
    return () => window.clearTimeout(timer);
  }, [frame, previous, motionEnabled]);

  return (
    <div className={styles.scene} aria-hidden="true" data-season={frame?.season ?? season} data-asset-status={failed ? 'fallback' : frame ? 'ready' : 'loading'} data-motion={motionEnabled}>
      {!frame && <PaintedLandscape controlledSeason={season} autoRotate={false} motionEnabled={false} />}
      {previous && <div className={styles.poster} style={{ backgroundImage: `url("${previous.url}")` }} />}
      {frame && <div key={frame.url} className={`${styles.poster} ${styles.arriving} ${layeredReady ? styles.posterCovered : ''}`} style={{ backgroundImage: `url("${frame.url}")` }} />}
      {frame && !previous && <LayeredSeasonScene season={frame.season} mobile={mobile ?? false} enabled={motionEnabled} onReady={() => setLayeredReady(true)} />}
      <div className={styles.atmosphere} />
    </div>
  );
}
