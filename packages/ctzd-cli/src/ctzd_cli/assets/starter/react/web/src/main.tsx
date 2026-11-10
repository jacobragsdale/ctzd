/* The app's entry point: its routes in the firm's app shell. The pages need no sign-in. */

import "./styles.css";

import { anonymousSignIn, BasisApp } from "@basis/ui";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { router } from "./router.ts";

/** Every ctzd app is served under this domain; there, the person's theme, mode and density follow them from app to
 * app. Anywhere else, such as `ctzd dev` on localhost, they stay with this app. */
const appsDomain = ".{{apps_domain}}";
const preferencesDomain = globalThis.location.hostname.endsWith(appsDomain)
  ? appsDomain
  : undefined;

const root = document.querySelector("#root");
if (root === null) {
  throw new Error("index.html has no #root element for the app to render into.");
}
createRoot(root).render(
  <StrictMode>
    <BasisApp router={router} signIn={anonymousSignIn()} preferencesDomain={preferencesDomain} />
  </StrictMode>,
);
