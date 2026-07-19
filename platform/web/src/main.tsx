import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";

const rootElement = document.getElementById("root");
if (rootElement === null) {
  throw new Error("缺少课程工作台根节点。");
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
