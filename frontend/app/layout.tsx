import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { AuthGuard } from "@/components/auth/AuthGuard";
import { MobileTabBar } from "@/components/layout/MobileTabBar";
import { ServiceWorkerRegistrar } from "@/components/ServiceWorkerRegistrar";

export const metadata: Metadata = {
  title: "Scalpyn — Institutional Crypto Quant",
  description: "Institutional-grade quantitative crypto trading platform with AI-driven signals.",
  manifest: "/manifest.json",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "Scalpyn",
  },
  icons: {
    icon: "/icon-192.svg",
    apple: "/icon-192.svg",
  },
};

export const viewport: Viewport = {
  themeColor: "#06070A",
  width: "device-width",
  initialScale: 1,
  minimumScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <ServiceWorkerRegistrar />
        <AuthGuard>
          <div className="flex h-screen w-full relative">
            {/* Desktop sidebar — hidden on mobile */}
            <div className="max-md:hidden">
              <Sidebar />
            </div>

            <div className="flex flex-col flex-1 h-full ml-[240px] max-lg:ml-[64px] max-md:ml-0 transition-all duration-350 ease-out overflow-hidden">
              <Header />
              <main className="flex-1 overflow-y-auto p-6 max-w-[1440px] mx-auto w-full pt-[80px] custom-scrollbar">
                <div className="page-content">
                  {children}
                </div>
              </main>
            </div>
          </div>

          {/* Mobile bottom tab bar — visible only on < 768px */}
          <MobileTabBar />
        </AuthGuard>
      </body>
    </html>
  );
}
