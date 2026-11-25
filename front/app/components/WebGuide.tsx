'use client';

import { useEffect, useId, useRef, useState, type KeyboardEvent, type WheelEvent } from 'react';
import { createPortal } from 'react-dom';
import { ArrowLeft, ArrowRight, ArrowUpRight, BookOpen, CalendarDays, Compass, Footprints, LocateFixed, Map, MapPin, PencilLine, Route, Search, Users, X } from 'lucide-react';
import { JOURNEY_SEASONS, JOURNEY_TRAILS } from '../lib/journeySeason';
import { SEASONS, type Season } from '../lib/season';
import { PROVINCE_DATA } from '../data/provinceData';
import { useJourneySeason } from './JourneySeasonProvider';
import { seasonIcons } from './JourneySeasonSelect';
import OmniLogo from './OmniLogo';
import styles from './WebGuide.module.css';

type GuideStep = {
  title: string;
  detail: string;
  action?: { target: string; label: string };
};

type GuideChapter = { title: string; intro: string; steps: GuideStep[]; example?: string };

const chapters = {
  portal: {
    title: '登录与体验',
    intro: '有账号就登录。想先看看？访客也能试着安排一趟旅行。',
    steps: [
      { title: '进入规划大厅', detail: '在账号区点「先以游客身份看看」，就能进入大厅。', action: { target: 'portal-account', label: '前往账号区' } },
      { title: '把行程带到下次旅行', detail: '游客行程保存在临时账号下。需要长期保存或换设备找回时，使用正式账号。' },
    ],
  },
  lobby: {
    title: '开始规划',
    intro: '先定去哪、玩几天、和谁去，再想想这趟旅程最在意什么。',
    steps: [
      { title: '填写目的地、天数和人数', detail: '输入想去的城市，再用加减按钮调整天数和人数。', action: { target: 'destination', label: '去填目的地' } },
      { title: '写下必须满足的条件', detail: '点「修改」，写下必去的地方、想放慢的节奏或饮食禁忌。', action: { target: 'preferences', label: '定位到「修改」' } },
      { title: '确认偏好，继续规划', detail: '点「生成方案」。选好偏好，再点「查看规划方案」，就可以继续补充需求。', action: { target: 'begin-plan', label: '定位到「生成方案」' } },
    ],
  },
  room: {
    title: '和朋友同行',
    intro: '通过同一个房间一起规划，让同行人看到同一份方案。',
    steps: [
      { title: '分享或输入房间码', detail: '房间准备就绪后，复制房间码发给朋友。收到邀请的人在大厅输入房间码，再点「加入」。', action: { target: 'room-code', label: '去房间区域' } },
      { title: '跟随房主一起看方案', detail: '房主负责生成方案。打开「跟随房主」，房主开始规划后，同行人会看到同一份行程。' },
    ],
  },
  draft: {
    title: '生成行程',
    intro: '把大厅带过来的需求检查一遍，再补上预算、必去地点和想要的旅行节奏。',
    example: '我们两个人去成都玩 3 天，总预算 3000 元。想去熊猫基地，每天留出午休，尽量少换酒店。',
    steps: [
      { title: '把需求写具体', detail: '用一句话写清去哪、玩几天、总预算，以及哪项安排对你最重要。', action: { target: 'intent', label: '去写旅行需求' } },
      { title: '生成并等待方案', detail: '点「生成行程方案」开始生成。调整已有方案时，按钮会显示「应用调整」。', action: { target: 'submit-intent', label: '定位到提交按钮' } },
    ],
  },
  result: {
    title: '查看、调整与保存',
    intro: '拿到行程后，先核对每天去哪里、预计花费和交通时间，再决定哪些安排需要调整。',
    steps: [
      { title: '看看每天去哪', detail: '在「主方案」看每天的停留点。出发后，到「旅中执行」记录去过的地方。', action: { target: 'plan-nav', label: '定位到行程导航' } },
      { title: '只改不合适的部分', detail: '在方案里改某一天或换一个地点。要改整体安排，点「更多」里的「重新调整」。', action: { target: 'plan-tools', label: '定位到工具栏「更多」' } },
      { title: '保存行程或带到日历', detail: '点「保存到我的行程」，以后在「我的路书」找。也可以在工具栏直接或通过「更多」导出日历。', action: { target: 'save-plan', label: '定位到保存按钮' } },
    ],
  },
  profile: {
    title: '找到我的路书',
    intro: '在大厅「更多 → 个人主页」查看已保存的行程。',
    steps: [
      { title: '搜索、查看或导出', detail: '在「我的路书」搜索目的地或标题。展开某本路书能查看每日安排，也可以导出日历或旅行日记。', action: { target: 'trip-search', label: '去我的路书' } },
      { title: '分享走过的路线', detail: '确认想分享的内容后，在对应路书上点「分享」，让社区旅友也能看到。' },
    ],
  },
  community: {
    title: '浏览社区',
    intro: '从大厅「更多 → 社区」打开旅友分享的路线。',
    steps: [
      { title: '找灵感，带回自己的规划', detail: '按目的地或标签搜索。展开感兴趣的路线，确认合适后，可以把它作为自己规划的起点。', action: { target: 'community-search', label: '去搜索路线' } },
      { title: '发布自己的路书', detail: '用「发布路书」填写要分享的内容。也可以在个人主页从已保存的路书发起分享。' },
    ],
  },
} satisfies Record<string, GuideChapter>;

