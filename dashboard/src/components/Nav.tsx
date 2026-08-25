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
    <nav className="border-b border-border bg-card">
      <div className="max-w-7xl mx-auto px-4 flex items-center gap-8 h-14">
        <span className="font-bold text-accent text-lg tracking-tight">SportPredict</span>
        {links.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className="text-sm text-muted hover:text-white transition-colors"
          >
            {l.label}
          </Link>
        ))}
      </div>
    </nav>
  );
}
