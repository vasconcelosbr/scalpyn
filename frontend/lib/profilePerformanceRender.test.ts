import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  EmptyProfilesState,
  ErrorState,
  LoadingDashboard,
} from "@/components/shadow-portfolio/ProfilePerformanceDashboard";


test("profile dashboard exposes stable loading, error and empty states", () => {
  const loading = renderToStaticMarkup(createElement(LoadingDashboard));
  assert.match(loading, /Carregando performance dos profiles/);

  const error = renderToStaticMarkup(createElement(ErrorState, { message: "API indisponível", onRetry: () => undefined }));
  assert.match(error, /Não foi possível carregar a performance dos profiles/);
  assert.match(error, /API indisponível/);

  const empty = renderToStaticMarkup(createElement(EmptyProfilesState));
  assert.match(empty, /Nenhum profile L3 disponível/);
});
