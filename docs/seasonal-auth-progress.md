# 四季门户实施进度（未完成）

## 已有证据

- 新门户布局、统一季节控制器、日期/手动/24秒演示、减少动态与隐藏/busy暂停已实现。
- 一次生产构建成功；全量ESLint成功；Node测试244/244成功。
- 新增seasonal-auth浏览器测试4/4成功：输入保持和错误提示、360px注册/重置可达、图片失败兜底、减少动态键盘交互。
- 上述浏览器测试发生在场景素材生成前，验证的是SVG兜底，不是树屋主视觉。
- 构建后的少量CSS与资源加载超时处理仍需最终重新构建验证。

## 当前后台工作

- 旧pwsh-1bf62eed75194731镜像超时已终止收集；blender.zip是残缺分段文件，不可使用。
- 新镜像完整下载work/seasonal-tools/blender-verified.zip已完成，SHA256 = 6b657c8bdd3a7b65b07b9e1ae17eb4be7dd4aa23121da7f3d3354fc2551330a7，与发布校验值一致；job pwsh-62528355a5914db3已收集。
- PowerShell解压过慢已停；Python zipfile解压6309项成功。Blender路径 work/seasonal-tools/runtime/blender-4.5.3-windows-x64/blender.exe。
- pwsh-69d4bde9cf3f46de秋季preview成功，8m56s，1280x720，母场景已保存。旧相机几何边界left=-.021,right=.49,bottom=-.018,top=1.136，有裁切。
- 已修改create_scene.py桌面ortho_scale=34,shift_x=.20,shift_y=.033。pwsh-15b0b301033242f4修正版13秒渲染成功且已收集；几何边界left=.065,right=.464,bottom=.037,top=.937无裁切。autumn-preview.png已present给用户，但模型无法审图。
- pwsh-2a4f8d13025046c2已停并收集：发现旧移动机位裁切。已修正mobile ortho30 shift_x=-.043 shift_y=-.08，经inspect_mobile.py验证bounds left=.103 right=.898 bottom=.330 top=.891。
- pwsh-072c2d21be3c470b：修正版 --full --all --layers --samples48 四季桌面/移动24张渲染，日志work/seasonal-art/full-render.log。不要重复开渲染或修改脚本/母场景。完成后check_assets.py验证24图尺寸/alpha，再export_web.py压缩。
- pwsh-a42af27003574053：9项seasonal-auth全部通过，已收集；当前仍无真实图接入。
- 已新增verify_web_assets.mjs验证WebP头/清单体积，须export_web后运行。
- SeasonalExperience新增image.fetchPriority='high'，需最终build包含。
- 最新build pwsh-015fba40ea3e45d7已成功并收集。
- pwsh-48dfe31bc517497a几何检查已完成收集，506 objects/501 mesh/27 materials/50161 vertices。
- pwsh-5ec68790560c42d4：新增motionGuard和saveData后的定向lint/typecheck。
- 7组视口注册按钮可达无横向溢出测试通过，job已收集。
- pwsh-ed76db5f096a4d15：已完成，最新分层场景TypeScript和定向lint通过。
- pwsh-d5bb4081ed4d46be：分层组件生产构建成功，已收集。
- pwsh-80d3fec1e999407e：最新专项7/7成功，已收集。增加成功poster失败保留/重试、两阶段重置、24秒演示焦点暂停。
- 当前模型不支持图像输入，read_image已明确拒绝，唯一可选子模型也相同；不能宣称人工审图。work/seasonal-review/mobile-reset.png已生成可供用户审阅。
- core-flow + refactor-regression已完成26/28。图谱mock缺query匹配、预算文案断言过期，均已登录路径不挂载AuthPortal；未改无关业务或测试。
- 第二次build成功，包含资源重试按钮；之后4项seasonal-auth仍全通过。新增分层组件还需build。
- 新增LayeredSeasonScene：桌面可选背景/透明主体解码全成功后启用±6px鼠标视差，busy/隐藏/减少动态不运行；素材尚未生成。
- 脚本现支持 --layers 输出poster/background/subject。export_web.py保留主体alpha。

## 接续工作

1. 收集pwsh-58be7d342d1a488c解压结果（不要重复解压）。glob work/seasonal-tools/runtime/**/blender.exe，然后执行blender -b -t 8 --python scripts/seasonal/create_scene.py -- --preview --season autumn --samples 20。下载已验证，不再等待旧下载job。
2. 用read_image查看work/seasonal-art/autumn-preview.png；调整母场景与网页构图，不能未经看图宣称画质完成。
3. 生成四季桌面/手机：--full --all，透明主体可选--subjects（会增加渲染时间）。原始脚本可用--only desktop/mobile缩小范围。
4. 用 bundled Python执行scripts/seasonal/export_web.py，要求8张完整原图，生成public/hero/v1资源及manifest。
5. 加入分层动态/有限视差（当前主要为poster转场和CSS粒子，尚非方案全部4–6层），加载失败重试和原页面回退配置尚待完善。
6. 最终build、浏览器四季截图、移动/注册/重置/减少动态和资产失败验证；完整回归和对比度检查。
7. 部署文档已创建，但未发布公网。用户强调已部署，禁止未经授权执行deploy脚本或修改生产配置。

## 已知边界

- 用户当前没有重新上传参考图片；没有视觉对照，不能承诺达到参考建模精度。
- .blend与首张秋季预览已实际生成，正在修正相机裁切；仍没有进行真实移动设备性能测试。
- 便携工具与work中间产物不要打入生产包。
