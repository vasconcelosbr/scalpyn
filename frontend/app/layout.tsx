import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { AuthGuard } from "@/components/auth/AuthGuard";

export const metadata: Metadata = {
  title: "Scalpyn - Institutional Crypto Quant",
  description: "Institutional-grade SaaS quantitative crypto trading platform",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <AuthGuard>
          <div className="flex h-screen w-full relative">
            <Sidebar />
            <div className="flex flex-col flex-1 h-full ml-[240px] max-lg:ml-[64px] max-md:ml-0 transition-all duration-350 ease-out overflow-hidden page-content">
              <Header />
              <main className="flex-1 overflow-y-auto p-6 max-w-[1440px] mx-auto w-full pt-[80px]">
                <div className="page-content">
                  {children}
                </div>
              </main>
            </div>
          </div>
        </AuthGuard>
      </body>
    </html>
  );
}
