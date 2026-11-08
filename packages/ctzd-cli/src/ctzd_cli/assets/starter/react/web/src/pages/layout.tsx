/* The layout every page sits in: the app's name and a link to each page, then the page, with parallax's engine
 * started for its grids and charts. */

import { ParallaxProvider } from "@basis/parallax";
import { Link, Outlet } from "@tanstack/react-router";
import type { ReactElement } from "react";

import { pages } from "#nav.ts";

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
      </div>
    </ParallaxProvider>
  );
}

export { Layout };
