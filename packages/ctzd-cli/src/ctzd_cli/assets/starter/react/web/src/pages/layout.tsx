/* The layout every page sits in: the app's name, and the page, with parallax's engine started for its grids and
 * charts. */

import { ParallaxProvider } from "@basis/parallax";
import { Outlet } from "@tanstack/react-router";
import type { ReactElement } from "react";

import { startParallax } from "#parallax.ts";

function Layout(): ReactElement {
  return (
    <ParallaxProvider worker={startParallax} prewarm>
      <div className="flex min-h-svh flex-col">
        <header className="border-b px-6 py-3">
          <span className="text-sm font-semibold">{{title}}</span>
        </header>
        <main className="flex w-full flex-1 flex-col gap-6 p-6">
          <Outlet />
        </main>
      </div>
    </ParallaxProvider>
  );
}

export { Layout };
