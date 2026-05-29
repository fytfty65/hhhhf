
import './globals.css' // 引入全局样式（如 Tailwind）

export const metadata = {
  title: 'OmniRoute Dashboard',
  description: '多智能体旅游路线协商中枢',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  )
}