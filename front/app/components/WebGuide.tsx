'use client';

import { useEffect, useId, useRef, useState } from 'react';
import { ArrowDown, ArrowLeft, ArrowRight, BookOpen, Compass, X } from 'lucide-react';
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
    intro: '第一次进 OmniRoute，可以先用游客身份走一遍规划流程。',
    steps: [
      { title: '进入规划大厅', detail: '有账号就登录。需要先试用，点「先以游客身份看看」。', action: { target: 'portal-account', label: '前往账号区' } },
      { title: '把行程带到下次旅行', detail: '游客行程保存在临时账号下。需要长期保存或换设备找回时，使用正式账号。' },
    ],
  },
  lobby: {
    title: '开始规划',
    intro: '先确定去哪、几天、几个人，再告诉我们哪些安排一定要满足。',
    steps: [
      { title: '填写目的地、天数和人数', detail: '输入城市或点选推荐城市，再用加减按钮调整天数和同行人数。', action: { target: 'destination', label: '去填目的地' } },
      { title: '写下必须满足的条件', detail: '在「必须满足什么」点「修改」。例如每天少走一点、必须去某个景点，或者有饮食禁忌。', action: { target: 'preferences', label: '定位到「修改」' } },
      { title: '确认偏好，进入工作区', detail: '点「生成方案」，在弹窗内确认偏好，再点「查看规划方案」。进入工作区后，还可以补充需求。', action: { target: 'begin-plan', label: '定位到「生成方案」' } },
    ],
  },
  room: {
    title: '和朋友同行',
    intro: '通过同一个房间一起规划，让同行人看到同一份方案。',
    steps: [
      { title: '分享或输入房间码', detail: '房间准备就绪后，复制房间码发给朋友。收到邀请的人在大厅输入房间码，再点「加入」。', action: { target: 'room-code', label: '去房间区域' } },
      { title: '跟随房主一起看方案', detail: '房主负责生成方案。成员打开跟随后会进入同一个工作区，也可以在房主开始规划后手动进入。' },
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
      { title: '按天查看路线', detail: '在「主方案」查看每天的停留点。想看行前依据或当天到访进度，使用对应的工作区入口。', action: { target: 'plan-nav', label: '定位到行程导航' } },
      { title: '只改不合适的部分', detail: '可以在方案里调整某一天或替换部分安排。想修改整体需求，在工具栏「更多」中选择「重新调整」。', action: { target: 'plan-tools', label: '定位到工具栏「更多」' } },
      { title: '保存行程或带到日历', detail: '点「保存到我的行程」，之后在个人主页的「我的路书」查找。点「导出日历」可下载日历文件，普通模式下从工具栏「更多」进入。', action: { target: 'save-plan', label: '定位到保存按钮' } },
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

function findTarget(target: string) {
  const elements = document.querySelectorAll<HTMLElement>(`[data-web-guide="${target}"]`);
  return Array.from(elements).find(element => element.getClientRects().length > 0 && !element.matches(':disabled'));
}

export default function WebGuide({ startAt, invitation = false, canJump = true }: { startAt: ChapterId; invitation?: boolean; canJump?: boolean }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const pendingTarget = useRef<string | null>(null);
  const titleId = useId();
  const chapterTitleId = useId();
  const [chapterId, setChapterId] = useState(startAt);
  const [showInvitation, setShowInvitation] = useState(false);
  const [targetStatus, setTargetStatus] = useState('');
  const chapter: GuideChapter = chapters[chapterId];
  const index = chapterIds.indexOf(chapterId);

  useEffect(() => {
    if (!invitation) return;
    try { setShowInvitation(localStorage.getItem(INVITATION_KEY) !== 'yes'); }
    catch { setShowInvitation(true); }
  }, [invitation]);

  function dismissInvitation() {
    try { localStorage.setItem(INVITATION_KEY, 'yes'); } catch { /* The launcher still works. */ }
    setShowInvitation(false);
    document.querySelector<HTMLElement>('[data-web-guide-launcher="lobby"]')?.focus({ preventScroll: true });
  }

  function chooseChapter(id: ChapterId) {
    setChapterId(id);
    setTargetStatus('');
  }

  function openGuide() {
    chooseChapter(startAt);
    dialog.current?.showModal();
  }

  function jumpTo(target: string) {
    if (!findTarget(target)) {
      setTargetStatus('这个入口目前不可用，请完成本页操作后再试。');
      return;
    }
    pendingTarget.current = target;
    dialog.current?.close();
  }

  function handleClose() {
    const id = pendingTarget.current;
    pendingTarget.current = null;
    if (invitation && showInvitation) dismissInvitation();
    if (!id) return;
    const target = findTarget(id);
    target?.scrollIntoView({ block: 'center', inline: 'nearest' });
    target?.focus({ preventScroll: true });
  }

  if (invitation && !showInvitation) return null;

  return <>
    {invitation ? (
      <aside className={styles.invitation} aria-label="首次规划提示">
        <span className={styles.invitationIcon}><Compass size={23} aria-hidden="true" /></span>
        <div className={styles.invitationCopy}>
          <strong>第一次规划旅行？</strong>
          <p>从填写目的地开始，跟着指南生成并保存第一份行程。</p>
        </div>
        <div className={styles.invitationActions}>
          <button type="button" className={styles.secondary} onClick={dismissInvitation}>暂不需要</button>
          <button type="button" className={styles.primary} onClick={openGuide} aria-haspopup="dialog">开始引导 <ArrowRight size={16} aria-hidden="true" /></button>
        </div>
      </aside>
    ) : (
      <button type="button" className={styles.launcher} data-web-guide-launcher={startAt} onClick={openGuide} aria-haspopup="dialog"><BookOpen size={16} aria-hidden="true" /> 新手指南</button>
    )}
    <dialog ref={dialog} className={styles.dialog} onClose={handleClose} aria-labelledby={titleId}>
      <header className={styles.dialogHeader}>
        <div className={styles.dialogBrand}><Compass size={19} aria-hidden="true" /><h2 id={titleId}>新手指南</h2></div>
        <button type="button" className={styles.close} onClick={() => dialog.current?.close()} aria-label="关闭新手指南"><X size={20} /></button>
      </header>
      <nav className={styles.chapters} aria-label="新手指南章节">
        {chapterIds.map(id => (
          <button key={id} type="button" aria-pressed={chapterId === id} onClick={() => chooseChapter(id)}>{chapters[id].title}</button>
        ))}
      </nav>
      <section className={styles.chapter} aria-labelledby={chapterTitleId}>
        <h3 id={chapterTitleId} aria-live="polite">{chapter.title}</h3>
        <p className={styles.intro}>{chapter.intro}</p>
        {chapter.example && <blockquote className={styles.example}><strong>可以这样写</strong><p>{chapter.example}</p></blockquote>}
        <ol className={styles.steps}>
          {chapter.steps.map((step, stepIndex) => (
            <li key={step.title}>
              <span className={styles.stepNumber} aria-hidden="true">{stepIndex + 1}</span>
              <div>
                <h4>{step.title}</h4>
                <p>{step.detail}</p>
                {canJump && step.action && (chapterId === startAt || (startAt === 'lobby' && chapterId === 'room')) && (
                  <button type="button" className={styles.jump} onClick={() => jumpTo(step.action.target)}>{step.action.label} <ArrowDown size={14} aria-hidden="true" /></button>
                )}
              </div>
            </li>
          ))}
        </ol>
        {targetStatus && <p role="status" className={styles.notice}>{targetStatus}</p>}
      </section>
      <footer className={styles.footer}>
        <button type="button" className={styles.secondary} disabled={index === 0} onClick={() => chooseChapter(chapterIds[index - 1])}><ArrowLeft size={15} aria-hidden="true" /> 上一篇</button>
        {index + 1 < chapterIds.length ? (
          <button type="button" className={styles.primary} onClick={() => chooseChapter(chapterIds[index + 1])}>下一篇 <ArrowRight size={15} aria-hidden="true" /></button>
        ) : (
          <button type="button" className={styles.primary} onClick={() => dialog.current?.close()}>完成</button>
        )}
      </footer>
    </dialog>
  </>;
}
