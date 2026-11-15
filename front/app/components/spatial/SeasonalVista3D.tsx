'use client';

/**
 * SeasonalVista3D — 登录门户的 Three.js 四季山野远景（程序化生成，零外部资源）
 *
 * 目标（对照参考稿）：立体感 · 空旷感 · 灵动感
 * - 立体感：真实地形网格（平面位移 + 顶点灰阶 + 方向光 + 平直着色），山体有
 *   受光面/背光面的明暗关系，不是平面色带；
 * - 空旷感：三层纵深——前景云海（半透明云纹理平面）→ 中景山脊 → 远景雾化山影，
 *   Fog 让远山融入天色，形成空气透视；
 * - 灵动感：相机缓慢漂移 + 鼠标视差、云海缓慢流动、季节粒子飘落；
 *   四季色板（雾/日光/山体/粒子/天空）平滑插值轮播。
 *
 * 工程约束：
 * - three 已在依赖内；组件经 dynamic(ssr:false) 懒加载，避免登录首帧阻塞；
 * - 纹理全部用 canvas 程序化生成，CSP 零改动；WebGL 不可用时安静降级
 *   （父级保留 SVG 兜底场景）；
 * - prefers-reduced-motion：不轮播、不漂移、粒子静止；页面隐藏时暂停渲染。
 */

import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';

export const VISTA_SEASONS = ['spring', 'summer', 'autumn', 'winter'] as const;
export type VistaSeason = (typeof VISTA_SEASONS)[number];

interface Palette {
  fog: string;
  sun: string;
  terrain: string;
  light: string;
  particle: string;
  particleSize: number;
}

const PALETTES: Record<VistaSeason, Palette> = {
  spring: { fog: '#f7e9d8', sun: '#ffe2b0', terrain: '#c9ab8b', light: '#fff3df', particle: '#f0b3a2', particleSize: 9 },
  summer: { fog: '#e7efe0', sun: '#ffedbb', terrain: '#8aa87c', light: '#fff5da', particle: '#f6f1d8', particleSize: 6 },
  autumn: { fog: '#f7e0c4', sun: '#ffcf85', terrain: '#c08a56', light: '#ffe8c5', particle: '#d78746', particleSize: 10 },
  winter: { fog: '#e5ecf2', sun: '#eef4fa', terrain: '#b6c4d1', light: '#f4f8fc', particle: '#ffffff', particleSize: 7 },
};

/* ------------------------------ 噪声（确定性） ------------------------------ */

function hash2(x: number, y: number): number {
  const v = Math.sin(x * 127.1 + y * 311.7) * 43758.5453;
  return v - Math.floor(v);
}

function valueNoise(x: number, y: number): number {
  const xi = Math.floor(x);
  const yi = Math.floor(y);
  const xf = x - xi;
  const yf = y - yi;
  const u = xf * xf * (3 - 2 * xf);
  const v = yf * yf * (3 - 2 * yf);
  const a = hash2(xi, yi);
  const b = hash2(xi + 1, yi);
  const c = hash2(xi, yi + 1);
  const d = hash2(xi + 1, yi + 1);
  return a * (1 - u) * (1 - v) + b * u * (1 - v) + c * (1 - u) * v + d * u * v;
}

function fbm(x: number, y: number, octaves: number): number {
  let sum = 0;
  let amp = 1;
  let freq = 1;
  let norm = 0;
  for (let i = 0; i < octaves; i += 1) {
    sum += amp * valueNoise(x * freq, y * freq);
    norm += amp;
    amp *= 0.5;
    freq *= 2;
  }
  return sum / norm;
}

/** 山脊噪声：形成尖锐起伏的峰线（0~1） */
function ridged(x: number, y: number): number {
  let sum = 0;
  let amp = 1;
  let freq = 1;
  let norm = 0;
  for (let i = 0; i < 4; i += 1) {
    const n = 1 - Math.abs(valueNoise(x * freq, y * freq) * 2 - 1);
    sum += amp * n * n;
    norm += amp;
    amp *= 0.5;
    freq *= 2;
  }
  return sum / norm;
}

