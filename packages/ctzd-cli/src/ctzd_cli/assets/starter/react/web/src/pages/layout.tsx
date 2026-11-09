/* The layout every page sits in: the app's name and a link to each page, then the page, with parallax's engine
 * started for its grids and charts, and a footer that says who built the app and that its figures are best effort,
 * since the people who open a dashboard are not only the person who built it. */

import { ParallaxProvider } from "@basis/parallax";
import { Link, Outlet } from "@tanstack/react-router";
import type { ReactElement } from "react";

import { pages } from "#nav.ts";

/** Who built the app and how far to trust its figures, for everyone who opens it; from app.toml's owners. */
const footnote =
  "Built by {{owners}} with ctzd, by someone who does not code. The figures are best effort: check them against their source before you rely on them.";

function Layout(): ReactElement {
  return (
    <ParallaxProvider prewarm>
      <div className="flex min-h-svh flex-col">
        <header className="flex items-center gap-6 border-b px-6 py-3">
          <span className="text-sm font-semibold">{{title}}</span>
          <nav aria-label="Pages" className="flex items-center gap-4">
            {pages.map(({ path, title }) => (
              <Link key={path} to={path} className="text-sm text-muted-foreground">
                {title}
              </Link>
            ))}
          </nav>
        </header>
        <main className="flex w-full flex-1 flex-col gap-6 p-6">
          <Outlet />
        </main>
        <footer className="border-t px-6 py-3 text-xs text-muted-foreground">{footnote}</footer>
      </div>
    </ParallaxProvider>
  );
}

export { Layout };
