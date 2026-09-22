'use client';

import React, { useEffect, useRef, useState, useMemo, useCallback } from 'react';
import { createPortal } from 'react-dom';
import Globe from 'react-globe.gl';
import {
  ShieldCheck, AlertTriangle, X, Activity, RotateCw, Radar,
  Thermometer, CloudRain, Globe2, Eye, Siren,
  Navigation, CheckCircle2, Info, Target,
  Clock, Building2, DollarSign
} from 'lucide-react';

import {
  RADAR_FONT,
  riskProfile,
  parseCostNumber,
  useRadarTheme,
  radarPalette,
  radarPanelStyle,
  radarBackground,
  radarSharedCss,
  radarGlobeImage,
} from '../lib/radarTheme';
import { fetchRiskSnapshot } from '../lib/riskSnapshot';
import { validLngLat } from '../lib/mapTiles';
import RiskRadarChart from './RiskRadarChart';
import DraggablePanel from './DraggablePanel';

export interface SafetyAlert {
  type: string;
  level: 'LOW' | 'MEDIUM' | 'HIGH';
  title: string;
  detail: string;
  position?: [number, number];
}

export interface SafetyInfo {
  city?: string;
  cii_score?: number;
  risk_level?: 'LOW' | 'MEDIUM' | 'HIGH';
  active_alerts?: SafetyAlert[];
  safety_advice?: string;
  crime_score?: number;
  weather_score?: number;
  political_score?: number;
  health_score?: number;
  traffic_score?: number;
  source?: string;
  is_estimated?: boolean;
}

export interface RoutePoint {
  name: string;
  lnglat: [number, number];
  desc?: string;
  time?: string;
  transport?: string;
  cost?: string;
  tags?: string[];
  riskScore?: number;
}

interface WorldSafetyGlobeProps {
  targetCity: string;
  cityCoords?: [number, number];
  originCoords?: [number, number];
  routePoints?: RoutePoint[];
  safetyInfo?: SafetyInfo;
  weatherCondition?: string;
  trafficSummary?: string;
  onClose: () => void;
}

// 👑 全球城市数据库（真实的经纬度与区域）
const GLOBAL_CITIES: { name: string; lat: number; lng: number; region: string; regionGroup: string }[] = [
  { name: '北京', lat: 39.90, lng: 116.40, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '上海', lat: 31.23, lng: 121.47, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '东京', lat: 35.68, lng: 139.76, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '首尔', lat: 37.57, lng: 126.98, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '香港', lat: 22.32, lng: 114.17, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '台北', lat: 25.03, lng: 121.57, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '新加坡', lat: 1.35, lng: 103.82, region: '东南亚', regionGroup: 'SE_ASIA' },
  { name: '曼谷', lat: 13.75, lng: 100.50, region: '东南亚', regionGroup: 'SE_ASIA' },
  { name: '吉隆坡', lat: 3.14, lng: 101.69, region: '东南亚', regionGroup: 'SE_ASIA' },
  { name: '雅加达', lat: -6.21, lng: 106.85, region: '东南亚', regionGroup: 'SE_ASIA' },
  { name: '马尼拉', lat: 14.60, lng: 120.98, region: '东南亚', regionGroup: 'SE_ASIA' },
  { name: '河内', lat: 21.03, lng: 105.85, region: '东南亚', regionGroup: 'SE_ASIA' },
  { name: '孟买', lat: 19.08, lng: 72.88, region: '南亚', regionGroup: 'SOUTH_ASIA' },
  { name: '新德里', lat: 28.61, lng: 77.23, region: '南亚', regionGroup: 'SOUTH_ASIA' },
  { name: '迪拜', lat: 25.20, lng: 55.27, region: '中东', regionGroup: 'MIDDLE_EAST' },
  { name: '利雅得', lat: 24.71, lng: 46.68, region: '中东', regionGroup: 'MIDDLE_EAST' },
  { name: '伊斯坦布尔', lat: 41.01, lng: 28.98, region: '中东', regionGroup: 'MIDDLE_EAST' },
  { name: '德黑兰', lat: 35.69, lng: 51.39, region: '中东', regionGroup: 'MIDDLE_EAST' },
  { name: '莫斯科', lat: 55.75, lng: 37.62, region: '东欧', regionGroup: 'EAST_EUROPE' },
  { name: '基辅', lat: 50.45, lng: 30.52, region: '东欧', regionGroup: 'EAST_EUROPE' },
  { name: '伦敦', lat: 51.51, lng: -0.13, region: '西欧', regionGroup: 'WEST_EUROPE' },
  { name: '巴黎', lat: 48.86, lng: 2.35, region: '西欧', regionGroup: 'WEST_EUROPE' },
  { name: '柏林', lat: 52.52, lng: 13.40, region: '西欧', regionGroup: 'WEST_EUROPE' },
  { name: '罗马', lat: 41.90, lng: 12.50, region: '南欧', regionGroup: 'WEST_EUROPE' },
  { name: '马德里', lat: 40.42, lng: -3.70, region: '南欧', regionGroup: 'WEST_EUROPE' },
  { name: '纽约', lat: 40.71, lng: -74.01, region: '北美', regionGroup: 'NORTH_AMERICA' },
  { name: '洛杉矶', lat: 34.05, lng: -118.24, region: '北美', regionGroup: 'NORTH_AMERICA' },
  { name: '芝加哥', lat: 41.88, lng: -87.63, region: '北美', regionGroup: 'NORTH_AMERICA' },
  { name: '多伦多', lat: 43.65, lng: -79.38, region: '北美', regionGroup: 'NORTH_AMERICA' },
  { name: '墨西哥城', lat: 19.43, lng: -99.13, region: '拉美', regionGroup: 'LATIN_AMERICA' },
  { name: '圣保罗', lat: -23.55, lng: -46.63, region: '拉美', regionGroup: 'LATIN_AMERICA' },
  { name: '布宜诺斯艾利斯', lat: -34.60, lng: -58.38, region: '拉美', regionGroup: 'LATIN_AMERICA' },
  { name: '开罗', lat: 30.04, lng: 31.24, region: '北非', regionGroup: 'AFRICA' },
  { name: '拉各斯', lat: 6.45, lng: 3.40, region: '西非', regionGroup: 'AFRICA' },
  { name: '内罗毕', lat: -1.29, lng: 36.82, region: '东非', regionGroup: 'AFRICA' },
  { name: '约翰内斯堡', lat: -26.20, lng: 28.05, region: '南非', regionGroup: 'AFRICA' },
  { name: '悉尼', lat: -33.87, lng: 151.21, region: '大洋洲', regionGroup: 'OCEANIA' },
  { name: '墨尔本', lat: -37.81, lng: 144.96, region: '大洋洲', regionGroup: 'OCEANIA' },
  { name: '成都', lat: 30.57, lng: 104.07, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '乌鲁木齐', lat: 43.83, lng: 87.62, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '拉萨', lat: 29.65, lng: 91.11, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '昆明', lat: 25.04, lng: 102.68, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '广州', lat: 23.13, lng: 113.26, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '深圳', lat: 22.54, lng: 114.06, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '杭州', lat: 30.27, lng: 120.15, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '西安', lat: 34.26, lng: 108.94, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '重庆', lat: 29.56, lng: 106.55, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '武汉', lat: 30.59, lng: 114.31, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '长沙', lat: 28.23, lng: 112.94, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '南京', lat: 32.06, lng: 118.80, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '天津', lat: 39.14, lng: 117.18, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '青岛', lat: 36.07, lng: 120.38, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '大连', lat: 38.91, lng: 121.61, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '哈尔滨', lat: 45.80, lng: 126.53, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '厦门', lat: 24.48, lng: 118.09, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '三亚', lat: 18.25, lng: 109.51, region: '东亚', regionGroup: 'EAST_ASIA' },
  { name: '桂林', lat: 25.28, lng: 110.29, region: '东亚', regionGroup: 'EAST_ASIA' },
];

