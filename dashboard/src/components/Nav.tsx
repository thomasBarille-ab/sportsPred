import Link from "next/link";

const links = [
  { href: "/",        label: "Vue d'ensemble" },
  { href: "/ligue1",  label: "Ligue 1" },
  { href: "/nba",     label: "NBA" },
  { href: "/models",  label: "Modèles" },
  { href: "/logs",    label: "Logs" },
];

export default function Nav() {
  return (
    <nav className="fixed top-0 left-0 h-full w-52 border-r border-border bg-card flex flex-col gap-1 px-3 py-6 z-10">
      <span className="font-bold text-accent text-lg tracking-tight px-3 mb-6">SportPredict</span>
      {links.map((l) => (
        <Link
          key={l.href}
          href={l.href}
          className="text-sm text-muted hover:text-white hover:bg-border/40 transition-colors rounded-lg px-3 py-2"
        >
          {l.label}
        </Link>
      ))}
    </nav>
  );
}
