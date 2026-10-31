import Link from 'next/link';

export const metadata = {
  title: '能力与数据来源 · OmniRoute',
  description: 'OmniRoute 的能力边界、外部依赖与无供应商密钥时的降级行为',
};

const required = [
  {
    name: '大模型 API Key',
    env: 'LLM_API_KEY',
    detail: '唯一硬依赖。AI 服务在导入时构造客户端，缺少 Key 会直接启动失败，而不是等到首次推演才报错。',
    alternative: '可指向任意 OpenAI 兼容端点，包括本地 Ollama（LLM_BASE_URL + LLM_MODEL_NAME）。',
  },
];

const optional = [
  {
    name: '高德地图',
    env: 'AMAP_API_KEY',
    fallback: '没有 Key 时前端仍用公开栅格瓦片显示地图；POI 检索与地理编码能力降级，结果标注来源与估算状态。',
  },
  {
    name: '心知天气',
    env: 'SENIVERSE_API_KEY',
    fallback: '没有 Key 时不伪造实时天气，方案中的天气信息留空或标注为未获取。',
  },
  {
    name: '酒店房价供应商',
    env: 'ROLLINGGO_API_KEY',
    fallback: '供应商保持 not_configured，方案如实写“需要你确认房价”，不假装查过。',
  },
  {
    name: '交易供应商（酒店/票务/交通/餐饮）',
    env: 'gateway/.env 中的 MCP 供应商配置',
    fallback: '下单接口返回 PROVIDER_NOT_CONFIGURED；前端引导到官方渠道，不收款、不承诺出票。',
  },
  {
    name: 'Redis',
    env: 'REDIS_URL',
    fallback: '使用进程内兜底，登录与会话仍可用；不会因为 Redis 缺失导致全站 503。',
  },
];

const noKeyBehaviors = [
  ['天气 / 客流', '标记 estimated 与来源，给出规则估算，不写成实时数据'],
  ['拥挤预测', 'provider_first_fallback_estimate：没有供应商时返回估算，并附建议错峰窗口'],
  ['多语言导览', 'template_only：只覆盖内置短语，未收录的短语明确显示“未收录”'],
  ['AR 锚点', '只输出锚点数据，标注 camera_permission: client_required，不自动打开摄像头'],
  ['价格', '区分官方页、票务平台、网摘估价与供应商实时价；没有来源时标“来源未标注”'],
  ['风险', '本地基线不得升级为 HIGH；拿不到外部证据时如实说明数据缺失'],
];

const boundaries = [
  '平台不代收支付、不承诺出票、不掌握库存；交易在第三方官方渠道完成。',
  '境内公开运营涉及地图商用授权、地图审图号、ICP 备案与隐私政策；本仓库只覆盖本地演示所需的技术实现。',
  '若未来代售、打包旅游或承诺出票，需要对应的旅行社业务经营许可或票务代理授权；当前代码刻意不越过这条线。',
  '所有“已核验 / 估算 / 未知”三态都随结果返回，前端不把估算包装成事实。',
];

export default function CapabilitiesPage() {
  return (
    <main className="min-h-screen bg-slate-50 px-4 py-10 text-slate-900">
      <div className="mx-auto max-w-4xl">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-bold tracking-[0.2em] text-orange-600">OMNIROUTE</p>
            <h1 className="mt-1 text-2xl font-black sm:text-3xl">能力与数据来源</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">
              这套系统按「规划 + 决策 + 官方渠道导流」设计，不依赖商业资质，也不假装拥有供应商能力。下面列出唯一的硬依赖、可选项，以及缺少外部 Key 时会发生什么。
            </p>
          </div>
          <Link href="/" className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-bold text-slate-700 hover:border-orange-300 hover:text-orange-700">
            返回规划大厅
          </Link>
        </header>

        <section className="mt-8 rounded-xl border border-slate-200 bg-white p-5">
          <h2 className="text-sm font-black text-slate-900">唯一硬依赖</h2>
          {required.map((item) => (
            <div key={item.env} className="mt-3 rounded-lg border border-amber-200 bg-amber-50/60 p-4">
              <p className="text-sm font-bold text-slate-900">{item.name} <span className="ml-1 font-mono text-[11px] font-normal text-amber-700">{item.env}</span></p>
              <p className="mt-1 text-xs leading-5 text-slate-700">{item.detail}</p>
              <p className="mt-1 text-xs leading-5 text-slate-500">{item.alternative}</p>
            </div>
          ))}
        </section>

        <section className="mt-4 rounded-xl border border-slate-200 bg-white p-5">
          <h2 className="text-sm font-black text-slate-900">可选依赖与降级行为</h2>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[640px] border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-slate-200 text-slate-500">
                  <th className="py-2 pr-4 font-bold">能力</th>
                  <th className="py-2 pr-4 font-bold">环境变量</th>
                  <th className="py-2 font-bold">没有配置时</th>
                </tr>
              </thead>
              <tbody>
                {optional.map((item) => (
                  <tr key={item.env} className="border-b border-slate-100 align-top">
                    <td className="py-3 pr-4 font-bold text-slate-800">{item.name}</td>
                    <td className="py-3 pr-4 font-mono text-[11px] text-slate-500">{item.env}</td>
                    <td className="py-3 leading-5 text-slate-600">{item.fallback}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="mt-4 rounded-xl border border-slate-200 bg-white p-5">
          <h2 className="text-sm font-black text-slate-900">没有供应商数据时，结果长什么样</h2>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {noKeyBehaviors.map(([name, behavior]) => (
              <div key={name} className="rounded-lg bg-slate-50 p-3">
                <p className="text-xs font-bold text-slate-800">{name}</p>
                <p className="mt-1 text-[11px] leading-5 text-slate-600">{behavior}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-4 rounded-xl border border-slate-200 bg-white p-5">
          <h2 className="text-sm font-black text-slate-900">产品边界</h2>
          <ul className="mt-3 space-y-2 text-xs leading-5 text-slate-700">
            {boundaries.map((item) => <li key={item} className="flex gap-2"><span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-orange-500" />{item}</li>)}
          </ul>
        </section>

        <section className="mt-4 rounded-xl border border-slate-200 bg-slate-900 p-5 text-slate-100">
          <h2 className="text-sm font-black">零供应商密钥的演示路径</h2>
          <ol className="mt-3 space-y-2 text-xs leading-5 text-slate-300">
            <li>1. 只配置一个 <span className="font-mono text-white">LLM_API_KEY</span>（或指向本地 Ollama），启动网关与 AI 服务。</li>
            <li>2. 在大厅输入偏好，观察成员协商、maximin 选路与专家提案；最终方案会带 worker 提案与让步记录。</li>
            <li>3. 打开旅中辅助：拥挤预测会标“估算”，多语言导览会标 template_only，AR 只给锚点数据。</li>
            <li>4. 打开预订面板：未配置供应商的品类会直接说明不可用，官方渠道始终可用。</li>
          </ol>
        </section>

        <p className="mt-6 text-center text-[11px] text-slate-400">
          OmniRoute · 作品集演示模式 · 所有能力标注均来自实际返回值，不在此页面单独承诺实时性
        </p>
      </div>
    </main>
  );
}
