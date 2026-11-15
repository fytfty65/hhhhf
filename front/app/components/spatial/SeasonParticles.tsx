'use client';

import type { Season } from '../../lib/season';

const PARTICLES = Array.from({ length: 18 }, (_, index) => ({
  left: `${(index * 37) % 100}%`,
  size: `${5 + (index % 4) * 2}px`,
  delay: `${(index % 7) * -1.9}s`,
  duration: `${14 + (index % 6) * 3}s`,
  sway: `${index % 2 ? 5 : -5}vw`,
}));

export default function SeasonParticles({ season, enabled }: { season: Season; enabled: boolean }) {
  const kind = season === 'spring' ? 'petal' : season === 'autumn' ? 'leaf' : season === 'winter' ? 'snow' : 'none';
  if (!enabled || kind === 'none') return null;
  return (
    <div className="season-particles" data-kind={kind} aria-hidden="true">
      {PARTICLES.map((particle, index) => <span key={index} style={{ left: particle.left, width: particle.size, height: particle.size, '--delay': particle.delay, '--dur': particle.duration, '--sway': particle.sway } as React.CSSProperties} />)}
    </div>
  );
}
