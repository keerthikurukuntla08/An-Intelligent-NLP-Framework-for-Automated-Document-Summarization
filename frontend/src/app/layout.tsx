import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Forma | Adaptive Academic Learning",
  description: "Source-grounded study and adaptive revision for your course material.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
