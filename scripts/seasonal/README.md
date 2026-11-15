# 四季云上驿站 · 原创 Blender 母场景

## 环境及运行

Blender **4.5 LTS**，内置 Python，无插件、无外部模型、无贴图下载。渲染器固定 **Cycles CPU**，支持无显示器 background 模式。不需要 GPU/CUDA。以下从项目根目录运行；将 `blender` 替换为 portable 的完整可执行路径。

```powershell
# 第一轮：秋季低采样构图预览，先保存可编辑母场景，再渲染
blender -b -t 8 --python scripts/seasonal/create_scene.py -- --preview --season autumn --samples 20

# 四季完整尺寸 + 四季透明主体层
blender -b -t 8 --python scripts/seasonal/create_scene.py -- --full --all --subjects --samples 64

# 竖屏低采样检查：720 × 1280
blender -b -t 8 --python scripts/seasonal/create_scene.py -- --preview --only mobile --samples 20

# 预算有限时先输出秋季单个方向，只有一张PNG
blender -b -t 8 --python scripts/seasonal/create_scene.py -- --full --season autumn --only desktop --samples 48
blender -b -t 8 --python scripts/seasonal/create_scene.py -- --full --season autumn --only mobile --samples 48

# 分批完成四季桌面，无透明层：四张PNG
blender -b -t 8 --python scripts/seasonal/create_scene.py -- --full --all --only desktop --samples 48
```

默认输出目录由脚本路径解析，为项目 `work/seasonal-art`，不依赖启动目录；可用 `--output "D:/absolute/output"` 覆盖。未传 `--full` 时默认预览。`--preview` 是显式说明标志。`--subjects` 才会附加透明主体层；`--all` 才会渲染全部四季，否则仅渲染 `--season`（默认 autumn）。`--only mobile` 同样适用于预览，输出 `autumn-mobile-preview.png`。

## 输出约定

| 文件 | 内容 |
| --- | --- |
| `autumn-preview.png` | 1280 × 720 秋季桌面预览（亦可用 `--season` 选择其他季节） |
| `{season}-desktop.png` | 1920 × 1080，主体在左，右侧表单安全区 |
| `{season}-mobile.png` | 1080 × 1920，主体上移，下方给表单 |
| `{season}-{desktop/mobile}-subject.png` | 对应构图透明主体，关闭远山云海背景；保留世界照明与灯光 |
| `seasonal-station.blend` | 可编辑母场景，完整模式结束后回到秋季桌面镜头 |

`season` 为 `spring / summer / autumn / winter`。使用 `--full --all --subjects` 总计 **16 张 PNG**；默认 `--full` 仅当前季节双方向 2 张，`--full --only desktop` 仅 1 张。首次母场景保存发生在渲染之前，即使耗时中断仍能打开 `.blend` 检查。重跑会覆盖同名输出，Blender 可能保留 `.blend1` 备份。

## 几何与材质

- 固定随机种子 `127`；四季使用同一模型和机位，不重新生成场景。
- 不规则多层三角岩岛、矿石突起、苔藓表土、可见盘根与向下垂落根须。
- 多段锥形弯曲树干/主枝/细分叉；数千枚有折面的独立杏仁状树叶，按六组材质合并网格降低 CPU 场景开销。
- 小屋由厚实独立木板、露出角柱、三角山墙板、重叠圆角陶瓦、屋脊瓦构成；窗框/窗棂/窗台/百叶窗、琥珀发光玻璃、门板与黄铜把手、逐块露台板/宽台阶/栏杆均独立可编辑。
- 草叶、草间石、帆布背包/口袋/皮带/铺盖卷、双向旅行路牌；岩石烟囱带灰缝。
- 独立 `WINTER` 集合有屋顶积雪、树冠积雪、岛面雪毯、边缘雪堆、烟囱雪帽。冬季隐藏叶片；雪不是单纯把夏季材质涂白。
- 材质包含 Noise → ColorRamp 基色、Noise → Bump 法线、Noise → MapRange 粗糙度；木材/岩石/陶瓦/布料使用不同频率与粗糙度。无图片纹理。
- 远山与云海采用轻量不规则实体，避免 CPU 体积积分产生不可控时间；背景层可以整体隐藏，得到透明主体层。
- 柔光主灯、冷天空补光、树冠暖色轮廓灯和窗外暖光。AgX 色调映射、Cycles 去噪。

## 编辑和季节控制

集合按 `STATION`、`ATMOSPHERE`、`CANOPY`、`WINTER` 命名。脚本末尾 `season()` 控制叶片六色、草地颜色、主光色和雪/叶集合显隐；`composition()` 控制桌面/竖屏正交相机。完整渲染始终调用这两个函数，不换模型。透明层隐藏 `ATMOSPHERE` 集合并启用 film transparency。

## 真正的 2.5D 分层（按需开启）

```powershell
# 先检查低成本三层套件：poster + clean background + RGBA subject
blender -b -t 8 --python scripts/seasonal/create_scene.py -- --preview --only desktop --layers --samples 20
# 单季单方向完整三张
blender -b -t 8 --python scripts/seasonal/create_scene.py -- --full --season autumn --only desktop --layers --samples 48
# 四季双方向完整三张，共24张
blender -b -t 8 --python scripts/seasonal/create_scene.py -- --full --all --layers --samples 64
```

`--layers` 才会附加独立 `{season}-{view}-background.png`（纯 ENV 云海/远山/天空，不含主体）及 `{season}-{view}-subject.png`（透明 RGBA，主体/当季叶或雪）。静态 `{season}-{view}.png` poster 继续始终输出，用于首屏兜底。旧 `--subjects` 仅附加透明主体，保留兼容；不输出背景。两标志同时传入不重复渲染。

预览分层文件在名称中添加 `-preview`，例如 `autumn-desktop-preview-background.png` / `autumn-desktop-preview-subject.png`，避免低分辨率预览覆盖正式文件。每组三张使用完全相同的分辨率、相机和投影，网页可在图层解码完成后，把 poster 切换成 background + subject，给两层不同幅度的轻微位移。应预留移动裁切边缘并遵从 reduced-motion；不要将 poster 与 subject 同时叠加，否则会重复显示主体。

灯光和相机处于场景根集合，任何图层输出都不隐藏它们。背景渲染会隐藏 STATION/CANOPY/WINTER，主体渲染仅隐藏 ATMOSPHERE，保留当前季节显隐。每次输出用 try/finally 恢复之前集合状态，防止冬雪泄漏到其他季节。独立重渲染的间接反射可能令合成与 poster 有细微差异，不保证逐像素完全一致。

## 时间预算和检查状态

默认预览 20 samples，完整 64 samples，最大反弹 6、漫反射与光泽反弹各 3。CPU 性能差异很大，不能提前保证分钟数；先根据一次预览耗时估计完整 16 次渲染。可将完整采样降至 32–48，或通过 `--only` 分批渲染。不要同时运行多份全量 CPU 渲染导致争用。

脚本已通过 Python AST 语法检查；实际 Blender 4.5 执行、首张构图检查和采样质量由主代理渲染后验证。此说明不代表已达到参考图画质。优先检查树冠是否截边、桌面右侧是否平静、竖屏上部主体是否完整以及雪是否与屋顶正确贴合。

## 原创来源与范围

全部几何、材质、布局和配色由本脚本程序化构建，没有导入第三方照片、模型、字体、贴图，也没有使用任何参考图作为输出底图。本任务仅生成本地艺术资产及可编辑 Blender 源文件；不连接、修改或部署任何公网项目。
