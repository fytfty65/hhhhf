'use client';

// 成员专属等候页：通过房间码加入别人房间后看到的页面。
//
// 为什么不直接复用大厅：大厅是「我要去哪」的编辑面，成员此刻要回答的是
// 「房主到哪一步了、我什么时候被带进去」。把这两件事放在同一屏里，
// 成员会误以为要自己在另一套表单里重新填一遍，而不是等同一份方案。
//
// 跟随是默认行为：房主开始规划会自动进入规划页；这里保留开关，
// 想自己先看方案的人可以关掉，再点「先自己规划」回大厅。

import {
  Users, Copy, Check, MapPin, Compass, ArrowRight,
  Radio, Loader2, ThumbsUp, ThumbsDown,
} from 'lucide-react';
import { motion } from 'framer-motion';
import OmniLogo from './OmniLogo';
import RoomConnectionStatus, { type RoomConnectionState } from './RoomConnectionStatus';
import { generatedAvatarUrl } from '../lib/avatarArt';
import { voteCountsByNode, type RoomVoteState } from '../lib/collaborationVotes';
import type { RoomMember, UserProfile } from '../types';

export default function RoomWaitingScreen({
  currentUser,
  roomCode,
  copied,
  onCopyRoomCode,
  roomMembers,
  roomVotes,
  roomConnection,
  roomRetryAttempt,
  roomRetryDelayMs,
  onRetryRoom,
  hostName,
  hostPlanning,
  hostDestination,
  followHost,
  onFollowHostChange,
  onEnterPlanning,
}: {
  currentUser: UserProfile;
  roomCode: string;
  copied: boolean;
  onCopyRoomCode: () => void;
  roomMembers: RoomMember[];
  roomVotes: RoomVoteState;
  roomConnection: RoomConnectionState;
  roomRetryAttempt?: number;
  roomRetryDelayMs?: number;
  onRetryRoom?: () => void;
  hostName: string;
  hostPlanning: boolean;
  hostDestination?: string;
  followHost: boolean;
  onFollowHostChange: (next: boolean) => void;
  onEnterPlanning: () => void;
}) {
  // 只展示「有人投过票」的节点，按票数排序；空房间不渲染一个假的 0 票列表。
  const votedNodes = Object.entries(voteCountsByNode(roomVotes, currentUser.id))
    .map(([node, counts]) => ({ node, ...counts }))
    .sort((a, b) => b.up + b.down - (a.up + a.down))
    .slice(0, 6);

  return (
    <div data-testid="member-waiting-screen" className="min-h-screen w-full bg-slate-50">
      <div className="mx-auto flex min-h-screen w-full max-w-5xl flex-col px-4 py-6 sm:px-8 sm:py-10">
        {/* 顶部：品牌 + 房间码 */}
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <OmniLogo className="h-8 w-8" />
            <div>
              <p className="text-sm font-extrabold text-slate-800">OmniRoute</p>
              <p className="text-[11px] font-medium text-slate-500">协作房间 · 成员等候</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-black tracking-wider text-orange-600">{roomCode || '准备中…'}</span>
            <button
              type="button"
              onClick={onCopyRoomCode}
              className="rounded-lg border border-slate-200 bg-white p-1.5 text-slate-500 transition-colors hover:border-orange-300 hover:text-orange-600"
              title="复制房间码"
            >
              {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
            </button>
          </div>
        </header>

        <main className="mt-6 grid flex-1 grid-cols-1 gap-4 lg:grid-cols-3">
          {/* 房主状态：这一页只回答「房主到哪了」 */}
          <motion.section
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25 }}
            className="lg:col-span-2"
          >
            <div className="flex h-full flex-col rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
              <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-wide text-slate-400">
                <Radio className={`h-3.5 w-3.5 ${hostPlanning ? 'text-orange-500' : 'text-slate-300'}`} />
                房主状态
              </div>

              {hostPlanning ? (
                <>
                  <h1 className="mt-3 flex items-center gap-2 text-xl font-extrabold text-slate-900 sm:text-2xl">
                    <span className="relative flex h-2.5 w-2.5">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-orange-400 opacity-70" />
                      <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-orange-500" />
                    </span>
                    {hostName} 正在规划{hostDestination ? `「${hostDestination}」` : ''}
                  </h1>
                  <p className="mt-2 text-sm font-medium leading-relaxed text-slate-600">
                    方案由房主那边统一生成，完成后会直接同步到这里，你不需要再单独生成一次。
                    {followHost ? '现在会被自动带进同一个规划页。' : '你已关闭自动跟随，可以手动进入。'}
                  </p>
                  <div className="mt-5 flex flex-wrap items-center gap-3">
                    <button
                      type="button"
                      onClick={onEnterPlanning}
                      className="inline-flex items-center gap-2 rounded-xl bg-orange-500 px-4 py-2.5 text-sm font-bold text-white transition-colors hover:bg-orange-600"
                    >
                      进入规划页 <ArrowRight className="h-4 w-4" />
                    </button>
                    <FollowToggle checked={followHost} onChange={onFollowHostChange} />
                  </div>
                </>
              ) : (
                <>
                  <h1 className="mt-3 flex items-center gap-2 text-xl font-extrabold text-slate-900 sm:text-2xl">
                    <Loader2 className="h-5 w-5 animate-spin text-slate-300" />
                    等待房主开始规划
                  </h1>
                  <p className="mt-2 text-sm font-medium leading-relaxed text-slate-600">
                    房主确认行程信息后会开始规划，届时你会自动进入同一个规划页。
                    等待期间可以先把你的想法告诉房主。
                  </p>
                  <div className="mt-5">
                    <FollowToggle checked={followHost} onChange={onFollowHostChange} />
                  </div>
                </>
              )}

              <div className="mt-auto pt-6">
                <RoomConnectionStatus
                  connection={roomConnection}
                  retryAttempt={roomRetryAttempt}
                  retryDelayMs={roomRetryDelayMs}
                  memberCount={roomMembers.length}
                  onRetry={onRetryRoom}
                  testId="room-status-member-waiting"
                />
              </div>
            </div>
          </motion.section>

          {/* 成员与投票：只同步事实，不制造「另一个大厅」 */}
          <section className="flex flex-col gap-4">
            <div className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
              <h2 className="flex items-center gap-2 text-sm font-extrabold text-slate-800">
                <Users className="h-4 w-4 text-orange-500" /> 房间成员
                <span className="text-xs font-bold text-slate-400">{roomMembers.length}</span>
              </h2>
              {roomMembers.length === 0 ? (
                <p className="mt-3 text-xs font-medium text-slate-500">正在同步成员…</p>
              ) : (
                <ul className="mt-3 space-y-2">
                  {roomMembers.map((member) => (
                    <li key={member.id} className="flex items-center gap-2.5">
                      <img
                        src={member.avatarUrl || generatedAvatarUrl(member.avatarSeed || member.name, member.name)}
                        alt=""
                        className="h-8 w-8 shrink-0 rounded-full border border-slate-100 bg-slate-50 object-cover"
                      />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-xs font-bold text-slate-800">
                          {member.name}
                          {member.id === currentUser.id && <span className="ml-1 font-semibold text-slate-400">（我）</span>}
                        </p>
                        <p className="truncate text-[11px] font-medium text-slate-500">{member.role}</p>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {votedNodes.length > 0 && (
              <div className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
                <h2 className="flex items-center gap-2 text-sm font-extrabold text-slate-800">
                  <MapPin className="h-4 w-4 text-orange-500" /> 成员票数
                </h2>
                <ul className="mt-3 space-y-2">
                  {votedNodes.map((row) => (
                    <li key={row.node} className="flex items-center justify-between gap-2">
                      <span className="min-w-0 truncate text-xs font-semibold text-slate-700">{row.node}</span>
                      <span className="flex shrink-0 items-center gap-2 text-[11px] font-bold">
                        <span className="inline-flex items-center gap-0.5 text-emerald-600">
                          <ThumbsUp className="h-3 w-3" /> {row.up}
                        </span>
                        <span className="inline-flex items-center gap-0.5 text-rose-500">
                          <ThumbsDown className="h-3 w-3" /> {row.down}
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        </main>

        <footer className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-4">
          <p className="text-[11px] font-medium text-slate-500">
            加入的是 {hostName} 的房间，方案与修改都发生在同一份行程上。
          </p>
          <p className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-400">
            <Compass className="h-3.5 w-3.5" /> 房主开始后自动进入，不需要再单独规划
          </p>
        </footer>
      </div>
    </div>
  );
}

function FollowToggle({ checked, onChange }: { checked: boolean; onChange: (next: boolean) => void }) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-xs font-semibold text-slate-600">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="h-3.5 w-3.5 rounded accent-orange-500"
      />
      跟随房主自动进入
    </label>
  );
}
