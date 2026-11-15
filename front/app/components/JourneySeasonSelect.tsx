'use client';

import { ChevronDown, Flower2, Sun, Leaf, Snowflake } from 'lucide-react';
import { SEASONS, type Season } from '../lib/season';
import { JOURNEY_SEASONS } from '../lib/journeySeason';
import { useJourneySeason } from './JourneySeasonProvider';

export const seasonIcons = { spring: Flower2, summer: Sun, autumn: Leaf, winter: Snowflake };

export default function JourneySeasonSelect() {
  const { season, enterSeason } = useJourneySeason();
  const Icon = seasonIcons[season];
  return <label className="journey-season-select" title="切换季节氛围，不改变行程内容">
    <Icon size={17} strokeWidth={1.5} aria-hidden="true" />
    <select aria-label="旅程季节" value={season} onChange={event => enterSeason(event.target.value as Season)}>
      {SEASONS.map(item => <option key={item} value={item}>{JOURNEY_SEASONS[item].name} · {JOURNEY_SEASONS[item].title}</option>)}
    </select>
    <ChevronDown size={13} aria-hidden="true" />
  </label>;
}
