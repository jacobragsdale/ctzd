/* Platform-owned: ctzd sync rewrites this file. Starts parallax's engine in its worker, which Vite bundles with the
 * engine beside it.
 *
 * SHIM (MVP): until @basis/parallax gives apps a start function of its own, this file holds Vite's `?worker` import,
 * which the resolver cannot see a default export in. */

// oxlint-disable-next-line import/default -- `?worker` is Vite's worker import, whose default export, the worker's constructor, Vite makes; the module the resolver finds has none.
import ParallaxWorker from "@basis/parallax/worker?worker";

/** A new worker for `ParallaxProvider`'s `worker` prop. */
const startParallax = (): Worker => new ParallaxWorker();

export { startParallax };
