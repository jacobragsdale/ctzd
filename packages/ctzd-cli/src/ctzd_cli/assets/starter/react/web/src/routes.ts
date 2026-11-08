/* The app's routes, inside the layout: one page at `/`. */

import { createRootRoute, createRoute } from "@tanstack/react-router";

import { HomePage } from "./pages/home.tsx";
import { Layout } from "./pages/layout.tsx";

const rootRoute = createRootRoute({ component: Layout });

const homeRoute = createRoute({
  component: HomePage,
  getParentRoute: () => rootRoute,
  path: "/",
});

const routeTree = rootRoute.addChildren([homeRoute]);

export { routeTree };
