# Spring hero v2

Place the rendered files here:

- `spring-desktop.webp` — 2048×1152 or larger
- `spring-mobile.webp` — 900×1400 or larger
- `spring-hero.glb` — optional enhanced 3D scene

Generate them from the repository root with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\render-spring-hero.ps1
```

The frontend tries these v2 WebP files first, then the v2 SVG composite, and finally the v1 fallback assets.