type ChapterId = keyof typeof chapters;
const chapterIds = Object.keys(chapters) as ChapterId[];

const INVITATION_KEY = 'omni_web_guide_invitation_dismissed';
const LOBBY_TARGETS = ['destination', 'preferences', 'begin-plan', 'room-code'] as const;
export type LobbyGuideTarget = (typeof LOBBY_TARGETS)[number];
const isLobbyTarget = (target: string): target is LobbyGuideTarget => LOBBY_TARGETS.some(id => id === target);
const number = (index: number) => String(index + 1).padStart(2, '0');
const chapterCount = String(chapterIds.length).padStart(2, '0');

function GuideIllustration({ chapter }: { chapter: ChapterId }) {
  if (chapter === 'portal') return <div className={styles.illustration} aria-hidden="true"><div className={styles.miniCard}><span className={styles.miniFields}><i /><i /></span><span>旅行者账号</span><b>先以游客身份看看 <ArrowRight size={13} /></b></div></div>;
  if (chapter === 'room') return <div className={styles.illustration} aria-hidden="true"><div className={styles.miniCard}><Users size={18} /><span>加入房间</span><code>MVAPZ3</code><span className={styles.connected}><i /> 协作已连接</span></div></div>;
  if (chapter === 'community') return <div className={styles.illustration} aria-hidden="true"><div className={styles.miniCard}><span className={styles.miniMap}><Map size={25} /><MapPin size={12} /></span><span>打开交互式地图</span><b>{Object.keys(PROVINCE_DATA).length} 个目的地</b></div></div>;

  const paths = {
    lobby: [{ icon: MapPin, label: '选目的地' }, { icon: Users, label: '天数和人数' }, { icon: ArrowUpRight, label: '生成方案' }],
    draft: [{ icon: PencilLine, label: '写下需求' }, { icon: Route, label: '生成行程' }],
    result: [{ icon: Footprints, label: '查看每天' }, { icon: PencilLine, label: '调整安排' }, { icon: CalendarDays, label: '保存行程' }],
    profile: [{ icon: BookOpen, label: '我的路书' }, { icon: Search, label: '搜索行程' }, { icon: CalendarDays, label: '导出日历' }],
  };
  return <div className={styles.illustration} aria-hidden="true">
    {paths[chapter].map(({ icon: Icon, label }, i) => <div className={styles.miniStage} key={label}>
      {i > 0 && <ArrowRight className={styles.miniArrow} size={15} />}
      <span><Icon size={16} />{label}</span>
    </div>)}
  </div>;
}

function findTarget(target: string) {
  return [...document.querySelectorAll<HTMLElement>(`[data-web-guide="${target}"]`)].find(element =>
    element.getClientRects().length > 0 && !element.matches(':disabled'));
}

