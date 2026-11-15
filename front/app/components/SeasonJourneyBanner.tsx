'use client';

import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { ArrowUpRight, Compass, Pause, Play } from 'lucide-react';
import { useJourneySeason } from './JourneySeasonProvider';
import { seasonIcons } from './JourneySeasonSelect';
import { restoreSeasonPreferences, SEASON_STORAGE_KEY } from '../lib/season';
import styles from './SeasonJourneyBanner.module.css';

const MOTION_KEY = 'omni_journey_banner_motion';
const scenes = {
  spring: { heading: '花开之处。', destination: '下一站 · 花开的地方', trail: 'FOLLOW THE BLOSSOMS, FIND YOUR WAY', path: 'M30 180C110 182 55 55 145 78S240 190 310 100 395 90 440 40', waypoint: [145, 78], kind: 'petals' },
  summer: { heading: '心之所向。', destination: '下一站 · 山海与长夏', trail: 'A LITTLE DETOUR, A NEW DISCOVERY', path: 'M30 180C115 195 65 20 190 55S280 180 350 90 410 85 440 40', waypoint: [190, 55], kind: 'fireflies' },
  autumn: { heading: '层林之间。', destination: '下一站 · 山野的秋色', trail: 'TAKE THE SLOW ROAD, KEEP THE GOLD', path: 'M30 180C80 135 125 195 175 125S205 40 280 68 390 135 440 40', waypoint: [280, 68], kind: 'leaves' },
  winter: { heading: '雪落之境。', destination: '下一站 · 雪中的暖意', trail: 'QUIET MOUNTAINS, WARM MEMORIES', path: 'M30 180C90 180 100 130 165 130S205 65 275 82 390 20 440 40', waypoint: [275, 82], kind: 'snow' },
} as const;

const motes = Array.from({ length: 14 }, (_, index) => ({
  '--x': `${44 + (index * 17) % 54}%`,
  '--y': `${15 + (index * 13) % 70}%`,
  '--size': `${4 + index % 4 * 2}px`,
  '--duration': `${13 + index % 5 * 3}s`,
  '--delay': `${-index * 2.3}s`,
  '--sway': `${index % 2 ? 35 : -45}px`,
} as CSSProperties));

export default function SeasonJourneyBanner({ onExplore }: { onExplore: () => void }) {
  const { season, story } = useJourneySeason();
  const scene = scenes[season];
  const Icon = seasonIcons[season];
  const host = useRef<HTMLElement>(null);
  const [enabled, setEnabled] = useState(false);
  const [reduced, setReduced] = useState(true);
  const [visible, setVisible] = useState(false);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(MOTION_KEY);
      setEnabled(saved === 'on' || (saved !== 'off' && restoreSeasonPreferences(localStorage.getItem(SEASON_STORAGE_KEY)).motionEnabled));
    } catch { setEnabled(true); }
    const media = matchMedia('(prefers-reduced-motion: reduce)');
    const syncMotion = () => setReduced(media.matches);
    const syncVisibility = () => setVisible(!document.hidden);
    syncMotion();
    syncVisibility();
    media.addEventListener('change', syncMotion);
    document.addEventListener('visibilitychange', syncVisibility);
    const observer = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting), { threshold: 0 });
    if (host.current) observer.observe(host.current);
    return () => {
      observer.disconnect();
      media.removeEventListener('change', syncMotion);
      document.removeEventListener('visibilitychange', syncVisibility);
    };
  }, []);

  const playing = enabled && !reduced && visible && inView;
  const toggleMotion = () => {
    setEnabled(!enabled);
    try { localStorage.setItem(MOTION_KEY, enabled ? 'off' : 'on'); } catch { /* Keep working without storage. */ }
  };

  return <section ref={host} className={styles.banner} data-testid="journey-season-banner" data-season={season} data-motion={playing ? 'playing' : 'paused'} aria-label={`${story.name}季旅程规划序章`}>
    <div className={styles.landscape} data-testid="journey-banner-landscape" aria-hidden="true" />
    <div className={styles.light} aria-hidden="true" />
    <div className={styles.mist} aria-hidden="true" />
    <div key={season} className={styles.particles} data-kind={scene.kind} aria-hidden="true">{motes.map((style, index) => <i key={index} style={style} />)}</div>
    <div className={styles.copy}>
      <p className={styles.eyebrow}><Compass size={15} strokeWidth={1.5} /> YOUR JOURNEY STARTS HERE</p>
      <h2>下一站，<em>{scene.heading}</em></h2>
      <p className={styles.description}>从一个目的地开始，把大家的期待写进同一段旅程。</p>
      <button type="button" className={styles.explore} onClick={onExplore}>寻找目的地灵感 <ArrowUpRight size={17} /></button>
      <div className={styles.seasonNote}><Icon size={15} strokeWidth={1.4} /><span>{story.title}</span><span className={styles.noteDivider} />{story.english} / {story.edition}</div>
    </div>
    <div className={styles.routeArt} aria-hidden="true">
      <p className={styles.routeCaption}>{scene.trail}</p>
      <svg viewBox="0 0 480 230" fill="none">
        <path d="M0 175C65 175 30 78 110 98S235 207 275 127 365 0 480 30" stroke="currentColor" opacity=".22" />
        <path className={styles.routeLine} d={scene.path} stroke="currentColor" strokeWidth="2" strokeDasharray="5 9" />
        <circle cx="30" cy="180" r="6" fill="currentColor" />
        <circle cx={scene.waypoint[0]} cy={scene.waypoint[1]} r="5" fill="currentColor" />
        <circle cx="440" cy="40" r="11" stroke="currentColor" strokeWidth="1.5" />
        <circle className={styles.destinationPulse} cx="440" cy="40" r="17" stroke="currentColor" />
        <circle cx="440" cy="40" r="3" fill="currentColor" />
        <circle className={styles.traveler} r="3.5" fill="#fff8ea" style={{ offsetPath: `path('${scene.path}')` }} />
        <text x="12" y="214" fill="currentColor" fontSize="12">此刻 · 出发</text>
        <text x="465" y="74" fill="currentColor" fontSize="12" textAnchor="end">{scene.destination}</text>
      </svg>
      <div className={styles.stamp}><span>GO</span><small>TOGETHER</small></div>
    </div>
    <button type="button" className={styles.motionToggle} onClick={toggleMotion} disabled={reduced}
      aria-label={reduced ? '系统已减少动态效果' : enabled ? '暂停横幅动效' : '开启横幅动效'} aria-pressed={enabled && !reduced}
      title={reduced ? '已跟随系统减少动态效果' : enabled ? '暂停横幅动效' : '开启横幅动效'}>
      {enabled && !reduced ? <Pause size={13} /> : <Play size={13} />}<span>{reduced ? '静态风景' : enabled ? '动态风景' : '静态风景'}</span>
    </button>
  </section>;
}
