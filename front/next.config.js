/** @type {import('next').NextConfig} */
const nextConfig = {
  // 添加 rewrites 配置
  async rewrites() {
    return [
      {
        source: '/amap-traffic',
        destination: 'https://tm.amap.com/trafficengine/mapabc/traffictile',
      },
    ];
  },
};

module.exports = nextConfig;