const prefersStill = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function foldedPosition(book: HTMLDialogElement) {
  const rect = book.getBoundingClientRect();
  const x = window.innerWidth - rect.left - rect.width / 2 - 102;
  const y = window.innerHeight - rect.top - rect.height / 2 - 48;
  return { transform: `translate(${x}px,${y}px) scale(.18)`, opacity: .08 };
}

const OPEN_BOOK = { transform: 'translate(0,0) scale(1)', opacity: 1 };

type WebGuideProps = {
  startAt: ChapterId;
  canJump?: boolean;
  invitationHost?: HTMLElement | null;
  handoffTarget?: LobbyGuideTarget | null;
  onHandoff?: () => void;
  portal?: { season: Season; changeSeason: (season: Season) => void; enterAsGuest: (target: LobbyGuideTarget) => Promise<boolean> };
};

export default function WebGuide({ startAt, canJump = true, invitationHost, handoffTarget, onHandoff, portal }: WebGuideProps) {
  const journey = useJourneySeason();
  const season = portal?.season ?? journey.season;
  const story = JOURNEY_SEASONS[season];
  const SeasonIcon = seasonIcons[season];
  const dialog = useRef<HTMLDialogElement>(null);
  const launcher = useRef<HTMLButtonElement>(null);
  const scrollArea = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const wheel = useRef({ distance: 0, last: 0, locked: 0 });
  const spotlight = useRef<{ element: HTMLElement; timer: number } | null>(null);
  const titleId = useId();
  const [chapterId, setChapterId] = useState(startAt);
  const [visited, setVisited] = useState<Set<ChapterId>>(() => new Set());
  const [showInvitation, setShowInvitation] = useState(false);
  const [docked, setDocked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [photoReady, setPhotoReady] = useState(false);
  const index = chapterIds.indexOf(chapterId);
  const chapter: GuideChapter = chapters[chapterId];

  useEffect(() => {
    if (startAt !== 'lobby') return;
    try { setShowInvitation(localStorage.getItem(INVITATION_KEY) !== 'yes'); }
    catch { setShowInvitation(true); }
  }, [startAt]);

  function clearSpotlight() {
    const active = spotlight.current;
    if (!active) return;
    window.clearTimeout(active.timer);
    active.element.removeAttribute('data-guide-spotlight');
    spotlight.current = null;
  }

  function focusTarget(element: HTMLElement, activeSeason: Season) {
    clearSpotlight();
    element.focus({ preventScroll: true });
    element.scrollIntoView({ block: 'center', inline: 'nearest', behavior: prefersStill() ? 'instant' : 'smooth' });
    element.setAttribute('data-guide-spotlight', activeSeason);
    spotlight.current = { element, timer: window.setTimeout(clearSpotlight, 3000) };
  }

  useEffect(() => () => clearSpotlight(), []);

  function dismissInvitation() {
    try { localStorage.setItem(INVITATION_KEY, 'yes'); } catch { /* Still dismiss in memory. */ }
    setShowInvitation(false);
  }

  function chooseChapter(id: ChapterId) {
    setChapterId(id);
    setVisited(previous => previous.has(id) ? previous : new Set(previous).add(id));
    setNotice('');
    scrollArea.current?.scrollTo({ top: 0, behavior: 'instant' });
    wheel.current.distance = 0;
  }

  useEffect(() => {
    if (!handoffTarget) return;
    const frame = window.requestAnimationFrame(() => {
      const element = findTarget(handoffTarget);
      if (element) {
        dismissInvitation();
        chooseChapter(handoffTarget === 'room-code' ? 'room' : 'lobby');
        setDocked(true);
        focusTarget(element, season);
      }
      onHandoff?.();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [handoffTarget, onHandoff, season]);

  function openGuide(resume = false) {
    if (!resume) chooseChapter(startAt);
    if (startAt === 'lobby') dismissInvitation();
    clearSpotlight();
    setPhotoReady(true);
    const book = dialog.current;
    book?.showModal();
    if (resume && book && !prefersStill()) {
      book.animate([foldedPosition(book), OPEN_BOOK], { duration: 330, easing: 'cubic-bezier(.18,1.2,.36,1)' });
    }
    closeButton.current?.focus({ preventScroll: true });
    setDocked(false);
  }

  async function jumpTo(target: string) {
    const book = dialog.current;
    const element = findTarget(target);
    if (!element && portal && isLobbyTarget(target)) {
      setBusy(true);
      setNotice('');
      const entered = await portal.enterAsGuest(target);
      setBusy(false);
      if (!entered) setNotice('暂时无法进入大厅。请稍后再试，或关闭手札后登录。');
      return;
    }
    if (!element || !book?.open) {
      setNotice('这里暂时不能操作。等页面准备好后再来试试。');
      return;
    }
    if (!prefersStill()) {
      setBusy(true);
      const animation = book.animate([OPEN_BOOK, foldedPosition(book)], { duration: 330, easing: 'cubic-bezier(.55,-.12,.25,1)', fill: 'forwards' });
      try { await animation.finished; } catch { return; }
      animation.cancel();
      setBusy(false);
    }
    if (!book.open) return;
    book.close();
    setDocked(true);
    focusTarget(element, season);
  }

  function handleKey(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    let next = index;
    if (event.key === 'ArrowLeft') next--;
    else if (event.key === 'ArrowRight') next++;
    else if (/^[1-7]$/.test(event.key)) next = Number(event.key) - 1;
    else return;
    event.preventDefault();
    event.stopPropagation();
    if (!busy && chapterIds[next]) chooseChapter(chapterIds[next]);
  }

  function handleWheel(event: WheelEvent<HTMLDivElement>) {
    const area = event.currentTarget;
    const state = wheel.current;
    const now = performance.now();
    if (busy || event.deltaY <= 0 || area.scrollTop + area.clientHeight < area.scrollHeight - 2 || !chapterIds[index + 1]) {
      state.distance = 0;
      return;
    }
    if (now < state.locked) return;
    if (now - state.last > 220) state.distance = 0;
    state.last = now;
    state.distance += event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? area.clientHeight : 1);
    if (state.distance < 190) return;
    state.locked = now + 650;
    chooseChapter(chapterIds[index + 1]);
  }

  const canLocate = canJump && (chapterId === startAt || (startAt === 'lobby' && chapterId === 'room'));
  const canEnterLobby = Boolean(portal) && (chapterId === 'lobby' || chapterId === 'room');

  return <>
    <button ref={launcher} type="button" className={`${styles.launcher} ${styles.palette}`} data-book-season={season} onClick={() => openGuide()} aria-haspopup="dialog">
      <BookOpen size={16} aria-hidden="true" /> 新手指南 <SeasonIcon className={styles.launcherSeason} size={14} aria-hidden="true" />
    </button>
    {invitationHost && showInvitation && createPortal(
      <aside className={`${styles.invitation} ${styles.palette}`} aria-label="首次规划提示" data-book-season={season}>
        <span className={styles.invitationIcon}><Compass size={22} aria-hidden="true" /></span>
        <div className={styles.invitationCopy}><strong>第一次规划旅行？</strong><p>从选一座城市开始，跟着指南安排第一份行程。</p></div>
        <div className={styles.invitationActions}>
          <button type="button" className={styles.secondary} onClick={dismissInvitation}>暂不需要</button>
          <button type="button" className={styles.primary} onClick={() => openGuide()} aria-haspopup="dialog">开始引导 <ArrowRight size={16} /></button>
        </div>
      </aside>, invitationHost,
    )}
    <dialog ref={dialog} className={`${styles.dialog} ${styles.palette}`} data-book-season={season} data-journey-season={season}
      onKeyDown={handleKey} onCancel={event => { if (busy) event.preventDefault(); }}
      onClose={() => { if (document.activeElement === document.body) launcher.current?.focus({ preventScroll: true }); }} aria-labelledby={titleId}>
      <div className={styles.spread}>
        <aside className={styles.scenery}>
          {photoReady && <span key={season} className={styles.photo} aria-hidden="true" />}
          <div className={styles.signature}>
            <OmniLogo className={styles.logo} />
            <div>
              <div className={styles.seasonName}><SeasonIcon size={16} aria-hidden="true" />{story.title}</div>
              <p>{story.english} / {story.edition}</p>
              <h2 id={titleId} className={styles.srOnly}>新手指南</h2>
              <p className={styles.guidebook} aria-hidden="true">JOURNEY GUIDEBOOK</p>
            </div>
          </div>
          <nav className={styles.chapters} aria-label="新手指南章节">
            {chapterIds.map((id, i) => <button key={id} type="button" aria-current={chapterId === id ? 'step' : undefined}
              data-visited={visited.has(id)} onClick={() => chooseChapter(id)} disabled={busy} title={chapters[id].title}>
              <span className={styles.station} aria-hidden="true" />
              <span className={styles.chapterNo} aria-hidden="true">{number(i)}</span>
              <span className={styles.chapterLabel}>{chapters[id].title}</span>
            </button>)}
          </nav>
          <div className={styles.sceneryFooter} aria-hidden="true"><p>{JOURNEY_TRAILS[season]}</p><div className={styles.stamp}>GO<small>TOGETHER</small></div></div>
        </aside>

        <div className={styles.paper}>
          <header className={styles.paperHeader}>
            <span className={styles.progress}>CHAPTER {number(index)} / {chapterCount}</span>
            <span className={styles.srOnly} role="status">第 {index + 1} 章，共 {chapterIds.length} 章。{chapter.title}</span>
            <div className={styles.paperActions}>
              <div className={styles.seasons} role="group" aria-label="切换四季">
                {SEASONS.map(id => <button key={id} type="button" className={styles.palette} data-book-season={id} aria-pressed={id === season} title={`${JOURNEY_SEASONS[id].name}季 · ${JOURNEY_SEASONS[id].title}`}
                  aria-label={`${JOURNEY_SEASONS[id].name}季`} onClick={() => (portal?.changeSeason ?? journey.enterSeason)(id)} disabled={busy}><span /></button>)}
              </div>
              <button ref={closeButton} type="button" className={styles.close} aria-label="关闭新手指南" onClick={() => dialog.current?.close()} disabled={busy}><X size={18} /></button>
            </div>
          </header>
          <div ref={scrollArea} className={styles.reading} onWheel={handleWheel}>
            <section key={chapterId} className={styles.chapter}>
              <h3>{chapter.title}</h3>
              <p className={styles.intro}>{chapter.intro}</p>
              <GuideIllustration chapter={chapterId} />
              <ol className={styles.steps}>
                {chapter.steps.map(({ title, detail, action }, i) => <li key={title}>
                  <span className={styles.stepNumber} aria-hidden="true">{i + 1}</span>
                  <div><h4>{title}</h4><p>{detail}</p></div>
                  {action && (canLocate || canEnterLobby) && <button type="button" className={styles.jump} disabled={busy} onClick={() => void jumpTo(action.target)}>
                    <LocateFixed size={14} aria-hidden="true" />{canEnterLobby ? '以游客身份进入并定位' : action.label}<ArrowUpRight size={14} aria-hidden="true" />
                  </button>}
                </li>)}
              </ol>
              {chapter.example && <blockquote className={styles.example}><strong>可以这样写</strong><p>{chapter.example}</p></blockquote>}
              {busy && portal && <p role="status" className={styles.notice}>正在为你打开大厅…</p>}
              {notice && <p role="status" className={styles.notice}>{notice}</p>}
            </section>
          </div>
          <footer className={styles.footer}>
            <div>
              <button type="button" className={styles.secondary} disabled={index === 0 || busy} onClick={() => chooseChapter(chapterIds[index - 1])}><ArrowLeft size={15} /> 上一篇</button>
              <span className={styles.keyboard}><kbd>←</kbd> <kbd>→</kbd> 切换</span>
            </div>
            <button type="button" className={styles.primary} disabled={busy} onClick={() => {
              if (chapterIds[index + 1]) chooseChapter(chapterIds[index + 1]);
              else dialog.current?.close();
            }}>{index + 1 < chapterIds.length ? '下一篇' : '开始这段旅程'} <ArrowRight size={16} /></button>
          </footer>
        </div>
      </div>
    </dialog>
    {docked && createPortal(<button type="button" className={`${styles.dock} ${styles.palette}`} data-book-season={season} aria-haspopup="dialog" onClick={() => openGuide(true)}>
      <BookOpen size={17} />继续指南<span>{number(index)}/{chapterCount}</span><ArrowUpRight size={14} />
    </button>, document.body)}
  </>;
}
