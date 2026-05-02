import "./globals.css";

export const metadata = {
  title: "从夯到拉锐评生成器",
  description: "从素材直接生成锐评短视频",
};

export default function RootLayout({ children }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
