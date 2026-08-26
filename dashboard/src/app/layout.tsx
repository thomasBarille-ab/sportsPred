import type { Metadata } from "next";
import "./globals.css";
import Nav from "@/components/Nav";

export const metadata: Metadata = {
  title: "Sports Predictor",
  description: "Dashboard de prédiction Ligue 1 & NBA",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr">
      <body className="bg-surface text-slate-100 min-h-screen flex">
        <Nav />
        <main className="ml-52 flex-1 px-8 py-8">{children}</main>
      </body>
    </html>
  );
}