function smoothstep(a: number, b: number, value: number): number {
  const t = THREE.MathUtils.clamp((value - a) / (b - a), 0, 1);
  return t * t * (3 - 2 * t);
}

function terrainHeight(x: number, z: number): number {
  const d = 400 - z; // 相机前方距离：近 0 → 远 3800
  const ridge = Math.pow(ridged(x * 0.00085 + 13.1, z * 0.00085 + 7.3), 1.35);
  const ridge2 = Math.pow(ridged(x * 0.0021 + 41.7, z * 0.0021 + 19.3), 1.25);
  const ridge3 = Math.pow(ridged(x * 0.0045 + 83.9, z * 0.0045 + 57.1), 1.1);
  const broad = fbm(x * 0.00042, z * 0.00042, 3);
  const detail = fbm(x * 0.006, z * 0.006, 2) * 45;
  const rise = smoothstep(260, 1300, d); // 相机前方留出山谷（由云海覆盖）
  const fall = 1 - 0.34 * smoothstep(2400, 3300, d); // 最远处压低，交给雾融合
  const base = 8 + (ridge * 0.72 + ridge2 * 0.34 + ridge3 * 0.18 + broad * 0.26) * 640 * rise * fall;
  return Math.max(2, base + detail * rise);
}

/* ------------------------------ 程序化纹理 ------------------------------ */

function makeCloudTexture(): THREE.CanvasTexture {
  const size = 512;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (ctx) {
    ctx.clearRect(0, 0, size, size);
    for (let i = 0; i < 26; i += 1) {
      const x = Math.random() * size;
      const y = Math.random() * size;
      const r = 60 + Math.random() * 150;
      const gradient = ctx.createRadialGradient(x, y, 0, x, y, r);
      gradient.addColorStop(0, 'rgba(255,255,255,0.55)');
      gradient.addColorStop(0.6, 'rgba(255,255,255,0.22)');
      gradient.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.fillStyle = gradient;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(2.4, 2.4);
  return texture;
}

function makeSunTexture(): THREE.CanvasTexture {
  const size = 256;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (ctx) {
    const gradient = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    gradient.addColorStop(0, 'rgba(255,255,255,0.95)');
    gradient.addColorStop(0.3, 'rgba(255,244,224,0.55)');
    gradient.addColorStop(0.65, 'rgba(255,236,205,0.16)');
    gradient.addColorStop(1, 'rgba(255,236,205,0)');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, size, size);
  }
  return new THREE.CanvasTexture(canvas);
}

function makeDotTexture(): THREE.CanvasTexture {
  const size = 64;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (ctx) {
    const gradient = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    gradient.addColorStop(0, 'rgba(255,255,255,1)');
    gradient.addColorStop(0.45, 'rgba(255,255,255,0.65)');
    gradient.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, size, size);
  }
  return new THREE.CanvasTexture(canvas);
}

function makeBalloonTexture(): THREE.CanvasTexture {
  const width = 160;
  const height = 200;
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  if (ctx) {
    const envelope = ctx.createLinearGradient(0, 10, 0, 112);
    envelope.addColorStop(0, '#f8b26a');
    envelope.addColorStop(0.55, '#e2622f');
    envelope.addColorStop(1, '#b23c14');
    ctx.fillStyle = envelope;
    ctx.beginPath();
    ctx.ellipse(width / 2, 70, 42, 56, 0, 0, Math.PI * 2);
    ctx.fill();
    // 奶油色竖条
    ctx.fillStyle = 'rgba(255, 243, 220, 0.85)';
    ctx.beginPath();
    ctx.ellipse(width / 2, 70, 10, 56, 0, 0, Math.PI * 2);
    ctx.fill();
    // 吊绳
    ctx.strokeStyle = 'rgba(74, 58, 40, 0.8)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(width / 2 - 26, 118);
    ctx.lineTo(width / 2 - 12, 150);
    ctx.moveTo(width / 2 + 26, 118);
    ctx.lineTo(width / 2 + 12, 150);
    ctx.stroke();
    // 吊篮
    ctx.fillStyle = '#6b4f33';
    ctx.fillRect(width / 2 - 13, 150, 26, 16);
  }
  return new THREE.CanvasTexture(canvas);
}

