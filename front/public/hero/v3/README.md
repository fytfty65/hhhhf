# Hero v3 hybrid asset contract

This folder is the high-fidelity asset slot for the seasonal login hero.

The frontend checks these files before the procedural v2 scene:

- `<season>-cinematic.png` — desktop cinematic background plate for spring/summer/autumn/winter
- `<season>-cinematic-mobile.png` — mobile crop for each season
- `spring-foreground.glb` — optional interactive foreground asset
- `spring-depth.webp` — optional depth map for future parallax/compositing

The current repository intentionally keeps v2 as a fallback until scanned/PBR assets or a generated cinematic plate are supplied. Add the final v3 files here without changing the login page or season controller.
