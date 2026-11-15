'use client';

import type { Season, SeasonMode } from '../../lib/season';
import { SEASONS, SEASON_LABELS } from '../../lib/season';

interface Props {
  season: Season;
  mode: SeasonMode;
  motionEnabled: boolean;
  onSeasonChange: (season: Season) => void;
  onModeChange: (mode: SeasonMode) => void;
  onMotionChange: (enabled: boolean) => void;
}

export default function SeasonControls({ season, mode, motionEnabled, onSeasonChange, onModeChange, onMotionChange }: Props) {
  return (
    <div className="season-controls" aria-label="季节场景控制">
      <div className="season-picker" role="group" aria-label="选择季节">
        {SEASONS.map((item) => (
          <button key={item} type="button" aria-pressed={season === item} onClick={() => { onModeChange('manual'); onSeasonChange(item); }}>
            <span className="season-dot" style={{ backgroundColor: SEASON_LABELS[item].accent }} />
            {SEASON_LABELS[item].name}
          </button>
        ))}
      </div>
      <label className="motion-toggle">
        <input type="checkbox" checked={motionEnabled} onChange={(event) => onMotionChange(event.target.checked)} />
        <span>{motionEnabled ? '动态场景' : '静止场景'}</span>
      </label>
      <button className="season-mode" type="button" aria-pressed={mode === 'date'} onClick={() => onModeChange('date')}>
        跟随日期
      </button>
      <button className="season-mode" type="button" aria-pressed={mode === 'demo'} onClick={() => onModeChange(mode === 'demo' ? 'manual' : 'demo')}>
        {mode === 'demo' ? '暂停演示' : '演示四季'}
      </button>
    </div>
  );
}