function makeBirdsTexture(): THREE.CanvasTexture {
  const width = 256;
  const height = 96;
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  if (ctx) {
    ctx.strokeStyle = 'rgba(90, 76, 58, 0.8)';
    ctx.lineWidth = 3;
    ctx.lineCap = 'round';
    const flock = [
      { x: 40, y: 52, s: 1 },
      { x: 96, y: 34, s: 0.8 },
      { x: 150, y: 58, s: 0.6 },
    ];
    for (const bird of flock) {
      const s = bird.s;
      ctx.beginPath();
      ctx.moveTo(bird.x - 16 * s, bird.y);
      ctx.quadraticCurveTo(bird.x - 8 * s, bird.y - 10 * s, bird.x, bird.y);
      ctx.quadraticCurveTo(bird.x + 8 * s, bird.y - 10 * s, bird.x + 16 * s, bird.y);
      ctx.stroke();
    }
  }
  return new THREE.CanvasTexture(canvas);
}

/* ------------------------------ 组件 ------------------------------ */

interface SeasonalVista3DProps {
  intervalMs?: number;
  onSeasonChange?: (season: VistaSeason) => void;
  onReady?: () => void;
}

export default function SeasonalVista3D({ intervalMs = 11000, onSeasonChange, onReady }: SeasonalVista3DProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [season, setSeason] = useState<VistaSeason>('spring');
  const seasonRef = useRef<VistaSeason>('spring');
  const onSeasonChangeRef = useRef(onSeasonChange);
  const onReadyRef = useRef(onReady);

  useEffect(() => {
    onSeasonChangeRef.current = onSeasonChange;
  }, [onSeasonChange]);

  useEffect(() => {
    onReadyRef.current = onReady;
  }, [onReady]);

  useEffect(() => {
    seasonRef.current = season;
    onSeasonChangeRef.current?.(season);
  }, [season]);

  // 季节轮播：页面隐藏时暂停；减少动态时停在当前季节。
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    let timer: number | undefined;
    const tick = () => {
      setSeason((prev) => VISTA_SEASONS[(VISTA_SEASONS.indexOf(prev) + 1) % VISTA_SEASONS.length]);
    };
    const start = () => {
      timer = window.setInterval(tick, intervalMs);
    };
    const stop = () => {
      if (timer !== undefined) {
        window.clearInterval(timer);
        timer = undefined;
      }
    };
    const onVisibility = () => {
      if (document.hidden) stop();
      else {
        stop();
        start();
      }
    };
    start();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      stop();
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [intervalMs]);

  // Three.js 场景：只在客户端初始化一次（回调经 ref 读取，避免重建）。
  useEffect(() => {
    const canvas = canvasRef.current;
    const host = hostRef.current;
    if (!canvas || !host) return;

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
    } catch {
      return; // WebGL 不可用：保留 SVG 兜底场景
    }
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;

    const scene = new THREE.Scene();
    scene.fog = new THREE.Fog(new THREE.Color(PALETTES.spring.fog), 500, 3600);

    const camera = new THREE.PerspectiveCamera(60, 1, 10, 6000);
    camera.position.set(0, 150, 520);
    camera.lookAt(0, 120, -1600);

    // 光照：暖色主光（日光）+ 冷色补光（左侧天光）+ 半球光填充，形成明暗体积
    const sunLight = new THREE.DirectionalLight(new THREE.Color(PALETTES.spring.light), 2.6);
    sunLight.position.set(900, 900, -300);
    scene.add(sunLight);
    const fillLight = new THREE.DirectionalLight(0xaec6dc, 0.5);
    fillLight.position.set(-700, 500, 600);
    scene.add(fillLight);
    const hemi = new THREE.HemisphereLight(0xfff4e8, 0x4a3d30, 0.5);
    scene.add(hemi);

    // 地形：平面位移 + 顶点灰阶（暗谷→亮峰），平直着色呈现低多边形的体积感
    const geometry = new THREE.PlaneGeometry(4600, 3800, 400, 300);
    geometry.rotateX(-Math.PI / 2);
    geometry.translate(0, 0, -1400);
    const position = geometry.attributes.position as THREE.BufferAttribute;
    const colors = new Float32Array(position.count * 3);
    const shadeColor = new THREE.Color();
    for (let i = 0; i < position.count; i += 1) {
      const x = position.getX(i);
      const z = position.getZ(i);
      const height = terrainHeight(x, z);
      position.setY(i, height);
      const shade = THREE.MathUtils.clamp(0.18 + 0.82 * Math.pow(height / 640, 0.62), 0.16, 1);
      shadeColor.setScalar(shade);
      colors[i * 3] = shadeColor.r;
      colors[i * 3 + 1] = shadeColor.g;
      colors[i * 3 + 2] = shadeColor.b;
    }
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    geometry.computeVertexNormals();
    const terrainMaterial = new THREE.MeshStandardMaterial({
      vertexColors: true,
      color: new THREE.Color(PALETTES.spring.terrain),
      roughness: 1,
      metalness: 0,
      flatShading: true,
    });
    const terrain = new THREE.Mesh(geometry, terrainMaterial);
    scene.add(terrain);

    // 前景云海：半透明云纹理平面，铺满画面底部（延伸到相机脚下），
    // 覆盖山谷形成纵深，也让底栏文字落在明亮的云面上
    const cloudTexture = makeCloudTexture();
    cloudTexture.repeat.set(3, 3);
    const cloudGeometry = new THREE.PlaneGeometry(6600, 4400);
    cloudGeometry.rotateX(-Math.PI / 2);
    const cloudMaterial = new THREE.MeshBasicMaterial({
      map: cloudTexture,
      transparent: true,
      opacity: 0.9,
      depthWrite: false,
      fog: true,
      color: 0xffffff,
    });
    const clouds = new THREE.Mesh(cloudGeometry, cloudMaterial);
    clouds.position.set(0, 132, -1200);
    scene.add(clouds);

    // 日光：暖色实心柔光盘（正常混合，浅色天空上也有明确轮廓）
    const sunTexture = makeSunTexture();
    const sunMaterial = new THREE.SpriteMaterial({
      map: sunTexture,
      transparent: true,
      opacity: 0.95,
      depthWrite: false,
      blending: THREE.NormalBlending,
      color: new THREE.Color(PALETTES.spring.sun),
      fog: false,
    });
    const sun = new THREE.Sprite(sunMaterial);
    sun.position.set(480, 540, -2350);
    sun.scale.set(820, 520, 1);
    scene.add(sun);

    // 天空浮云：几片缓慢漂移的云，填补空天空、提供额外的纵深线索
    const skyCloudTexture = cloudTexture.clone();
    skyCloudTexture.needsUpdate = true;
    skyCloudTexture.repeat.set(1, 1);
    skyCloudTexture.offset.set(0, 0);
    const skyCloudDefs = [
      { x: -560, y: 660, z: -2000, sx: 1100, sy: 420, opacity: 0.5, speed: 3.2 },
      { x: 380, y: 720, z: -2150, sx: 900, sy: 340, opacity: 0.42, speed: 2.2 },
      { x: 60, y: 580, z: -1750, sx: 760, sy: 280, opacity: 0.32, speed: 4.0 },
    ];
    const skyClouds: { sprite: THREE.Sprite; speed: number }[] = [];
    for (const def of skyCloudDefs) {
      const material = new THREE.SpriteMaterial({
        map: skyCloudTexture,
        transparent: true,
        opacity: def.opacity,
        depthWrite: false,
        fog: true,
        color: 0xffffff,
      });
      const sprite = new THREE.Sprite(material);
      sprite.position.set(def.x, def.y, def.z);
      sprite.scale.set(def.sx, def.sy, 1);
      scene.add(sprite);
      skyClouds.push({ sprite, speed: def.speed });
    }

    // 天空里的具体符号：热气球（旅行隐喻）+ 一小群飞鸟，缓慢漂移
    const balloonTexture = makeBalloonTexture();
    const balloonMaterial = new THREE.SpriteMaterial({
      map: balloonTexture,
      transparent: true,
      opacity: 0.95,
      depthWrite: false,
      fog: true,
    });
    const balloon = new THREE.Sprite(balloonMaterial);
    balloon.position.set(120, 400, -1750);
    balloon.scale.set(92, 116, 1);
    scene.add(balloon);

    const birdsTexture = makeBirdsTexture();
    const birdsMaterial = new THREE.SpriteMaterial({
      map: birdsTexture,
      transparent: true,
      opacity: 0.75,
      depthWrite: false,
      fog: true,
    });
    const birds = new THREE.Sprite(birdsMaterial);
    birds.position.set(-160, 640, -1900);
    birds.scale.set(320, 120, 1);
    scene.add(birds);

    // 季节粒子：花瓣 / 叶 / 雪（同一点云，按季节换色与尺寸）
    const PARTICLE_COUNT = 460;
    const particlePositions = new Float32Array(PARTICLE_COUNT * 3);
    const particleSpeeds = new Float32Array(PARTICLE_COUNT);
    const particleSeeds = new Float32Array(PARTICLE_COUNT);
    for (let i = 0; i < PARTICLE_COUNT; i += 1) {
      particlePositions[i * 3] = (Math.random() - 0.5) * 2800;
      particlePositions[i * 3 + 1] = Math.random() * 760;
      particlePositions[i * 3 + 2] = -2500 + Math.random() * 2900;
      particleSpeeds[i] = 5 + Math.random() * 15;
      particleSeeds[i] = Math.random() * Math.PI * 2;
    }
    const particleGeometry = new THREE.BufferGeometry();
    particleGeometry.setAttribute('position', new THREE.BufferAttribute(particlePositions, 3));
    const particleTexture = makeDotTexture();
    const particleMaterial = new THREE.PointsMaterial({
      map: particleTexture,
      size: PALETTES.spring.particleSize,
      sizeAttenuation: true,
      transparent: true,
      opacity: 0.85,
      depthWrite: false,
      color: new THREE.Color(PALETTES.spring.particle),
    });
    const particles = new THREE.Points(particleGeometry, particleMaterial);
    scene.add(particles);

    // 颜色插值状态
    const paletteColor = (seasonName: VistaSeason) => ({
      fog: new THREE.Color(PALETTES[seasonName].fog),
      sun: new THREE.Color(PALETTES[seasonName].sun),
      terrain: new THREE.Color(PALETTES[seasonName].terrain),
      light: new THREE.Color(PALETTES[seasonName].light),
      particle: new THREE.Color(PALETTES[seasonName].particle),
    });
    const current = paletteColor('spring');
    const targets = paletteColor('spring');

    const resize = () => {
      const width = host.clientWidth;
      const height = host.clientHeight;
      if (width === 0 || height === 0) return;
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    resize();

    // 鼠标视差（细指针设备才有意义，触屏忽略）
    const pointer = { x: 0, y: 0 };
    const onPointerMove = (event: PointerEvent) => {
      pointer.x = (event.clientX / window.innerWidth) * 2 - 1;
      pointer.y = (event.clientY / window.innerHeight) * 2 - 1;
    };
    if (!reduced) window.addEventListener('pointermove', onPointerMove, { passive: true });

    let raf = 0;
    let last = performance.now();
    let firstRendered = false;

    const loop = (now: number) => {
      const dt = Math.min((now - last) / 1000, 0.05);
      last = now;

      // 季节色板平滑过渡（约 2s）
      const seasonPalette = paletteColor(seasonRef.current);
      targets.fog.copy(seasonPalette.fog);
      targets.sun.copy(seasonPalette.sun);
      targets.terrain.copy(seasonPalette.terrain);
      targets.light.copy(seasonPalette.light);
      targets.particle.copy(seasonPalette.particle);
      const blend = Math.min(1, dt * 1.4);
      current.fog.lerp(targets.fog, blend);
      current.sun.lerp(targets.sun, blend);
      current.terrain.lerp(targets.terrain, blend);
      current.light.lerp(targets.light, blend);
      current.particle.lerp(targets.particle, blend);

      (scene.fog as THREE.Fog).color.copy(current.fog);
      sunMaterial.color.copy(current.sun);
      terrainMaterial.color.copy(current.terrain);
      sunLight.color.copy(current.light);
      particleMaterial.color.copy(current.particle);
      const targetSize = PALETTES[seasonRef.current].particleSize;
      particleMaterial.size += (targetSize - particleMaterial.size) * Math.min(1, dt * 2);

      if (!reduced) {
        // 相机缓慢漂移 + 鼠标视差
        const swayX = Math.sin(now * 0.00005) * 46;
        const swayY = Math.sin(now * 0.000037) * 16;
        camera.position.x += (swayX + pointer.x * 70 - camera.position.x) * 0.02;
        camera.position.y += (150 + swayY + pointer.y * 26 - camera.position.y) * 0.02;
        camera.lookAt(0, 120, -1600);

        // 云海缓慢流动
        cloudTexture.offset.x += dt * 0.0045;
        cloudTexture.offset.y += dt * 0.0012;

        // 天空浮云缓慢漂移
        for (const cloud of skyClouds) {
          cloud.sprite.position.x += cloud.speed * dt;
          if (cloud.sprite.position.x > 2600) cloud.sprite.position.x = -2600;
        }

        // 热气球横向漂移 + 轻微起伏；飞鸟反向慢速掠过
        balloon.position.x += dt * 5;
        if (balloon.position.x > 1700) balloon.position.x = -1700;
        balloon.position.y = 400 + Math.sin(now * 0.0003) * 12;
        birds.position.x -= dt * 2.4;
        if (birds.position.x < -1700) birds.position.x = 1700;

        // 粒子飘落 + 横向摆动
        const positions = particleGeometry.attributes.position as THREE.BufferAttribute;
        for (let i = 0; i < PARTICLE_COUNT; i += 1) {
          let y = positions.getY(i) - particleSpeeds[i] * dt;
          if (y < 0) y = 760;
          positions.setY(i, y);
          positions.setX(i, positions.getX(i) + Math.sin(now * 0.0004 + particleSeeds[i]) * dt * 5);
        }
        positions.needsUpdate = true;
      }

      renderer.render(scene, camera);
      if (!firstRendered) {
        firstRendered = true;
        onReadyRef.current?.();
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
      window.removeEventListener('pointermove', onPointerMove);
      scene.traverse((object) => {
        const mesh = object as THREE.Mesh;
        if (mesh.geometry) mesh.geometry.dispose();
        const material = (mesh as THREE.Mesh).material as THREE.Material | THREE.Material[] | undefined;
        if (Array.isArray(material)) material.forEach((item) => item.dispose());
        else material?.dispose();
      });
      cloudTexture.dispose();
      skyCloudTexture.dispose();
      sunTexture.dispose();
      particleTexture.dispose();
      balloonTexture.dispose();
      birdsTexture.dispose();
      renderer.dispose();
    };
  }, []);

  return (
    <div ref={hostRef} className="vista3d" aria-hidden="true">
      {VISTA_SEASONS.map((item) => (
        <div
          key={item}
          className="vista-sky"
          data-season={item}
          data-active={item === season ? 'true' : 'false'}
        />
      ))}
      <canvas ref={canvasRef} className="vista-canvas" />
    </div>
  );
}
