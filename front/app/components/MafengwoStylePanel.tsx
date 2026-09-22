'use client';

/**
 * MafengwoStylePanel — 工作区「马蜂窝风」行程面板（日切换、POI 卡片与投票、预算/风险/共识摘要、导出与增量调优入口）
 *
 * Extracted verbatim from ContextualLobby.tsx during the file split (批 6)。
 * 仅搬运：props 签名、类名与文案逐字保留，未做任何行为改动。
 */

import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { AlertCircle, BrainCircuit, CheckCircle2, Clock, ExternalLink, Footprints, Hotel, MapPin, PiggyBank, RefreshCw, RotateCw, ShieldCheck, Split, Sun, ThumbsDown, ThumbsUp, Wand2 } from 'lucide-react';
import { getCleanPhotoUrl } from '../lib/lobbyUtils';
import PoiImage from './PoiImage';
import DynamicBudgetCard from './DynamicBudgetCard';
import ExpandableConnectorNav from './ExpandableConnectorNav';
import TeamPresenceBar from './TeamPresenceBar';
import ConsensusExplainability from './ConsensusExplainability';
import ScenarioSimulator from './ScenarioSimulator';

export default function MafengwoStylePanel({ 
  routes, totalDays, activeDayIndex, onSelectDay, travelDetails, selectedPoiIndex, 
  onSelectPoi, weatherInfo, trafficInfo, onReset, consensusSummary, budgetData, 
  onSwapPoi, onMarkVisited, visitedNodes = [], roomCode, roomMembers, teamSatisfaction, arbitrationRecords, nodeVotes, onVote,
  wsConnected = true, fairnessAudit,
  safetyInfo, safetyBrief, briefLoading, onGenerateSafetyBrief, targetCity,
  onPhotoClick, currentUser, onApplyScenario, onSecondaryPlan
}: any) {
  const [showWeatherForecast, setShowWeatherForecast] = useState(false);

  return (
    <div className="pt-2 pb-32 font-sans">
      <div className="px-8 pt-4">
        <TeamPresenceBar 
          members={roomMembers} 
          teamSatisfaction={teamSatisfaction} 
          roomCode={roomCode} 
          arbitrationRecords={arbitrationRecords}
          wsConnected={wsConnected}
          currentUser={currentUser}
        />
      </div>

      <div className="flex items-center gap-2 px-8 mb-6 sticky top-0 bg-white/95 backdrop-blur-md py-4 z-20 border-b border-slate-100 overflow-x-auto custom-scrollbar">
        {totalDays.map((dayNum: number, idx: number) => (
          <button 
            key={`day-pill-btn-${dayNum}-${idx}`} 
            onClick={() => onSelectDay(idx)}
            className={`px-4 py-1.5 rounded-full text-xs font-bold border transition-all cursor-pointer whitespace-nowrap ${activeDayIndex === idx ? 'bg-orange-500 text-white border-orange-500 shadow-md shadow-orange-200 scale-105' : 'bg-white text-slate-600 border-slate-200 hover:border-orange-300 hover:text-orange-500'}`}
          >
            Day {dayNum}
          </button>
        ))}
      </div>

      <div className="px-8">
        <DynamicBudgetCard summary={consensusSummary} budgetData={budgetData} />
        <ConsensusExplainability
          route={routes || []}
          members={(roomMembers || []).map((member: any) => ({ id: member.id, name: member.name, interestTags: member.intent ? [member.intent] : [] }))}
          fairnessAudit={fairnessAudit}
        />
        <ScenarioSimulator
          route={routes || []}
          members={(roomMembers || []).map((member: any) => ({
            id: member.id,
            name: member.name,
            interestTags: member.intent ? [member.intent] : [],
            budgetWeight: Number(member.budgetWeight ?? member.budget_weight ?? 1),
            paceWeight: Number(member.paceWeight ?? member.pace_weight ?? 1),
            riskWeight: Number(member.riskWeight ?? member.risk_weight ?? 1),
          }))}
          onApply={onApplyScenario}
        />

        {weatherInfo && (
          <div className="mb-8 p-4 bg-gradient-to-r from-orange-50/90 to-amber-50/60 border border-orange-100 rounded-2xl shadow-2xs">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="p-2.5 bg-orange-500 text-white rounded-xl shadow-xs"><Sun className="w-5 h-5"/></div>
                <div>
                  <h4 className="text-sm font-black text-slate-800">目的地气象：{weatherInfo.condition}</h4>
                  <p className="text-xs text-slate-500 mt-0.5 font-medium">{trafficInfo?.advice || '暂无供应商路况数据，请在出行前重新获取'}</p>
                </div>
              </div>
              <button 
                onClick={() => setShowWeatherForecast(!showWeatherForecast)}
                className="text-xs font-bold text-orange-600 hover:text-orange-700 underline flex items-center gap-1 cursor-pointer"
              >
                {showWeatherForecast ? '收起预报' : '查看一周预报'}
              </button>
            </div>

            <AnimatePresence>
              {showWeatherForecast && (
                <motion.div key="weather-forecast-dropdown" initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden mt-3 pt-3 border-t border-orange-200/60 grid grid-cols-4 gap-2">
                  {weatherInfo.forecast?.map((f: any, fIdx: number) => (
                    <div key={`forecast-card-${fIdx}`} className="bg-white/80 p-2 rounded-xl text-center border border-orange-100/60">
                      <p className="text-[10px] font-bold text-slate-400">{f.day}</p>
                      <p className="text-xs font-black text-slate-700 my-0.5">{f.text}</p>
                      <p className="text-[10px] font-bold text-orange-500">{f.temp}</p>
                    </div>
                  ))}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )}

        {safetyInfo && (
          <div className="mb-8 p-4 bg-gradient-to-r from-green-50/90 to-emerald-50/60 border border-green-100 rounded-2xl shadow-2xs">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="p-2.5 bg-green-500 text-white rounded-xl shadow-xs"><ShieldCheck className="w-5 h-5"/></div>
                <div>
                  <h4 className="text-sm font-black text-slate-800">
                    {targetCity ? `${targetCity}安全态势` : '目的地安全态势'}
                    {safetyInfo.risk_level && (
                      <span className={`ml-2 px-2 py-0.5 rounded-full text-[10px] font-bold ${
                        safetyInfo.risk_level === '低风险' ? 'bg-green-500/20 text-green-700'
                        : safetyInfo.risk_level === '中风险' ? 'bg-yellow-500/20 text-yellow-700'
                        : 'bg-red-500/20 text-red-700'
                      }`}>{safetyInfo.risk_level}</span>
                    )}
                  </h4>
                  <p className="text-xs text-slate-500 mt-0.5 font-medium">
                    {safetyInfo.cii_score !== undefined ? `CII 安全指数：${safetyInfo.cii_score}` : '安全数据加载中'}
                  </p>
                </div>
              </div>
              <button 
                onClick={onGenerateSafetyBrief}
                disabled={briefLoading}
                className="text-xs font-bold text-green-600 hover:text-green-700 flex items-center gap-1.5 bg-green-100/80 px-3 py-1.5 rounded-lg border border-green-200 hover:bg-green-200 transition-colors cursor-pointer disabled:opacity-50"
              >
                {briefLoading ? (
                  <><RefreshCw className="w-3.5 h-3.5 animate-spin"/> 生成中...</>
                ) : (
                  <><BrainCircuit className="w-3.5 h-3.5"/> 生成安全简报</>
                )}
              </button>
            </div>
            {safetyBrief && (
              <motion.div 
                initial={{ height: 0, opacity: 0 }} 
                animate={{ height: 'auto', opacity: 1 }} 
                className="overflow-hidden mt-3 pt-3 border-t border-green-200/60"
              >
                <div className="bg-white/80 p-3 rounded-xl border border-green-100/60">
                  <p className="text-xs text-slate-700 leading-relaxed whitespace-pre-wrap font-medium">{safetyBrief}</p>
                </div>
              </motion.div>
            )}
            {safetyInfo.active_alerts && safetyInfo.active_alerts.length > 0 && (
              <div className="mt-3 pt-3 border-t border-green-200/60">
                <div className="flex flex-wrap gap-1.5">
                  {safetyInfo.active_alerts.map((alert: any, aIdx: number) => (
                    <span key={`alert-${aIdx}`} className="inline-flex items-center gap-1 bg-amber-50 text-amber-700 text-[10px] px-2 py-0.5 rounded-md font-bold border border-amber-200">
                      <AlertCircle className="w-3 h-3"/> {alert?.title || alert?.type || '告警'}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        <h2 className="text-2xl font-black text-slate-800 mb-8 tracking-tight">第 {totalDays[activeDayIndex] || 1} 天 行程安排</h2>

        <div className="relative pl-6">
          <div className="absolute top-3 bottom-6 left-[11px] w-[2px] bg-slate-200/80"></div>

          {Array.isArray(routes) && routes.map((pt: any, idx: number) => {
            const isSelected = selectedPoiIndex === idx;

            const validPhotoUrls = (pt.photos || []).filter((u: string) => Boolean(u && (u.startsWith('http') || u.startsWith('//'))));
            const fallbackImg = getCleanPhotoUrl(pt.map_image || '');

            // 👑 地点详情链接：优先后端回填的 amap_url，缺失时回退到高德关键词搜索，保证"查看详情"永远可点击
            const detailUrl = (pt.amap_url && typeof pt.amap_url === 'string' && pt.amap_url.startsWith('http'))
              ? pt.amap_url
              : `https://www.amap.com/search?query=${encodeURIComponent(String(pt.name || ''))}`;

            return (
              <div key={`poi-node-card-${pt.name}-${idx}`} className="relative mb-10">
                <div 
                  onClick={() => onSelectPoi(idx)}
                  className={`absolute -left-[30px] top-0.5 w-7 h-7 rounded-full flex items-center justify-center text-[12px] font-black cursor-pointer transition-all z-10 ${isSelected ? 'bg-orange-500 text-white shadow-lg shadow-orange-300 ring-4 ring-orange-100 scale-110' : 'bg-white text-slate-500 border-2 border-slate-300 hover:border-orange-400'}`}
                >
                  {idx + 1}
                </div>

                <div className="cursor-pointer group" onClick={() => onSelectPoi(idx)}>
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2.5 flex-wrap">
                      <h3 className={`text-xl font-black transition-colors ${isSelected ? 'text-orange-500' : 'text-slate-800 group-hover:text-orange-500'}`}>{pt.name}</h3>
                      <span className="flex items-center gap-1 bg-orange-50 text-orange-600 px-2 py-0.5 rounded text-xs font-bold border border-orange-100">
                        ⭐ {pt.rating || '暂无供应商数据'}
                      </span>

                      {pt.cost && (
                        <span className="flex items-center gap-1 bg-emerald-50 text-emerald-700 px-2.5 py-0.5 rounded-lg text-xs font-bold border border-emerald-200/80 shadow-2xs">
                          <PiggyBank className="w-3.5 h-3.5 text-emerald-600"/>
                          <span>预估花销：{pt.cost}</span>
                        </span>
                      )}
                    </div>

                    <div className="flex items-center gap-1.5">
                      <button
                        data-testid={`vote-up-${idx}`}
                        onClick={(e) => { e.stopPropagation(); onVote(pt.name, 'up'); }}
                        className={`p-1.5 rounded-lg border text-xs font-bold flex items-center gap-1 transition-colors cursor-pointer ${nodeVotes[pt.name] === 'up' ? 'bg-emerald-500 text-white border-emerald-500' : 'bg-slate-50 hover:bg-emerald-50 text-slate-500 hover:text-emerald-600 border-slate-200'}`}
                        title="赞同该节点安排"
                      >
                        <ThumbsUp className="w-3 h-3" />
                      </button>

                      <button
                        data-testid={`vote-down-${idx}`}
                        onClick={(e) => { e.stopPropagation(); onVote(pt.name, 'down'); }}
                        className={`p-1.5 rounded-lg border text-xs font-bold flex items-center gap-1 transition-colors cursor-pointer ${nodeVotes[pt.name] === 'down' ? 'bg-rose-500 text-white border-rose-500' : 'bg-slate-50 hover:bg-rose-50 text-slate-500 hover:text-rose-600 border-slate-200'}`}
                        title="不满意？点此触发团队平替置换"
                      >
                        <ThumbsDown className="w-3 h-3" />
                      </button>

                      {onSwapPoi && (
                        <button
                          onClick={(e) => { e.stopPropagation(); onSwapPoi(pt.name); }}
                          className="text-xs text-slate-500 hover:text-orange-600 font-bold flex items-center gap-1 bg-slate-100 hover:bg-orange-50 px-2.5 py-1 rounded-lg border border-slate-200 hover:border-orange-200 transition-colors cursor-pointer"
                          title="一键换成同城同类好去处"
                        >
                          <RotateCw className="w-3 h-3" />
                          <span>换一换</span>
                        </button>
                      )}

                      {onSecondaryPlan && (
                        <button
                          onClick={(e) => { e.stopPropagation(); onSecondaryPlan(pt); }}
                          className="flex items-center gap-1 rounded-lg border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-bold text-emerald-700 transition-colors hover:bg-emerald-100"
                          title="规划景区内特色点与步行顺序"
                        >
                          <Footprints className="h-3 w-3" />
                          <span>景区内规划</span>
                        </button>
                      )}

                      {onMarkVisited && (
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); onMarkVisited(pt); }}
                          className={`flex items-center gap-1 rounded-lg border px-2.5 py-1 text-xs font-bold transition-colors ${visitedNodes.includes(`${pt.day || 1}:${pt.name || ''}`) ? 'border-emerald-300 bg-emerald-100 text-emerald-700' : 'border-slate-200 bg-slate-50 text-slate-500 hover:border-emerald-200 hover:bg-emerald-50 hover:text-emerald-700'}`}
                          title="明确标记已到访，写入行程反馈事件"
                        >
                          <CheckCircle2 className="h-3 w-3" />
                          <span>{visitedNodes.includes(`${pt.day || 1}:${pt.name || ''}`) ? '已到访' : '标记到访'}</span>
                        </button>
                      )}

                      <a 
                        href={detailUrl} 
                        target="_blank" 
                        rel="noopener noreferrer" 
                        onClick={(e) => e.stopPropagation()}
                        className="text-xs text-orange-600 hover:text-orange-700 font-bold flex items-center gap-1 bg-orange-50/80 px-2.5 py-1 rounded-lg border border-orange-200/80 hover:bg-orange-100 transition-colors"
                      >
                        <ExternalLink className="w-3.5 h-3.5" />
                        <span>查看详情</span>
                      </a>
                    </div>
                  </div>

                  {pt.time && (
                    <div className="mb-3 inline-flex items-center gap-1.5 text-xs font-bold text-orange-600 bg-orange-50 px-3 py-1 rounded-xl border border-orange-200/80 shadow-2xs">
                      <Clock className="w-3.5 h-3.5 text-orange-500" />
                      <span>建议游玩时段：{pt.time}</span>
                    </div>
                  )}

                  {(pt.open_time || pt.address) && (
                    <div className="mb-3 flex flex-wrap gap-2 text-[11px] font-medium text-slate-500">
                      {pt.open_time && (
                        <span className="inline-flex items-center gap-1 bg-slate-100 px-2.5 py-1 rounded-lg border border-slate-200">
                          <Clock className="w-3 h-3 text-slate-400" /> 开放时间：{pt.open_time}
                        </span>
                      )}
                      {pt.address && (
                        <span className="inline-flex items-center gap-1 bg-slate-100 px-2.5 py-1 rounded-lg border border-slate-200">
                          <MapPin className="w-3 h-3 text-slate-400" /> {pt.address}
                        </span>
                      )}
                    </div>
                  )}

                  {pt.split_info && (
                    <div className="mb-3 p-3 rounded-2xl bg-gradient-to-r from-purple-500/10 to-indigo-500/10 border border-purple-500/30 text-xs">
                      <div className="font-bold text-purple-700 dark:text-purple-400 flex items-center gap-1.5 mb-1">
                        <Split className="w-3.5 h-3.5" />
                        <span>多智能体分合流时空调度：</span>
                      </div>
                      <p className="text-[11px] text-slate-600 dark:text-slate-300 leading-relaxed font-medium">
                        {pt.split_info}
                      </p>
                    </div>
                  )}

                  <div className="grid grid-cols-3 gap-2 mb-4">
                    {(() => {
                      const vPhotos = (pt.photos || []).filter((u: string) => Boolean(getCleanPhotoUrl(u))).map((u: string) => getCleanPhotoUrl(u));
                      const slots: (string | null)[] = [vPhotos[0] || null, vPhotos[1] || null, vPhotos[2] || null];
                      const hasAny = vPhotos.length > 0 || fallbackImg;
                      const handlePhotoClick = () => {
                        if (onPhotoClick) {
                          onPhotoClick({ name: pt.name, type: pt.tags?.[0] || pt.type, city: targetCity });
                        }
                      };

                      if (!hasAny) {
                        return (
                          <div className="col-span-3 rounded-xl overflow-hidden aspect-[4/3]">
                            <PoiImage photos={[]} mapImage="" amapUrl={pt.amap_url} name={pt.name} type={pt.tags?.[0] || pt.type} className="w-full h-full" index={idx} onPhotoClick={handlePhotoClick} />
                          </div>
                        );
                      }
                      return slots.map((src, imgIdx) => (
                        <div key={`img-thumb-${idx}-${imgIdx}`} className={`rounded-xl overflow-hidden bg-slate-100 border border-slate-100 shadow-2xs ${imgIdx === 0 && vPhotos.length === 1 ? 'col-span-3 aspect-[16/10]' : 'aspect-[4/3]'}`}>
                          <PoiImage
                            photos={src ? [src] : []}
                            mapImage={fallbackImg}
                            amapUrl={pt.amap_url}
                            name={pt.name}
                            type={pt.tags?.[0] || pt.type}
                            className="w-full h-full"
                            index={imgIdx === 0 ? idx : undefined}
                            onPhotoClick={handlePhotoClick}
                          />
                        </div>
                      ));
                    })()}
                  </div>

                  <div className="text-sm text-slate-600 leading-relaxed space-y-2.5 mb-4">
                    <p className="leading-loose"><span className="font-bold text-slate-800">体验与文化：</span>{pt.desc}</p>
                    
                    {pt.tags && pt.tags.length > 0 && (
                      <div className="flex flex-wrap gap-1.5 pt-1">
                        {pt.tags.map((tag: string, tIdx: number) => <span key={`tag-pill-${idx}-${tIdx}`} className="bg-orange-50/80 border border-orange-100 text-orange-600 text-[11px] px-2 py-0.5 rounded-md font-bold">{tag}</span>)}
                      </div>
                    )}
                  </div>

                  {(pt.is_hotel || pt.tags?.includes("住宿") || idx === routes.length - 1) && pt.hotel_candidates && pt.hotel_candidates.length > 0 && (
                    <div className="mt-3 p-3.5 rounded-2xl bg-purple-50/80 border border-purple-200/80 text-xs">
                      <div className="font-bold text-purple-900 mb-2 flex items-center justify-between">
                        <span className="flex items-center gap-1.5"><Hotel className="w-4 h-4 text-purple-600" /> 同商圈备选品质酒店：</span>
                      </div>
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                        {pt.hotel_candidates.map((cand: any, cIdx: number) => {
                          const candName = typeof cand === 'string' ? cand : (cand?.name || '酒店');
                          const candPrice = (typeof cand === 'object' && cand?.price) ? cand.price : '';
                          // 👑 稳健生成详情外链：对象无 amap_url 时回退到高德关键词搜索并对中文 URL 编码，杜绝 href=undefined 导致的点击无响应
                          const candUrl = (typeof cand === 'object' && typeof cand?.amap_url === 'string' && cand.amap_url)
                            ? cand.amap_url
                            : `https://www.amap.com/search?query=${encodeURIComponent(candName)}`;
                          const openHotelDetail = (e: React.MouseEvent) => {
                            e.stopPropagation();
                            e.preventDefault();
                            if (candUrl) window.open(candUrl, '_blank', 'noopener,noreferrer');
                          };
                          return (
                            <div key={cIdx} className="bg-white p-2.5 rounded-xl border border-purple-100 flex items-center justify-between shadow-2xs">
                              <span className="font-bold text-slate-800 text-xs truncate max-w-[130px]">{candName} {candPrice && <span className="text-purple-600 font-normal">({candPrice})</span>}</span>
                              <div className="flex items-center gap-1.5 shrink-0">
                                <a 
                                  href={candUrl} 
                                  target="_blank" 
                                  rel="noopener noreferrer" 
                                  onClick={openHotelDetail}
                                  className="px-2 py-1 text-purple-600 hover:bg-purple-50 rounded-md border border-purple-200 text-[11px] font-bold flex items-center gap-0.5 cursor-pointer"
                                >
                                  <ExternalLink className="w-3.5 h-3.5" /> 详情
                                </a>
                                <button 
                                  onClick={(e) => { e.stopPropagation(); onSwapPoi(pt.name); }} 
                                  className="px-2 py-1 bg-purple-600 hover:bg-purple-700 text-white rounded-md text-[11px] font-bold flex items-center gap-0.5 cursor-pointer"
                                >
                                  <RotateCw className="w-3.5 h-3.5" /> 置换
                                </button>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>

                {idx < routes.length - 1 && (
                  <ExpandableConnectorNav 
                    pt={pt} 
                    nextPt={routes[idx + 1]} 
                    travelDetails={travelDetails} 
                  />
                )}

              </div>
            );
          })}
        </div>

        <div className="mt-8 pt-6 border-t border-slate-100 flex justify-center">
          <button 
            onClick={onReset} 
            className="px-6 py-3 bg-slate-900 border border-slate-800 text-white rounded-xl font-bold text-sm flex items-center gap-2 hover:bg-orange-500 transition-all shadow-md cursor-pointer"
          >
            <Wand2 className="w-4 h-4" /> 对当前路线增量调优
          </button>
        </div>
      </div>
    </div>
  );
}