// A tiny local equirectangular texture keeps the WebGL scene useful when a
// remote CDN is blocked or temporarily unavailable. It is intentionally
// subdued so route points remain legible instead of presenting a blank canvas.
const FALLBACK_GLOBE_TEXTURE = `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="512" viewBox="0 0 1024 512"><defs><linearGradient id="o" x1="0" x2="0" y1="0" y2="1"><stop stop-color="#12355b"/><stop offset="1" stop-color="#071528"/></linearGradient><pattern id="g" width="64" height="64" patternUnits="userSpaceOnUse"><path d="M0 32h64M32 0v64" stroke="#38bdf8" stroke-opacity=".18" fill="none"/><circle cx="32" cy="32" r="2" fill="#38bdf8" fill-opacity=".22"/></pattern></defs><rect width="1024" height="512" fill="url(#o)"/><rect width="1024" height="512" fill="url(#g)"/><path d="M118 158c55-45 106-36 140 5 20 24 12 46-19 62-26 14-45 42-73 34-39-11-82-55-48-101zm273 62c24-31 58-44 89-24 25 16 25 47 5 67-17 17-20 48-52 49-33 1-62-44-42-92zm189-47c39-19 82-5 95 25 10 24-13 43-35 57-25 16-23 51-52 54-32 4-69-30-62-65 6-28 27-57 54-71zm179 104c30-18 77-6 95 21 18 27 4 56-29 62-32 6-67-7-79-33-9-20-6-39 13-50z" fill="#1f6f59" fill-opacity=".78" stroke="#59c58b" stroke-opacity=".22"/><path d="M0 385c160-34 260 31 414 0 153-31 277-16 610 12v115H0z" fill="#0b4d68" fill-opacity=".45"/></svg>`)}`;

const escapeHtml = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char] || char));

type TextureStatus = 'loading' | 'ready' | 'fallback';

function useGlobeTexture(mode: 'light' | 'dark') {
  const configured = radarGlobeImage(mode);
  const [texture, setTexture] = useState(configured.globeImageUrl);
  const [bumpTexture, setBumpTexture] = useState<string | undefined>(configured.bumpImageUrl);
  const [status, setStatus] = useState<TextureStatus>('loading');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const candidates = [
      configured.globeImageUrl,
      mode === 'dark'
        ? 'https://unpkg.com/three-globe@2.45.2/example/img/earth-dark.jpg'
        : 'https://unpkg.com/three-globe@2.45.2/example/img/earth-blue-marble.jpg',
    ].filter((url, index, all) => url && all.indexOf(url) === index);
    let index = 0;

    setStatus('loading');
    setTexture(FALLBACK_GLOBE_TEXTURE);
    setBumpTexture(undefined);

    const finishFallback = () => {
      if (cancelled) return;
      setTexture(FALLBACK_GLOBE_TEXTURE);
      setBumpTexture(undefined);
      setStatus('fallback');
    };
    const probe = () => {
      if (cancelled) return;
      const url = candidates[index];
      if (!url) {
        finishFallback();
        return;
      }
      const image = new Image();
      image.crossOrigin = 'anonymous';
      timer = window.setTimeout(() => {
        image.onload = null;
        image.onerror = null;
        index += 1;
        probe();
      }, 5000);
      image.onload = () => {
        if (timer !== undefined) window.clearTimeout(timer);
        if (cancelled) return;
        setTexture(url);
        setStatus('ready');
        const bump = new Image();
        bump.crossOrigin = 'anonymous';
        bump.onload = () => { if (!cancelled) setBumpTexture(configured.bumpImageUrl); };
        bump.src = configured.bumpImageUrl;
      };
      image.onerror = () => {
        if (timer !== undefined) window.clearTimeout(timer);
        index += 1;
        probe();
      };
      image.src = url;
    };
    probe();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [configured.bumpImageUrl, configured.globeImageUrl, mode, attempt]);

  return { texture, bumpTexture, status, retry: () => setAttempt((value) => value + 1) };
}

export default function WorldSafetyGlobe({
  targetCity,
  cityCoords = [104.0665, 30.5722],
  originCoords = [116.4074, 39.9042],
  routePoints = [],
  safetyInfo,
  weatherCondition = '暂无供应商天气数据',
  trafficSummary = '暂无供应商路况数据',
  onClose
}: WorldSafetyGlobeProps) {
  const globeRef = useRef<any>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const globeHostRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState(() => typeof window === 'undefined'
    ? { width: 800, height: 600 }
    : { width: window.innerWidth, height: window.innerHeight });
  const [autoRotate, setAutoRotate] = useState(true);
  const [selectedIndex, setSelectedIndex] = useState<number>(0);
  const [activeTab, setActiveTab] = useState<'overview' | 'risk' | 'route'>('overview');
  const [showGlobalNetwork, setShowGlobalNetwork] = useState(false);
  const [showRouteLayer, setShowRouteLayer] = useState(true);
  const [showContextLayer, setShowContextLayer] = useState(true);
  const [showSignalLayer, setShowSignalLayer] = useState(true);
  const [currentTime, setCurrentTime] = useState(new Date());
  const mode = useRadarTheme();
  const COLORS = radarPalette(mode);
  const panelStyle = radarPanelStyle(mode);
  const bg = radarBackground(mode);
  const { texture: globeTexture, bumpTexture, status: textureStatus, retry: retryTexture } = useGlobeTexture(mode);
  const [globeKey, setGlobeKey] = useState(0);
  const [webglLost, setWebglLost] = useState(false);
  const compactViewport = dimensions.width < 640;
  const globeAtmosphereColor = mode === 'dark' ? '#7dd3fc' : '#93c5fd';

  // P1: 轻量刷新 —— 用后端实时风控端点刷新 is_estimated 快照，避免用户停留在过期快照上
  const [liveSafety, setLiveSafety] = useState<SafetyInfo | undefined>(safetyInfo);
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState<number>(Date.now());

  // Render the immersive radar at the document root. The map workspace uses
  // its own isolated stacking context, which otherwise lets sibling panels
  // intercept taps even when this dialog appears visually above them.
  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [onClose]);

  useEffect(() => {
    const t = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const updateSize = () => {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        setDimensions({ width: rect.width, height: rect.height });
      }
    };
    updateSize();
    const ro = new ResizeObserver(updateSize);
    ro.observe(el);
    window.addEventListener('resize', updateSize);
    return () => {
      ro.disconnect();
      window.removeEventListener('resize', updateSize);
    };
  }, []);

  // Surface WebGL context loss as a recoverable state instead of leaving a
  // frozen/black canvas. The listener is attached after react-globe creates
  // its canvas and is cleaned up whenever the globe is remounted.
  useEffect(() => {
    const canvas = globeHostRef.current?.querySelector('canvas');
    if (!canvas) return;
    const onLost = (event: Event) => {
      event.preventDefault();
      setWebglLost(true);
    };
    const onRestored = () => setWebglLost(false);
    canvas.addEventListener('webglcontextlost', onLost, { passive: false });
    canvas.addEventListener('webglcontextrestored', onRestored);
    return () => {
      canvas.removeEventListener('webglcontextlost', onLost);
      canvas.removeEventListener('webglcontextrestored', onRestored);
    };
  }, [globeKey, dimensions]);

  const safeCityCoords: [number, number] = validLngLat(cityCoords) ? cityCoords : [104.0665, 30.5722];
  const safeOriginCoords: [number, number] = validLngLat(originCoords) ? originCoords : [116.4074, 39.9042];
  const safeRoutePoints = useMemo(
    () => routePoints.filter((point) => validLngLat(point.lnglat)),
    [routePoints],
  );
  const destLng = safeRoutePoints.length > 0 ? safeRoutePoints[0].lnglat[0] : safeCityCoords[0];
  const destLat = safeRoutePoints.length > 0 ? safeRoutePoints[0].lnglat[1] : safeCityCoords[1];
  const origLng = safeOriginCoords[0];
  const origLat = safeOriginCoords[1];

  const hasRealCii = typeof liveSafety?.cii_score === 'number';
  const ciiScore = hasRealCii ? (liveSafety!.cii_score as number) : 0;
  const profile = riskProfile(ciiScore, COLORS);
  const ciiPercent = hasRealCii ? Math.max(0, Math.min(100, ciiScore)) : 0;

  // P1: 安全情报快照随 prop 变更同步到「活数据」
  useEffect(() => {
    setLiveSafety(safetyInfo);
    setLastRefreshed(Date.now());
  }, [safetyInfo]);

  // P1: 轻量刷新 —— 调网关实时风控端点，把「估算值/出发前快照」升级为「实时情报」。
  // 走共享读取器：与 RiskPushCenter 共享同一请求，避免同一城市每 30s 被拉两次。
  const refreshSafety = useCallback(async (force = false) => {
    if (!targetCity) return;
    setRefreshing(true);
    try {
      const snap = await fetchRiskSnapshot(targetCity, {
        coordinate: [destLng, destLat],
        baseline: safetyInfo || undefined,
        force,
      });
      if (snap) {
        setLiveSafety(prev => ({ ...(prev || {}), ...snap }));
      }
    } catch {
      // 静默失败，不打断雷达主流程
    } finally {
      setRefreshing(false);
      setLastRefreshed(Date.now());
    }
  }, [targetCity, destLng, destLat, safetyInfo]);

  // P1: 打开雷达期间每 30s 轻量刷新一次，避免长期停留看到过期快照
  useEffect(() => {
    void refreshSafety();
    const t = setInterval(() => { void refreshSafety(); }, 30000);
    return () => clearInterval(t);
  }, [refreshSafety]);

  // P1: tab 切换时强制刷新（绕过共享缓存，跳过首次挂载避免与定时器重复触发）
  const prevTabRef = useRef(activeTab);
  useEffect(() => {
    if (prevTabRef.current !== activeTab) {
      prevTabRef.current = activeTab;
      void refreshSafety(true);
    }
  }, [activeTab, refreshSafety]);

  // 👑 动态：根据目标城市确定关注的区域，过滤相关城市
  const targetCityInfo = GLOBAL_CITIES.find(c => c.name === targetCity || c.name.includes(targetCity));
  const targetRegionGroup = targetCityInfo?.regionGroup || 'EAST_ASIA';

  // Round-robin by region so a dense East Asia catalogue cannot crowd the
  // rest of the world off the globe. The target itself remains the first item.
  const dynamicGlobalCities = useMemo(() => {
    const groups = Array.from(new Set(GLOBAL_CITIES.map((city) => city.regionGroup)));
    const orderedGroups = [targetRegionGroup, ...groups.filter((group) => group !== targetRegionGroup)];
    const queues = new Map(orderedGroups.map((group) => {
      const cities = GLOBAL_CITIES.filter((city) => city.regionGroup === group);
      const target = cities.find((city) => city.name === targetCity || city.name.includes(targetCity));
      return [group, target ? [target, ...cities.filter((city) => city !== target)] : cities] as const;
    }));
    const balanced: typeof GLOBAL_CITIES = [];
    for (let round = 0; round < 2; round += 1) {
      for (const group of orderedGroups) {
        const city = queues.get(group)?.[round];
        if (city) balanced.push(city);
      }
    }
    return balanced.slice(0, 20);
  }, [targetCity, targetRegionGroup]);

  const globalRegionCount = useMemo(
    () => new Set(dynamicGlobalCities.map((city) => city.regionGroup)).size,
    [dynamicGlobalCities],
  );
  const globalRegionSummary = useMemo(() => {
    const counts = new Map<string, number>();
    dynamicGlobalCities.forEach((city) => counts.set(city.region, (counts.get(city.region) || 0) + 1));
    return Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);
  }, [dynamicGlobalCities]);

  useEffect(() => {
    if (globeRef.current) {
      globeRef.current.pointOfView(
        { lat: destLat, lng: destLng, altitude: 1.8 },
        2500
      );
    }
  }, [destLat, destLng]);

  useEffect(() => {
    const ctrl = globeRef.current?.controls?.();
    if (ctrl) {
      ctrl.autoRotate = autoRotate;
      ctrl.autoRotateSpeed = 0.5;
      ctrl.enableDamping = true;
      ctrl.dampingFactor = 0.1;
    }
  }, [autoRotate, dimensions]);

  // 无障碍：尊重系统「减少动态效果」偏好，关闭自动巡航（P7）
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const apply = () => { if (mq.matches) setAutoRotate(false); };
    apply();
    mq.addEventListener('change', apply);
    return () => mq.removeEventListener('change', apply);
  }, []);

  // 👑 路线节点数据
  const pointsData = safeRoutePoints.length > 0
    ? safeRoutePoints.map((pt, idx) => {
        const cost = parseCostNumber(pt.cost);
        const isHighCost = cost > 300;
        const isMediumCost = cost > 150;
        return {
          lat: pt.lnglat[1], lng: pt.lnglat[0],
          name: pt.name, index: idx,
          size: idx === 0 ? 0.9 : 0.6,
          color: idx === 0 ? COLORS.origin : (isHighCost ? COLORS.danger : isMediumCost ? COLORS.warn : COLORS.accent),
          desc: pt.desc || '', time: pt.time || '', cost: pt.cost || '', costNum: cost,
          tags: pt.tags || [], riskScore: pt.riskScore ?? 0,
          isRoutePoint: true,
        };
      })
    : [{
        lat: destLat, lng: destLng, name: targetCity, index: 0, size: 0.9,
        color: COLORS.origin, desc: '目标城市', time: '', cost: '', costNum: 0, tags: [], riskScore: 0,
        isRoutePoint: true,
      }];

  // 👑 周边城市上下文标记（目标城市周围 ~15 度范围内的城市）
  const contextCities = useMemo(() => {
    const tc = targetCityInfo || { lat: destLat, lng: destLng };
    const tcLat = tc.lat || destLat;
    const tcLng = tc.lng || destLng;
    return GLOBAL_CITIES
      .filter(c => {
        const dLat = Math.abs(c.lat - tcLat);
        const dLng = Math.abs(c.lng - tcLng);
        return dLat < 15 && dLng < 20 && c.name !== targetCity;
      })
      .filter(c => !safeRoutePoints.some(rp => rp.name === c.name))
      .slice(0, 8)
      .map(c => ({
        lat: c.lat, lng: c.lng, name: c.name, region: c.region,
        color: '#64748b',
        size: 0.12,
        isContextCity: true,
      }));
  }, [targetCityInfo, targetCity, destLat, destLng, safeRoutePoints]);

  const contextCityNames = useMemo(() => new Set(contextCities.map(c => c.name)), [contextCities]);

  const signalPoints = useMemo(() => safeRoutePoints.filter((point) => Number(point.riskScore || 0) > 0).map((point, index) => ({
    lat: point.lnglat[1], lng: point.lnglat[0], name: `${point.name} · 风险信号`, color: Number(point.riskScore || 0) > 60 ? COLORS.danger : COLORS.warn, size: 0.35, isSignalPoint: true, index,
  })), [safeRoutePoints, COLORS.danger, COLORS.warn]);

  // 👑 动态全球城市标记（过滤后，排除路线节点和周边上下文城市）
  const globalRiskPoints = useMemo(() => dynamicGlobalCities
    .filter(c => !safeRoutePoints.some(rp => rp.lnglat[0] === c.lng && rp.lnglat[1] === c.lat))
    .filter(c => !contextCityNames.has(c.name))
    .map(c => ({
      lat: c.lat, lng: c.lng, name: c.name, region: c.region,
      color: '#64748b',
      size: 0.2, isGlobalCity: true,
    })), [dynamicGlobalCities, safeRoutePoints, contextCityNames]);

  // 👑 航线弧线
  const arcsData: any[] = [];
  if (safeRoutePoints.length > 0) {
    arcsData.push({ startLat: origLat, startLng: origLng, endLat: destLat, endLng: destLng, color: [COLORS.accent, COLORS.origin], stroke: 0.28 });
    for (let i = 0; i < safeRoutePoints.length - 1; i++) {
      arcsData.push({
        startLat: safeRoutePoints[i].lnglat[1], startLng: safeRoutePoints[i].lnglat[0],
        endLat: safeRoutePoints[i + 1].lnglat[1], endLng: safeRoutePoints[i + 1].lnglat[0],
        color: [COLORS.accent, '#818cf8'], stroke: 0.22,
      });
    }
  }

  // 👑 涟漪环
  const ringsData = [
    { lat: destLat, lng: destLng, maxR: 3.5, propagationSpeed: 1.5, repeatPeriod: 2000, color: profile.ring },
  ];
  if (safeRoutePoints[selectedIndex] && selectedIndex > 0) {
    ringsData.push({
      lat: safeRoutePoints[selectedIndex].lnglat[1], lng: safeRoutePoints[selectedIndex].lnglat[0],
      maxR: 2.2, propagationSpeed: 2, repeatPeriod: 1500, color: 'rgba(56, 189, 248, 0.5)',
    });
  }

  const focusOnPoint = (idx: number) => {
    setSelectedIndex(idx);
    setAutoRotate(false); // 点击节点后锁定视角，避免自动巡航将目标转走
    if (globeRef.current && pointsData[idx]) {
      globeRef.current.pointOfView(
        { lat: pointsData[idx].lat, lng: pointsData[idx].lng, altitude: 1.0 },
        1000
      );
    }
  };

  // 切换自动巡航：切到「锁定」时复位视角到目标城市，避免定格在任意旋转角度
  const toggleAutoRotate = () => {
    if (autoRotate) {
      setAutoRotate(false);
      if (globeRef.current) {
        globeRef.current.pointOfView({ lat: destLat, lng: destLng, altitude: 1.8 }, 1200);
      }
    } else {
      setAutoRotate(true);
    }
  };

  const toggleGlobalNetwork = () => {
    const next = !showGlobalNetwork;
    setShowGlobalNetwork(next);
    setAutoRotate(next);
    globeRef.current?.pointOfView(
      next
        ? { lat: 18, lng: 15, altitude: 2.55 }
        : { lat: destLat, lng: destLng, altitude: 1.8 },
      1200,
    );
  };

  // 👑 五维风险评分：仅采用后端下发的真实 signal 值，前端不做任何字符串/启发式估算（硬约束）
  const computedDimensions = useMemo(() => {
    const decomposition = (liveSafety as any)?.cii_decomposition?.dimensions || {};
    const dims = {
      crime_score: liveSafety?.crime_score ?? decomposition.crime?.score,
      weather_score: liveSafety?.weather_score ?? decomposition.weather?.score,
      political_score: liveSafety?.political_score ?? decomposition.political?.score,
      health_score: liveSafety?.health_score ?? decomposition.health?.score,
      traffic_score: liveSafety?.traffic_score ?? decomposition.traffic?.score,
    };
    const hasAny = Object.values(dims).some(v => typeof v === 'number');
    return hasAny ? dims as { crime_score: number; weather_score: number; political_score: number; health_score: number; traffic_score: number } : null;
  }, [liveSafety]);

  const dimensionRisk = (score: number) => {
    if (score >= 60) return { color: COLORS.danger, label: '高' };
    if (score >= 30) return { color: COLORS.warn, label: '中' };
    return { color: COLORS.safe, label: '低' };
  };

  // 计算总成本
  const totalCost = safeRoutePoints.reduce((s, p) => s + parseCostNumber(p.cost), 0);

  const alertCount = liveSafety?.active_alerts?.length || 0;
  const hasSafetyData = !!liveSafety;
  const safetySource = String(liveSafety?.source || '').trim();
  const freshnessLabel = lastRefreshed ? `${Math.max(0, Math.round((Date.now() - lastRefreshed) / 1000))} 秒前` : '未同步';

  // 底部滚动条
  const tickerItems = [
    { icon: ShieldCheck, text: `${targetCity} CII: ${hasRealCii ? ciiScore.toFixed(1) : 'N/A'}`, color: profile.color },
    { icon: CloudRain, text: `天气: ${weatherCondition}`, color: COLORS.accent },
    { icon: Activity, text: `路况: ${trafficSummary}`, color: COLORS.accent },
    { icon: Globe2, text: `全球情报: ${globalRegionCount} 区域 / ${dynamicGlobalCities.length} 城市`, color: COLORS.purple },
    { icon: Siren, text: `活跃告警: ${alertCount}`, color: COLORS.danger },
    { icon: DollarSign, text: `总预算: ¥${totalCost}`, color: '#fbbf24' },
    { icon: Clock, text: `刷新: ${currentTime.toLocaleTimeString('zh-CN')}`, color: COLORS.cyan },
  ];

  if (typeof document === 'undefined') return null;

  return createPortal(
    <div ref={containerRef} role="dialog" aria-modal="true" aria-label="3D 态势感知雷达" className="radar-modal-root fixed inset-0 z-[2147483000] overflow-hidden pointer-events-auto" style={{ background: bg }}>
      {/* 星空背景 */}
      <div className="absolute inset-0 pointer-events-none overflow-hidden">
        <div className="absolute inset-0" style={{
          background: 'radial-gradient(1px 1px at 15% 25%, rgba(255,255,255,0.6), transparent), radial-gradient(1px 1px at 55% 65%, rgba(255,255,255,0.5), transparent), radial-gradient(1px 1px at 75% 15%, rgba(255,255,255,0.7), transparent), radial-gradient(2px 2px at 35% 75%, rgba(180,200,255,0.4), transparent), radial-gradient(1px 1px at 85% 45%, rgba(255,255,255,0.5), transparent), radial-gradient(1px 1px at 25% 85%, rgba(255,255,255,0.6), transparent), radial-gradient(1px 1px at 65% 5%, rgba(200,220,255,0.5), transparent), radial-gradient(2px 2px at 45% 55%, rgba(255,255,255,0.3), transparent), radial-gradient(1px 1px at 95% 80%, rgba(255,255,255,0.4), transparent), radial-gradient(1px 1px at 5% 55%, rgba(255,255,255,0.5), transparent)'
        }} />
        <div className="absolute inset-0 opacity-15" style={{ background: 'radial-gradient(ellipse 70% 18% at 65% 35%, rgba(56,189,248,0.15), transparent), radial-gradient(ellipse 50% 12% at 25% 65%, rgba(139,92,246,0.1), transparent)' }} />
      </div>
      <div className="absolute inset-0 opacity-[0.03]" style={{ backgroundImage: 'linear-gradient(rgba(56,189,248,0.5) 1px, transparent 1px), linear-gradient(90deg, rgba(56,189,248,0.5) 1px, transparent 1px)', backgroundSize: '60px 60px' }} />

      {/* 扫描线 / 旋转雷达扫掠 / 暗角 */}
      <div className="absolute inset-0 pointer-events-none radar-scanlines opacity-70" />
      <div className="absolute pointer-events-none radar-sweep" style={{ position: 'absolute', top: '-25%', left: '-25%', width: '150%', height: '150%' }} />
      <div className="absolute inset-0 pointer-events-none radar-vignette" />

      {/* ========== 顶部HUD ========== */}
      <div className="radar-hud-top absolute top-0 left-0 right-0 z-50 px-4 sm:px-6 py-4 flex items-start sm:items-center justify-between gap-3 flex-wrap sm:flex-nowrap pointer-events-none">
        <div className="radar-hud-brand flex items-center gap-4 pointer-events-auto min-w-0">
          <div className="relative">
            <div className="w-10 h-10 rounded-xl bg-sky-500/10 border border-sky-500/30 flex items-center justify-center">
              <Radar className="w-5 h-5 text-sky-600 dark:text-sky-400" />
            </div>
            <div className="absolute inset-0 w-10 h-10 rounded-xl bg-sky-400/20 animate-ping" />
          </div>
          <div>
            <div className="radar-hud-title flex items-center gap-3 min-w-0">
              <h2 className="text-slate-900 dark:text-white font-black text-xl tracking-[0.25em]" style={{ fontFamily: RADAR_FONT.display, textShadow: '0 0 20px rgba(56,189,248,0.45)' }}>OMNIROUTE</h2>
              <span className="radar-hud-badge text-xs font-bold px-2 py-0.5 rounded border border-sky-500/30 text-sky-700 dark:text-sky-300 bg-sky-500/10 tracking-wider">3D 态势感知雷达</span>
            </div>
            <div className="radar-hud-meta flex items-center gap-3 mt-1 min-w-0">
              <p className="text-[13px] text-slate-700 dark:text-slate-300 tracking-wide" style={{ fontFamily: RADAR_FONT.data }}>
                <span className="truncate inline-block max-w-[12rem] align-bottom">{targetCity}</span> <span className="text-slate-500">·</span> {safeRoutePoints.length} 节点 <span className="text-slate-500">·</span> CII <span className="text-cyan-700 dark:text-cyan-300">{hasRealCii ? ciiScore.toFixed(1) : '---'}</span>
              </p>
              <span className={`text-xs font-bold px-1.5 py-0.5 rounded border ${hasRealCii ? profile.bg + ' ' + profile.text + ' border-current/20' : 'bg-slate-100 dark:bg-slate-800/50 text-slate-500 border-slate-300/70 dark:border-white/10'}`}>
                {hasRealCii ? profile.label : '评估中'}
              </span>
            </div>
          </div>
        </div>

        <div className="radar-hud-actions flex items-center gap-2 pointer-events-auto">
          <button onClick={toggleGlobalNetwork} aria-pressed={showGlobalNetwork} aria-label={showGlobalNetwork ? '退出全球情报视图' : '打开全球情报视图'} className={`radar-hud-action h-8 px-2.5 rounded-lg border text-xs font-bold transition-all flex items-center gap-1 cursor-pointer ${showGlobalNetwork ? 'bg-purple-500/10 border-purple-400/30 text-purple-700 dark:text-purple-300' : 'bg-slate-900/5 dark:bg-white/5 border-slate-300/70 dark:border-white/10 text-slate-500'}`}>
            <Target className="w-3 h-3 shrink-0" /> <span className="radar-action-label">全球情报</span>
          </button>
          <button onClick={toggleAutoRotate} aria-pressed={autoRotate} aria-label={autoRotate ? '停止自动巡航' : '开启自动巡航'} className={`radar-hud-action h-8 px-2.5 rounded-lg border text-xs font-bold transition-all flex items-center gap-1 cursor-pointer ${autoRotate ? 'bg-sky-500/15 border-sky-400/40 text-sky-700 dark:text-sky-300' : 'bg-slate-900/5 dark:bg-white/5 border-slate-300/70 dark:border-white/10 text-slate-600 dark:text-slate-400 hover:bg-slate-900/10 dark:hover:bg-white/10'}`}>
            <RotateCw className={`w-3 h-3 ${autoRotate ? 'animate-spin' : ''}`} style={{ animationDuration: '4s' }} />
            <span className="radar-action-label">{autoRotate ? '巡航' : '手动'}</span>
          </button>
          <button onClick={onClose} aria-label="退出雷达" className="radar-hud-action min-h-10 h-10 px-3 rounded-lg bg-slate-900/5 dark:bg-white/5 border border-slate-300/70 dark:border-white/10 text-slate-700 dark:text-slate-300 hover:bg-slate-900/10 dark:hover:bg-white/10 transition-all flex items-center gap-1 text-xs font-bold cursor-pointer">
            <X className="w-3 h-3 shrink-0" /> <span className="radar-action-label">退出</span>
          </button>
        </div>
      </div>

      {/* ========== 左下角风控主面板 ========== */}
      <DraggablePanel
        key={compactViewport ? 'compact-risk-panel' : 'desktop-risk-panel'}
        className="radar-main-panel absolute bottom-8 left-4 right-4 sm:left-6 sm:right-auto sm:w-[360px] z-20 pointer-events-auto radar-panel"
        style={panelStyle}
        defaultCollapsed={compactViewport}
        icon={<ShieldCheck className="w-3.5 h-3.5" style={{ color: COLORS.accent }} />}
        title={<span className="text-[13px] font-bold" style={{ color: COLORS.accent }}>态势风控面板</span>}
      >
        <div className="flex border-b border-slate-300/50 dark:border-white/5">
          {([
            { key: 'overview', label: '综合概览', icon: Eye },
            { key: 'risk', label: '风险维度', icon: AlertTriangle },
            { key: 'route', label: '路线详情', icon: Navigation },
          ] as const).map(tab => (
            <button key={tab.key} onClick={() => setActiveTab(tab.key)} className={`flex-1 py-2.5 text-xs font-bold flex items-center justify-center gap-1.5 transition-all cursor-pointer ${activeTab === tab.key ? 'text-sky-700 dark:text-sky-300 border-b-2 border-sky-400 bg-sky-500/5' : 'text-slate-500 hover:text-slate-700 dark:text-slate-300'}`}>
              <tab.icon className="w-3 h-3" /> {tab.label}
            </button>
          ))}
        </div>

        <div className="p-4 space-y-3 h-[200px] sm:h-[380px] overflow-y-auto custom-scrollbar">
          {/* P1: 情报同步状态指示（估算值徽标 + 上次同步时间 + 刷新中转圈） */}
          <div className="flex items-center justify-between gap-2 text-[11px] font-mono text-slate-500">
            <span className="flex items-center gap-1.5">
              <RotateCw className={`w-3 h-3 ${refreshing ? 'animate-spin text-sky-500' : ''}`} />
              <span>{refreshing ? '正在同步实时情报…' : `上次同步 ${new Date(lastRefreshed).toLocaleTimeString('zh-CN')} · ${freshnessLabel}`}</span>
            </span>
            {liveSafety?.is_estimated && (
              <span className="px-1.5 py-0.5 rounded border border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400">估算值</span>
            )}
          </div>

          {activeTab === 'overview' && (
            <>
              {/* CII 综合影响指数 */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-slate-600 dark:text-slate-400 tracking-wide">综合影响指数 (CII)</span>
                  <span className={`text-2xl font-black ${hasRealCii ? profile.text : 'text-slate-500'}`} style={{ fontFamily: RADAR_FONT.display, textShadow: hasRealCii ? `0 0 18px ${profile.color}55` : 'none' }}>{hasRealCii ? ciiScore.toFixed(1) : 'N/A'}</span>
                </div>
                <div className="relative h-2 w-full rounded-full bg-slate-800 overflow-hidden">
                  <div className="h-full rounded-full transition-all duration-1000 relative overflow-hidden" style={{ width: `${Math.max(ciiPercent, 2)}%`, background: `linear-gradient(90deg, ${COLORS.safe}, ${COLORS.warn}, ${COLORS.danger})` }}>
                    <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/30 to-transparent animate-pulse" />
                  </div>
                  <div className="absolute top-0 left-[20%] w-px h-full bg-white/20" />
                  <div className="absolute top-0 left-[40%] w-px h-full bg-white/20" />
                </div>
                <div className="flex justify-between text-[11px] font-mono">
                  <span className="text-emerald-600 dark:text-emerald-500">安全</span><span className="text-amber-600 dark:text-amber-500">预警</span><span className="text-rose-600 dark:text-rose-500">高危</span><span className="text-slate-600">100</span>
                </div>
              </div>

              {/* 信任提示：无实时情报源时明确标注估算值，避免误导 */}
              {liveSafety?.is_estimated && (
                <div className="bg-amber-500/10 border border-amber-500/20 rounded-lg p-2 flex items-center gap-1.5">
                  <Info className="w-3.5 h-3.5 text-amber-600 dark:text-amber-400 shrink-0" />
                  <span className="text-xs text-amber-600 dark:text-amber-300 font-mono">本数据为估算值 · 暂未接入实时情报源</span>
                </div>
              )}

              <div className="rounded-lg border border-slate-300/50 bg-slate-100/60 p-2.5 dark:border-white/10 dark:bg-slate-900/30">
                <div className="mb-2 flex items-center justify-between text-[10px] font-bold text-slate-500">
                  <span>数据边界</span><span>{safetySource || '暂无风控供应商'}</span>
                </div>
                <div className="grid grid-cols-3 gap-1.5">
                  <button type="button" onClick={() => setShowRouteLayer((value) => !value)} aria-pressed={showRouteLayer} className={`rounded px-1.5 py-1 text-[10px] font-bold ${showRouteLayer ? 'bg-sky-500/15 text-sky-700 dark:text-sky-300' : 'bg-slate-200 text-slate-400 dark:bg-slate-800'}`}>路线 {showRouteLayer ? '开' : '关'}</button>
                  <button type="button" onClick={() => setShowContextLayer((value) => !value)} aria-pressed={showContextLayer} className={`rounded px-1.5 py-1 text-[10px] font-bold ${showContextLayer ? 'bg-slate-300/70 text-slate-700 dark:bg-white/10 dark:text-slate-300' : 'bg-slate-200 text-slate-400 dark:bg-slate-800'}`}>周边 {showContextLayer ? '开' : '关'}</button>
                  <button type="button" onClick={() => setShowSignalLayer((value) => !value)} aria-pressed={showSignalLayer} className={`rounded px-1.5 py-1 text-[10px] font-bold ${showSignalLayer ? 'bg-rose-500/15 text-rose-700 dark:text-rose-300' : 'bg-slate-200 text-slate-400 dark:bg-slate-800'}`}>风险 {showSignalLayer ? '开' : '关'}</button>
                </div>
                <div className="mt-1.5 grid grid-cols-2 gap-1.5"><button type="button" onClick={toggleGlobalNetwork} aria-pressed={showGlobalNetwork} className={`rounded px-1.5 py-1 text-[10px] font-bold ${showGlobalNetwork ? 'bg-purple-500/15 text-purple-700 dark:text-purple-300' : 'bg-slate-200 text-slate-400 dark:bg-slate-800'}`}>全球城市 {showGlobalNetwork ? '开' : '关'}</button><span className="rounded bg-slate-200/60 px-1.5 py-1 text-center text-[10px] text-slate-500">信号点 {signalPoints.length}</span></div>
                <p className="mt-2 text-[10px] leading-4 text-slate-400">全球城市点为坐标目录，不代表实时天气、路况或客流；实时字段仅在接入供应商且带时间戳时展示。</p>
              </div>

              {/* 目标城市信息卡片 */}
              <div className="bg-slate-200/70 dark:bg-slate-800/40 rounded-xl p-3 border border-slate-300/50 dark:border-white/5 space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Building2 className="w-3.5 h-3.5 text-sky-600 dark:text-sky-400" />
                    <span className="text-[13px] font-bold text-slate-900 dark:text-white">{targetCity}</span>
                  </div>
                  <span className={`text-xs font-mono px-2 py-0.5 rounded border ${profile.text} border-current/20 bg-current/5`}>{profile.label}</span>
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <div className="text-center bg-slate-200/70 dark:bg-slate-900/50 rounded-lg p-2">
                    <div className="text-[11px] text-slate-500 mb-0.5">风险等级</div>
                    <div className={`text-[13px] font-black ${hasSafetyData ? profile.text : 'text-slate-500'}`}>
                      {hasSafetyData ? (liveSafety?.risk_level || '评估中') : '暂无数据'}
                    </div>
                  </div>
                  <div className="text-center bg-slate-200/70 dark:bg-slate-900/50 rounded-lg p-2">
                    <div className="text-[11px] text-slate-500 mb-0.5">节点数</div>
                    <div className="text-[13px] font-black text-slate-900 dark:text-white">{safeRoutePoints.length}</div>
                  </div>
                  <div className="text-center bg-slate-200/70 dark:bg-slate-900/50 rounded-lg p-2">
                    <div className="text-[11px] text-slate-500 mb-0.5">活跃告警</div>
                    <div className={`text-[13px] font-black ${alertCount > 0 ? 'text-rose-600 dark:text-rose-400' : 'text-emerald-600 dark:text-emerald-400'}`}>{alertCount}</div>
                  </div>
                </div>
              </div>

              {/* 天气 & 路况 */}
              <div className="grid grid-cols-2 gap-2">
                <div className="bg-slate-100 dark:bg-slate-800/50 rounded-lg p-2 border border-slate-300/50 dark:border-white/5">
                  <div className="flex items-center gap-1 text-sky-600 dark:text-sky-400 mb-0.5"><Thermometer className="w-3 h-3" /><span className="text-[11px] font-bold">天气</span></div>
                  <div className="text-[13px] text-slate-900 dark:text-white font-bold">{weatherCondition || '暂无数据'}</div>
                </div>
                <div className="bg-slate-100 dark:bg-slate-800/50 rounded-lg p-2 border border-slate-300/50 dark:border-white/5">
                  <div className="flex items-center gap-1 text-amber-600 dark:text-amber-400 mb-0.5"><Activity className="w-3 h-3" /><span className="text-[11px] font-bold">路况</span></div>
                  <div className="text-[13px] text-slate-900 dark:text-white font-bold leading-tight">{trafficSummary || '暂无数据'}</div>
                </div>
              </div>

              {/* 成本总览 */}
              <div className="bg-slate-200/70 dark:bg-slate-800/40 rounded-lg p-2.5 border border-slate-300/50 dark:border-white/5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5">
                    <DollarSign className="w-3 h-3 text-amber-600 dark:text-amber-400" />
                    <span className="text-xs text-slate-600 dark:text-slate-400 font-bold">预算总览</span>
                  </div>
                  <span className="text-sm font-black text-amber-600 dark:text-amber-400">¥{totalCost.toLocaleString()}</span>
                </div>
                {safeRoutePoints.length > 0 ? (
                  <div className="mt-1.5 flex items-center gap-2 text-[11px] text-slate-500">
                    <span>{safeRoutePoints.length} 个节点</span>
                    <span className="text-slate-700">·</span>
                    <span>均费 ¥{safeRoutePoints.length > 0 ? Math.round(totalCost / safeRoutePoints.length) : 0}</span>
                  </div>
                ) : (
                  <div className="mt-1 text-[11px] text-slate-600 font-mono">暂无路线预算数据</div>
                )}
              </div>

              {/* 无数据提示 */}
              {!hasSafetyData && (
                <div className="bg-slate-200/60 dark:bg-slate-800/20 border border-dashed border-slate-700/50 rounded-lg p-3 text-center">
                  <Info className="w-3.5 h-3.5 text-slate-600 mx-auto mb-1" />
                  <div className="text-xs text-slate-600 font-mono">暂无安全情报数据</div>
                  <div className="text-[11px] text-slate-700 mt-0.5">后端数据尚未同步，请稍后刷新</div>
                </div>
              )}
            </>
          )}

          {activeTab === 'risk' && (
            <div className="space-y-2">
              {computedDimensions ? (
                (() => {
                  const dims = [
                    { label: '治安', score: computedDimensions.crime_score ?? 0 },
                    { label: '气象', score: computedDimensions.weather_score ?? 0 },
                    { label: '政治', score: computedDimensions.political_score ?? 0 },
                    { label: '卫生', score: computedDimensions.health_score ?? 0 },
                    { label: '交通', score: computedDimensions.traffic_score ?? 0 },
                  ];
                  return (
                    <>
                      <RiskRadarChart
                        dimensions={dims.map(d => {
                          const dr = dimensionRisk(d.score);
                          return { label: d.label, score: d.score, color: dr.color, level: dr.label };
                        })}
                        accent={COLORS.accent}
                        grid={COLORS.divider}
                        labelColor={COLORS.muted}
                        tooltipBg={COLORS.labelBg}
                        tooltipText={mode === 'dark' ? '#e2e8f0' : '#0f172a'}
                        max={100}
                      />
                      <div className="grid grid-cols-5 gap-1">
                        {dims.map(d => {
                          const dr = dimensionRisk(d.score);
                          return (
                            <div key={d.label} className="text-center rounded-lg py-1.5" style={{ background: dr.color + '14' }}>
                              <div className="text-[11px] font-bold" style={{ color: dr.color }}>{d.label}</div>
                              <div className="text-[13px] font-black" style={{ color: dr.color }}>{Math.round(d.score)}</div>
                            </div>
                          );
                        })}
                      </div>
                    </>
                  );
                })()
              ) : (
                <div className="mt-3 bg-slate-200/60 dark:bg-slate-800/20 border border-dashed border-slate-700/50 rounded-lg p-3 text-center">
                  <Info className="w-3.5 h-3.5 text-slate-600 mx-auto mb-1" />
                  <div className="text-xs text-slate-600 font-mono">暂无五维风险数据</div>
                  <div className="text-[11px] text-slate-700 mt-0.5">以后端情报为准，尚未同步</div>
                </div>
              )}
            </div>
          )}

          {activeTab === 'route' && (
            <div className="space-y-2">
              <div className="flex justify-between text-xs font-mono">
                <span className="text-slate-600 dark:text-slate-400">路线节点概览</span>
                <span className="text-amber-600 dark:text-amber-400">总预算: ¥{totalCost}</span>
              </div>
              {safeRoutePoints.length > 0 ? safeRoutePoints.map((pt, idx) => {
                const cost = parseCostNumber(pt.cost);
                const isHighCost = cost > 300;
                return (
                  <button key={idx} onClick={() => focusOnPoint(idx)} className={`w-full text-left rounded-lg p-2 border transition-all cursor-pointer ${selectedIndex === idx ? 'bg-sky-500/10 border-sky-500/30' : 'bg-slate-200/70 dark:bg-slate-800/30 border-slate-300/50 dark:border-white/5 hover:bg-slate-100 dark:bg-slate-800/50'}`}>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="w-5 h-5 rounded-full bg-sky-500/20 text-sky-600 dark:text-sky-400 text-[11px] font-black flex items-center justify-center">{idx + 1}</span>
                        <span className="text-xs font-bold text-slate-900 dark:text-slate-200">{pt.name}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        {pt.time && <span className="text-[11px] text-slate-500 font-mono">{pt.time}</span>}
                        {pt.cost && <span className={`text-[11px] font-bold ${isHighCost ? 'text-rose-600 dark:text-rose-400' : 'text-amber-600 dark:text-amber-400'}`}>{pt.cost}</span>}
                      </div>
                    </div>
                    {pt.tags && pt.tags.length > 0 && (
                      <div className="flex gap-1 mt-1 ml-7">
                        {pt.tags.slice(0, 3).map((t, i) => (
                          <span key={i} className="text-[9px] bg-sky-500/10 border border-sky-500/20 px-1 py-0.5 rounded text-sky-600 dark:text-sky-400">{t}</span>
                        ))}
                      </div>
                    )}
                  </button>
                );
              }) : (
                <div className="bg-slate-200/60 dark:bg-slate-800/20 border border-dashed border-slate-700/50 rounded-lg p-4 text-center">
                  <Navigation className="w-4 h-4 text-slate-600 mx-auto mb-1.5" />
                  <div className="text-xs text-slate-600 font-mono">暂无路线数据</div>
                  <div className="text-[11px] text-slate-700 mt-0.5">请先生成路线规划后再查看</div>
                </div>
              )}
            </div>
          )}

          {/* 告警列表 */}
          {activeTab !== 'route' && (
            <div className="space-y-1.5">
              <div className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-400 font-mono"><Siren className="w-3 h-3" /> 安全告警</div>
              {alertCount > 0 ? (
                liveSafety!.active_alerts!.map((alert, idx) => (
                  <div key={idx} className={`rounded-lg p-2.5 border ${alert.level === 'HIGH' ? 'bg-rose-500/5 border-rose-500/20' : alert.level === 'MEDIUM' ? 'bg-amber-500/5 border-amber-500/20' : 'bg-sky-500/5 border-sky-500/20'}`}>
                    <div className="flex items-center gap-1.5 mb-0.5" style={{ color: alert.level === 'HIGH' ? COLORS.danger : alert.level === 'MEDIUM' ? COLORS.warn : COLORS.accent }}>
                      <AlertTriangle className="w-3 h-3" /><span className="text-xs font-bold">{alert.title}</span>
                      <span className="text-[10px] font-mono px-1 rounded" style={{ background: 'currentColor', color: '#000' }}>{alert.level}</span>
                    </div>
                    <div className="text-xs text-slate-600 dark:text-slate-400 leading-relaxed">{alert.detail}</div>
                  </div>
                ))
              ) : (
                <div className="bg-emerald-500/5 border border-emerald-500/20 rounded-lg p-2 flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400">
                  <CheckCircle2 className="w-3 h-3" /><span className="text-xs font-bold">当前无活跃告警</span>
                </div>
              )}
            </div>
          )}

          {liveSafety?.safety_advice && (
            <div className="pt-2 border-t border-slate-300/50 dark:border-white/5 text-xs text-slate-600 dark:text-slate-400 leading-relaxed">
              <span className="text-sky-600 dark:text-sky-400 font-bold flex items-center gap-1 mb-1"><Info className="w-3 h-3" /> 安全建议</span>
              {liveSafety.safety_advice}
            </div>
          )}
        </div>
      </DraggablePanel>

      {/* ========== 右下角：全球情报网络（风险等级以后端情报为准，前端不自行标定） ========== */}
      <DraggablePanel
        className="radar-network-panel absolute bottom-8 right-6 z-20 w-[280px] hidden md:flex pointer-events-auto radar-panel"
        style={panelStyle}
        icon={<Globe2 className="w-3.5 h-3.5" style={{ color: COLORS.accent }} />}
        title={<span className="text-[13px] font-bold" style={{ color: COLORS.accent }}>全球情报网络</span>}
      >
        <div className="px-4 py-2.5 border-b border-slate-300/50 dark:border-white/5 flex items-center justify-between">
          <span className="text-[11px] text-slate-500 font-mono">风险等级以后端情报为准</span>
          <span className="text-[11px] text-slate-500 font-mono">{globalRegionCount} 区域 · {dynamicGlobalCities.length} 城市</span>
        </div>
        <div className="p-3 space-y-2 max-h-[250px] overflow-y-auto custom-scrollbar">
          <div className="bg-slate-200/60 dark:bg-slate-800/20 border border-dashed border-slate-700/50 rounded-lg p-2.5">
            <div className="flex items-center justify-between text-[10px] font-bold text-slate-500"><span>全球目录覆盖</span><span>{globalRegionCount} 个区域</span></div>
            <div className="mt-1.5 flex flex-wrap gap-1">{globalRegionSummary.map(([region, count]) => <span key={region} className="rounded bg-slate-300/60 px-1.5 py-0.5 text-[9px] text-slate-600 dark:bg-white/10 dark:text-slate-400">{region} {count}</span>)}</div>
            <p className="mt-2 text-[10px] leading-4 text-slate-500">城市点是全球地理目录，不等同于实时风险。只有带供应商、时间戳的信号才进入风险判断。</p>
          </div>
          {dynamicGlobalCities.slice(0, 10).map(city => (
            <div key={city.name} className="flex items-center justify-between py-1.5 border-b border-slate-300/50 dark:border-white/5 last:border-0">
              <div className="flex items-center gap-2">
                <div className="w-1.5 h-1.5 rounded-full bg-slate-500" />
                <span className="text-xs text-slate-700 dark:text-slate-300">{city.name}</span>
              </div>
              <span className="text-[11px] text-slate-500">{city.region}</span>
            </div>
          ))}
        </div>
      </DraggablePanel>

      {/* ========== 底部滚动信息条 ========== */}
      <div className="radar-ticker absolute bottom-0 left-0 right-0 z-20 h-8 pointer-events-none" style={{ background: 'linear-gradient(180deg, transparent, rgba(2,6,23,0.95))', borderTop: '1px solid rgba(56,189,248,0.1)' }}>
        <div className="h-full flex items-center px-4 overflow-hidden">
          <div className="flex items-center gap-8 animate-marquee whitespace-nowrap">
            {[...tickerItems, ...tickerItems].map((item, i) => (
              <div key={i} className="flex items-center gap-1.5">
                <item.icon className="w-3 h-3" style={{ color: item.color }} />
                <span className="text-xs tracking-wide" style={{ color: item.color, fontFamily: RADAR_FONT.data }}>{item.text}</span>
                <span className="text-xs text-slate-700 mx-1">|</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ========== 3D 地球 ========== */}
      <div ref={globeHostRef} className="radar-globe-stage absolute inset-0 z-0" aria-label="交互式 3D 地球">
        <Globe
          key={globeKey}
          ref={globeRef}
          width={dimensions.width}
          height={dimensions.height}
          globeImageUrl={globeTexture}
          bumpImageUrl={bumpTexture}
          showAtmosphere={true}
          atmosphereColor={globeAtmosphereColor}
          atmosphereAltitude={0.22}
          backgroundColor="rgba(0,0,0,0)"

          pointsData={[...(showRouteLayer ? pointsData : []), ...(showSignalLayer ? signalPoints : []), ...(showContextLayer ? contextCities : []), ...(showGlobalNetwork ? globalRiskPoints : [])]}
          pointLat="lat"
          pointLng="lng"
          pointColor="color"
          pointAltitude={0.04}
          pointRadius={(d: any) => (d.size || 0.5) * 0.2}
          pointsMerge={false}

          arcsData={arcsData}
          arcStartLat="startLat"
          arcStartLng="startLng"
          arcEndLat="endLat"
          arcEndLng="endLng"
          arcColor="color"
          arcStroke="stroke"
          arcDashLength={0.4}
          arcDashGap={0.2}
          arcDashInitialGap={() => Math.random()}
          arcDashAnimateTime={3000}
          arcCircularResolution={128}
          arcAltitudeAutoScale={0.45}

          ringsData={ringsData}
          ringLat="lat"
          ringLng="lng"
          ringColor="color"
          ringMaxRadius="maxR"
          ringPropagationSpeed="propagationSpeed"
          ringRepeatPeriod="repeatPeriod"

          // 👑 始终可见的标签：路线节点显示名称+成本+风险，周边城市显示名称
          htmlElementsData={[...(showRouteLayer ? pointsData : []), ...(showSignalLayer ? signalPoints : []), ...(showContextLayer ? contextCities : []), ...(showGlobalNetwork ? globalRiskPoints : [])]}
          htmlLat="lat"
          htmlLng="lng"
          htmlAltitude={0.06}
          htmlElement={(d: any) => {
            const el = document.createElement('div');
            el.style.pointerEvents = 'auto';
            el.style.cursor = 'pointer';
            el.style.transform = 'translate(-50%, -50%)';
            const isSel = d.index === selectedIndex;
            const isRoute = d.isRoutePoint === true;
            const isContext = d.isContextCity === true;
            const isGlobal = d.isGlobalCity === true;
            const color = /^#[\da-f]{3,8}$/i.test(String(d.color || '')) ? String(d.color) : COLORS.accent;
            const size = isContext ? 4 : isGlobal ? 5 : 10;
            const glowSize = isRoute ? 16 : 0;
            // Showing every route card at once makes dense itineraries unreadable.
            // Keep the first/last/selected cards visible and expose the rest on hover.
            const routeLabelVisible = isRoute && (compactViewport
              ? (isSel || d.index === 0)
              : (isSel || d.index === 0 || d.index === pointsData.length - 1));
            const safeName = escapeHtml(d.name);
            const safeCost = escapeHtml(d.cost);
            const safeDesc = escapeHtml(d.desc);
            const safeTime = escapeHtml(d.time);
            const safeRegion = escapeHtml(d.region);
            const riskScore = Number.isFinite(Number(d.riskScore)) ? Math.max(0, Math.round(Number(d.riskScore))) : 0;

            // 👑 始终可见的标签：规划节点大标签 vs 周边城市小标签 vs 参考城市小标签
            const labelHtml = routeLabelVisible ? `
              <div style="position:absolute;bottom:100%;left:50%;transform:translateX(-50%);margin-bottom:${size + 6}px;white-space:nowrap;pointer-events:none;z-index:10;">
                <div style="background:rgba(2,8,23,0.96);border:2px solid ${color}aa;border-radius:8px;padding:5px 10px;backdrop-filter:blur(16px);display:flex;flex-direction:column;align-items:center;gap:3px;box-shadow:0 4px 24px rgba(0,0,0,0.6),0 0 16px ${color}30,0 0 40px ${color}10;">
                  <span style="font-size:14px;font-weight:900;color:${color};font-family:${RADAR_FONT.display};max-width:120px;overflow:hidden;text-overflow:ellipsis;letter-spacing:1px;text-shadow:0 0 8px ${color}60;">${safeName}</span>
                  <div style="display:flex;align-items:center;gap:4px;flex-wrap:wrap;justify-content:center;">
                    ${safeCost ? `<span style="font-size:11px;font-weight:800;color:#fbbf24;font-family:${RADAR_FONT.data};text-shadow:0 0 6px rgba(251,191,36,0.5);">${safeCost}</span>` : ''}
                    ${riskScore > 0 ? `<span style="font-size:9px;font-weight:700;padding:2px 5px;border-radius:4px;background:${riskScore > 30 ? '#f43f5e30' : '#10b98130'};color:${riskScore > 30 ? '#f43f5e' : '#10b981'};font-family:${RADAR_FONT.data};">风险${riskScore}</span>` : ''}
                    <span style="font-size:9px;font-weight:700;padding:2px 5px;border-radius:4px;background:#38bdf830;color:#38bdf8;font-family:${RADAR_FONT.data};">#${d.index + 1}</span>
                    <span style="font-size:8px;font-weight:800;padding:2px 5px;border-radius:4px;background:${color}20;color:${color};font-family:${RADAR_FONT.display};letter-spacing:1px;">规划地点</span>
                  </div>
                </div>
              </div>
            ` : isContext ? `
              <div style="position:absolute;bottom:100%;left:50%;transform:translateX(-50%);margin-bottom:${size + 2}px;white-space:nowrap;pointer-events:none;z-index:5;opacity:0.6;">
                <div style="background:rgba(2,8,23,0.5);border:1px solid rgba(100,116,139,0.3);border-radius:4px;padding:1px 5px;backdrop-filter:blur(4px);display:flex;align-items:center;gap:3px;">
                  <span style="font-size:8px;font-weight:500;color:#94a3b8;font-family:${RADAR_FONT.display};max-width:50px;overflow:hidden;text-overflow:ellipsis;">${safeName}</span>
                </div>
              </div>
            ` : isGlobal ? `
              <div style="position:absolute;bottom:100%;left:50%;transform:translateX(-50%);margin-bottom:${size + 2}px;white-space:nowrap;pointer-events:none;z-index:5;opacity:0.7;">
                <div style="background:rgba(2,8,23,0.7);border:1px solid ${color}40;border-radius:4px;padding:2px 5px;backdrop-filter:blur(6px);display:flex;align-items:center;gap:3px;">
                  <span style="font-size:8px;font-weight:600;color:${color};font-family:${RADAR_FONT.display};max-width:60px;overflow:hidden;text-overflow:ellipsis;">${safeName}</span>
                </div>
              </div>
            ` : '';

            el.innerHTML = `
              <div style="position:relative;display:flex;align-items:center;justify-content:center;" class="ws-globe-point">
                ${labelHtml}
                ${isRoute ? `<div style="position:absolute;bottom:${size}px;left:50%;transform:translateX(-50%);width:2px;height:${glowSize * 3}px;background:linear-gradient(to top, ${color}, ${color}00);border-radius:2px;z-index:0;opacity:0.5;animation:ws-pillar-glow 2s ease-in-out infinite;"></div>` : ''}
                <div style="display:flex;align-items:center;justify-content:center;transition:all 0.3s;" class="ws-point-dot">
                  <div style="position:absolute;width:${size * 2.5}px;height:${size * 2.5}px;border-radius:50%;border:${isRoute ? '2.5px' : '1px'} solid ${color};opacity:${isRoute ? 0.8 : isContext ? 0.2 : 0.4};animation:ws-pulse-ring 2s ease-out infinite;"></div>
                  <div style="position:absolute;width:${size * 3.8}px;height:${size * 3.8}px;border-radius:50%;border:1px solid ${color};opacity:${isRoute ? 0.2 : isContext ? 0.04 : 0.06};animation:ws-pulse-ring 2s ease-out 0.5s infinite;"></div>
                  <div style="width:${size}px;height:${size}px;border-radius:50%;background:${color};box-shadow:0 0 ${size * 1.5}px ${color},0 0 ${size * 3}px ${color}80;border:${isRoute ? '2.5px' : '1px'} solid rgba(255,255,255,${isRoute ? 0.95 : isContext ? 0.4 : 0.7});position:relative;z-index:1;"></div>
                </div>
                <div style="position:absolute;bottom:calc(100% + 6px);left:50%;transform:translateX(-50%) translateY(4px);opacity:0;pointer-events:none;transition:all 0.25s;z-index:50;white-space:nowrap;" class="ws-point-card">
                  <div style="background:rgba(8,15,30,0.95);border:1px solid ${color}50;border-radius:8px;padding:6px 10px;box-shadow:0 8px 32px rgba(0,0,0,0.6);backdrop-filter:blur(16px);">
                    <div style="font-size:10px;font-weight:900;color:${color};line-height:1.2;font-family:${RADAR_FONT.display};margin-bottom:2px;">${safeName}</div>
                    ${isContext ? `<div style="font-size:8px;color:#94a3b8;font-family:${RADAR_FONT.data};">${safeRegion} · 周边城市</div>` :
                    isGlobal ? `<div style="font-size:8px;color:#94a3b8;font-family:${RADAR_FONT.data};">${safeRegion} · 参考城市</div>` :
                    `<div style="font-size:8px;color:#94a3b8;font-family:${RADAR_FONT.data};">${safeDesc.slice(0, 25) || '航点'}${safeTime ? ' · ' + safeTime : ''}${safeCost ? ' · ' + safeCost : ''}</div>`}
                  </div>
                </div>
              </div>
            `;
            el.onclick = (e: Event) => {
              e.stopPropagation?.();
              if (d.index !== undefined && d.isRoutePoint) focusOnPoint(d.index);
            };
            return el;
          }}

          onGlobeReady={() => {
            if (globeRef.current) {
              try {
                const g = globeRef.current;
                if (g.controls) {
                  const ctrl = g.controls();
                  if (ctrl) { ctrl.autoRotate = autoRotate; ctrl.autoRotateSpeed = 0.4; ctrl.enableDamping = true; ctrl.dampingFactor = 0.1; }
                }
                if (g.globeMaterial) {
                  const mat = g.globeMaterial();
                  if (mat) { mat.emissive = { r: 0.05, g: 0.1, b: 0.16 }; mat.emissiveIntensity = 0.55; }
                }
              } catch {}
            }
          }}
          onGlobeClick={() => {}}
        />
      </div>

      {(textureStatus === 'loading' || textureStatus === 'fallback' || webglLost) && (
        <div className="radar-render-status absolute left-1/2 -translate-x-1/2 top-24 sm:top-20 z-40 pointer-events-auto" role="status">
          <div className="flex items-center gap-2 rounded-full border px-3 py-2 text-[11px] font-bold shadow-lg backdrop-blur-xl" style={{ background: COLORS.labelBg, borderColor: webglLost ? `${COLORS.danger}66` : `${COLORS.accent}44`, color: webglLost ? COLORS.danger : COLORS.muted }}>
            {webglLost ? <AlertTriangle className="w-3.5 h-3.5" /> : textureStatus === 'loading' ? <RotateCw className="w-3.5 h-3.5 animate-spin" /> : <Info className="w-3.5 h-3.5" />}
            <span>{webglLost ? '3D 渲染上下文已中断' : textureStatus === 'loading' ? '正在加载地球纹理…' : '地球纹理不可达，已启用离线视图'}</span>
            {(webglLost || textureStatus === 'fallback') && (
              <button
                type="button"
                onClick={() => { setWebglLost(false); retryTexture(); setGlobeKey((value) => value + 1); }}
                className="ml-1 rounded-md px-2 py-1 text-[10px] font-black transition-colors"
                style={{ background: `${COLORS.accent}18`, color: COLORS.accent }}
              >
                重试
              </button>
            )}
          </div>
        </div>
      )}

      <style>{radarSharedCss(mode)}</style>
    </div>,
    document.body,
  );
}
