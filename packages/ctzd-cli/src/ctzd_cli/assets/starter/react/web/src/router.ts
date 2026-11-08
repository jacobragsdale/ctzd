/* The app's router. Registering its type gives every `Link` and `useNavigate` the app's routes. */

import { createRouter } from "@tanstack/react-router";

import { routeTree } from "./routes.ts";

const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

export { router };
