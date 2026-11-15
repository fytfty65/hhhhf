'use client';

import { ArrowUpRight, Route, Users, ShieldCheck } from 'lucide-react';
import styles from './JourneyEditorial.module.css';

const chapters = [
  { icon: Users, number: '01', title: '不同期待，一起抵达。', text: '邀请同行人加入房间，把每个人的预算、节奏与必去清单，放进同一份计划。' },
  { icon: Route, number: '02', title: '把灵感，变成路线。', text: '让 AI 协助梳理交通、住宿与游玩安排。生成后继续调整，让计划更像你。' },
  { icon: ShieldCheck, number: '03', title: '计划之外，也有准备。', text: '关注旅途中的天气与风险，按需调整行程；价格与数据来源清晰可查。' },
];

export default function JourneyEditorial() {
  return <section id="journey-guide" className={styles.editorial} aria-labelledby="journey-title">
    <div className={styles.heading}><p>LESS PLANNING. MORE WANDERING.</p><span>THE OMNIROUTE WAY / 01</span></div>
    <div className={styles.intro}><h2 id="journey-title">世界很大，<br />一起出发就好。</h2><p>旅行的意义，从来不只在目的地。<br />把繁琐留给规划，把时间留给彼此。</p><a href="#portal-login">写下你的下一站 <ArrowUpRight size={20} /></a></div>
    <div className={styles.chapters}>{chapters.map(({ icon: Icon, number, title, text }) => <article key={number}><div className={styles.chapterTop}><Icon size={25} strokeWidth={1.3} /><span>{number}</span></div><h3>{title}</h3><p>{text}</p></article>)}</div>
    <div className={styles.closing}><span>OmniRoute® · 为每一种出发而设计</span><a href="/capabilities">了解平台能力与数据来源 <ArrowUpRight size={14} /></a><span>GO TOGETHER. GO FURTHER.</span></div>
  </section>;
}

export { default as JourneyLobbyHero } from './SeasonJourneyBanner';
