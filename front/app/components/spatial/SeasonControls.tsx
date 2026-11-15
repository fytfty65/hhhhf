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
      <label className="season-mode-label">
        <span>场景模式</span>
        <select aria-label="场景模式" value={mode} onChange={(event) => {
          const next = event.target.value as SeasonMode;
          onModeChange(next);
          if (next === 'demo') onMotionChange(true);
        }}>
          <option value="date">随日期变换</option>
          <option value="demo">四季循环 · 24 秒</option>
          <option value="manual">手动选季</option>
        </select>
      </label>
      <label className="motion-toggle">
        <input type="checkbox" checked={motionEnabled} onChange={(event) => onMotionChange(event.target.checked)} />
        <span>{motionEnabled ? '动态开启' : '动态暂停'}</span>
      </label>
    </div>
  );
}
