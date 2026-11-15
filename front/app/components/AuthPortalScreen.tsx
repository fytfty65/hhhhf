'use client';

import React, { useId, useState } from 'react';
import { AlertCircle, ArrowRight, CheckCircle2, Compass, Lock, RefreshCw, User } from 'lucide-react';
import { apiFetch, setAccessToken } from '../lib/apiClient';
import OmniLogo from './OmniLogo';
import JourneyEditorial from './JourneyEditorial';
import { useJourneySeason } from './JourneySeasonProvider';
import SeasonalExperience from './spatial/SeasonalExperience';
import SeasonControls from './spatial/SeasonControls';
import SeasonParticles from './spatial/SeasonParticles';
import { useSeasonController } from './spatial/useSeasonController';
import { SEASON_LABELS, type Season } from '../lib/season';
import styles from './AuthPortalScreen.module.css';

/** Seasonal sign-in portal. Authentication contracts and test ids stay stable. */
export default function AuthPortalScreen({ onLoginSuccess }: { onLoginSuccess: (user: any) => void }) {
  const { enterSeason } = useJourneySeason();
  const [tab, setTab] = useState<'login' | 'register'>('login');
  const [displayedSeason, setDisplayedSeason] = useState<Season>('spring');
  const [inputFocused, setInputFocused] = useState(false);
  const [assetStatus, setAssetStatus] = useState<'loading' | 'ready' | 'fallback'>('loading');
  const [assetRetry, setAssetRetry] = useState(0);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [nickname, setNickname] = useState('');
  const [errorMsg, setErrorMsg] = useState('');
  const [successMsg, setSuccessMsg] = useState('');
  const [loading, setLoading] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetToken, setResetToken] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [resetRequested, setResetRequested] = useState(false);
  const { season, mode, motionEnabled, effectiveMotion, setSeason, setMode, setMotionEnabled } =
    useSeasonController(inputFocused || loading || resetOpen);
  const nicknameId = useId();
  const usernameId = useId();
  const passwordId = useId();
  const resetTokenId = useId();
  const newPasswordId = useId();

  const requestPasswordReset = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg('');
    setSuccessMsg('');
    setLoading(true);
    try {
      const res = await apiFetch('/api/auth/password/forgot', {
        method: 'POST', auth: false,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.trim() }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || data?.message || `请求失败 (HTTP ${res.status})`);
      setResetRequested(true);
      if (data?.reset_token) {
        setResetToken(data.reset_token);
        setSuccessMsg('开发环境已返回重置令牌，请设置新密码');
      } else {
        setSuccessMsg(data?.message || '如果账号存在，重置邮件已发送，请查收后填入令牌');
      }
    } catch (err: any) {
      setErrorMsg(err.message || '找回密码请求失败');
    } finally { setLoading(false); }
  };

  const submitPasswordReset = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg('');
    setSuccessMsg('');
    setLoading(true);
    try {
      const res = await apiFetch('/api/auth/password/reset', {
        method: 'POST', auth: false,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: resetToken.trim(), new_password: newPassword.trim() }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || data?.message || `重置失败 (HTTP ${res.status})`);
      setResetOpen(false);
      setResetRequested(false);
      setResetToken('');
      setNewPassword('');
      setPassword('');
      setSuccessMsg('密码已重置，请使用新密码登录');
    } catch (err: any) {
      setErrorMsg(err.message || '密码重置失败');
    } finally { setLoading(false); }
  };

  // Guest accounts are real server accounts; no promise of later account migration.
  const startTrial = async () => {
    setErrorMsg('');
    setSuccessMsg('');
    setLoading(true);
    const suffix = Math.random().toString(36).slice(2, 8) || String(Date.now()).slice(-6);
    const credentials = {
      username: `guest_${suffix}`,
      password: `tour-${suffix}-${Date.now()}`,
      nickname: `游客 ${suffix.slice(0, 4).toUpperCase()}`,
    };
    try {
      const reg = await apiFetch('/api/auth/register', {
        method: 'POST', auth: false,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(credentials),
      });
      const regText = await reg.text().catch(() => '');
      let regData: any = {};
      try { regData = JSON.parse(regText); } catch { regData = {}; }
      if (!reg.ok) {
        throw new Error(regData?.detail || regData?.message || regData?.error || `试用账号创建失败 (HTTP ${reg.status})`);
      }
      const login = await apiFetch('/api/auth/login', {
        method: 'POST', auth: false,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(credentials),
      });
      const loginText = await login.text().catch(() => '');
      let data: any = null;
      try { data = JSON.parse(loginText); } catch { data = null; }
      if (!login.ok || !data?.user) {
        throw new Error(data?.detail || data?.message || data?.error || `试用登录失败 (HTTP ${login.status})`);
      }
      setAccessToken(data.token);
      enterSeason(displayedSeason);
      onLoginSuccess({ ...data.user });
    } catch (err: any) {
      setErrorMsg(err.message || '试用入口暂时不可用，请稍后再试或直接注册');
    } finally { setLoading(false); }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg('');
    setSuccessMsg('');
    setLoading(true);
    const endpoint = tab === 'login' ? '/api/auth/login' : '/api/auth/register';
    try {
      const res = await apiFetch(endpoint, {
        method: 'POST', auth: false,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.trim(), password: password.trim(), nickname: nickname.trim() || username.trim() }),
      });
      // Read text first so non-JSON gateway failures become usable form errors.
      const rawText = await res.text();
      let data: any = null;
      try { data = JSON.parse(rawText); } catch {
        throw new Error(`后端认证中枢响应异常 (HTTP ${res.status}): ${rawText.slice(0, 90) || '未返回有效 JSON 数据，请检查 Go 网关服务'}`);
      }
      if (!res.ok) {
        throw new Error(data?.detail || data?.message || data?.error || `请求失败 (HTTP ${res.status})，请检查账号密码`);
      }
      if (!data || !data.user) throw new Error('认证响应格式异常，缺少 user 数据对象');
      if (tab === 'register') {
        setSuccessMsg('注册成功，请使用新账号登录');
        setTab('login');
        setPassword('');
        setNickname('');
        return;
      }
      // Access token stays in memory; refresh token is an HttpOnly cookie.
      setAccessToken(data.token);
      enterSeason(displayedSeason);
      onLoginSuccess({ ...data.user });
    } catch (err: any) {
      setErrorMsg(err.message || '连接认证中枢失败，请检查网关服务与 API_BASE 配置');
    } finally { setLoading(false); }
  };

  return (
    <div className={styles.portal} data-season={displayedSeason} data-journey-season={displayedSeason}>
      <div className={styles.stage}>
      <div className={styles.landscape}>
        <SeasonalExperience season={season} motionEnabled={effectiveMotion} onDisplayedSeason={setDisplayedSeason} onAssetStatus={setAssetStatus} retryToken={assetRetry} />
        <SeasonParticles season={displayedSeason} enabled={effectiveMotion} />
        <div aria-hidden="true" className={styles.scrim} />
      </div>
      <header className={styles.header}>
        <div className={styles.brand}>
          <OmniLogo className="w-10 h-10" />
          <div><p className={styles.brandName}>OmniRoute</p><p className={styles.brandTagline}>云上驿站 · 一起出发</p></div>
        </div>
        <nav className={styles.nav} aria-label="主导航">
          <a href="#portal-story">旅程序章</a><a href="#journey-guide">关于同行</a><a href="#portal-season">四季灵感</a>
        </nav>
        <div className={styles.headerActions}><span className={styles.collaboration}><span />开放体验 · {SEASON_LABELS[displayedSeason].name}</span><a className={styles.talkButton} href="#portal-login">开始规划 <ArrowRight size={14} /></a></div>
      </header>
      <main className={styles.main}>
        <section id="portal-story" className={styles.story} aria-labelledby="portal-headline">
          <p className={styles.eyebrow}>A WORLD TO EXPLORE. A JOURNEY TO SHARE.</p>
          <h1 id="portal-headline" className={styles.headline}>把向往的远方，<br />走成<em>我们的故事。</em></h1>
          <p className={styles.description}>把目的地、时间、预算和同行人的期待，<br />汇成一份可以继续修改和执行的行程。</p>
          <a href="#portal-login" className={styles.storyAction}>开启下一段旅程 <ArrowRight size={17} /></a>
          <div className={styles.seasonCaption}><span className={styles.seasonIndicator} /><span>{mode === 'date' ? '随日期 · 当季风景' : mode === 'demo' ? (effectiveMotion ? '四季循环 · 每 24 秒换景' : '四季循环 · 已暂停') : '手动选季'} · {SEASON_LABELS[displayedSeason].name}</span></div>
          <div id="portal-season" className={styles.controls}><SeasonControls season={displayedSeason} mode={mode} motionEnabled={motionEnabled} onSeasonChange={setSeason} onModeChange={setMode} onMotionChange={setMotionEnabled} /></div>
        </section>
        <section id="portal-login" className={styles.panel} aria-label="旅行者账号"
          onFocusCapture={(event) => { if (event.target instanceof HTMLInputElement) setInputFocused(true); }}
          onBlurCapture={(event) => {
            if (!(event.relatedTarget instanceof HTMLInputElement) || !event.currentTarget.contains(event.relatedTarget)) setInputFocused(false);
          }}>
          <div className={styles.panelInner}>
            <div className={styles.panelHeading}>
              <div className={styles.accountMark}><Compass size={22} strokeWidth={1.25} /><span>OmniRoute <i>/ 旅行者账号</i></span></div>
              <h2>{tab === 'login' ? '好久不见。' : '认识一下。'}</h2>
              <p>{tab === 'login' ? '登录，接着安排你的下一次出发。' : '创建账号，和朋友一起把行程安排好。'}</p>
            </div>
            <div className={styles.tabs}>
              <button data-testid="auth-login-tab" className={styles.tab} aria-pressed={tab === 'login'}
                onClick={() => { setTab('login'); setErrorMsg(''); setSuccessMsg(''); }}>登录</button>
              <button data-testid="auth-register-tab" className={styles.tab} aria-pressed={tab === 'register'}
                onClick={() => { setTab('register'); setErrorMsg(''); setSuccessMsg(''); }}>注册</button>
            </div>
            {errorMsg && <div className={styles.error} role="alert"><AlertCircle size={16} /><span>{errorMsg}</span></div>}
            {successMsg && <div className={styles.success} role="status"><CheckCircle2 size={16} /><span>{successMsg}</span></div>}
            {!resetOpen && (
              <form onSubmit={handleSubmit} className={styles.form}>
                {tab === 'register' && <div>
                  <label htmlFor={nicknameId}>旅行者昵称</label>
                  <div className={styles.field}><User size={16} /><input data-testid="auth-nickname" id={nicknameId} type="text" required autoComplete="nickname" placeholder="朋友怎么称呼你" value={nickname} onChange={(e) => setNickname(e.target.value)} /></div>
                </div>}
                <div>
                  <label htmlFor={usernameId}>用户名 / 账号</label>
                  <div className={styles.field}><User size={16} /><input data-testid="auth-username" id={usernameId} type="text" required autoComplete="username" placeholder="你的用户名" value={username} onChange={(e) => setUsername(e.target.value)} /></div>
                </div>
                <div>
                  <label htmlFor={passwordId}>密码</label>
                  <div className={styles.field}><Lock size={16} /><input data-testid="auth-password" id={passwordId} type="password" required autoComplete={tab === 'login' ? 'current-password' : 'new-password'} placeholder="输入密码" value={password} onChange={(e) => setPassword(e.target.value)} /></div>
                </div>
                {tab === 'login' && <button type="button" data-testid="auth-forgot" className={styles.textButton} onClick={() => { setResetOpen(true); setErrorMsg(''); setSuccessMsg(''); }}>忘记密码？</button>}
                <button data-testid="auth-submit" type="submit" disabled={loading} className={styles.primaryButton}>
                  {loading ? <><RefreshCw size={16} className="animate-spin" /> 请稍候…</> : <>{tab === 'login' ? '登录' : '创建账号'} <ArrowRight size={16} /></>}
                </button>
              </form>
            )}
            {!resetOpen && <div className={styles.trial}>
              <button type="button" data-testid="auth-trial" onClick={startTrial} disabled={loading} className={styles.trialButton}>先以游客身份看看 <ArrowRight size={14} /></button>
              <details className={styles.guestNote}><summary>关于游客模式</summary><p>会为你创建临时账号。行程仅保存在该账号下，不支持跨设备找回；长期保存行程请注册账号。</p></details>
            </div>}
            {resetOpen && <div className={styles.reset}>
              <div><h3>找回密码</h3><p>先提交账号获取重置令牌；生产环境令牌会发送到绑定邮箱，开发环境直接显示。</p></div>
              {!resetRequested ? (
                <form onSubmit={requestPasswordReset} className={styles.form}>
                  <div><label htmlFor={usernameId}>用户名 / 账号</label><div className={styles.field}><User size={16} /><input id={usernameId} data-testid="reset-username" type="text" required autoComplete="username" placeholder="输入要找回的账号" value={username} onChange={(e) => setUsername(e.target.value)} /></div></div>
                  <button type="submit" data-testid="reset-request" disabled={loading} className={styles.primaryButton}>{loading ? '正在提交…' : '获取重置令牌'}</button>
                </form>
              ) : (
                <form onSubmit={submitPasswordReset} className={styles.form}>
                  <div><label htmlFor={resetTokenId}>重置令牌</label><input className={styles.plainInput} id={resetTokenId} data-testid="reset-token" type="text" required placeholder="粘贴邮件中的重置令牌" value={resetToken} onChange={(e) => setResetToken(e.target.value)} /></div>
                  <div><label htmlFor={newPasswordId}>新密码</label><div className={styles.field}><Lock size={16} /><input id={newPasswordId} data-testid="reset-new-password" type="password" required autoComplete="new-password" placeholder="输入新密码" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} /></div></div>
                  <button type="submit" data-testid="reset-submit" disabled={loading || !resetToken.trim() || !newPassword.trim()} className={styles.primaryButton}>{loading ? '正在重置…' : '设置新密码'}</button>
                </form>
              )}
              <button type="button" className={styles.textButton} onClick={() => { setResetOpen(false); setResetRequested(false); setErrorMsg(''); setSuccessMsg(''); }}>返回登录</button>
            </div>}
          </div>
        </section>
      </main>
      <footer className={styles.footer}>
        <span className={styles.footerNote}>山川有时，同行有你。</span>
        <div className={styles.footerMeta}><span className={styles.openDot} />当前开放 · {SEASON_LABELS[displayedSeason].hint}<span className={styles.scrollHint}>向下探索 <i /></span></div>
        {assetStatus === 'fallback' ? <button type="button" className={styles.textButton} onClick={() => setAssetRetry(value => value + 1)}>场景暂未加载 · 重试</button> : <p className={styles.footerNote}>与四季同行 · {SEASON_LABELS[displayedSeason].name}</p>}
      </footer>
      </div>
      <JourneyEditorial />
    </div>
  );
}
