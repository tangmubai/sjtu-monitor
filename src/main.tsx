import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { ErrorBoundary } from "./ErrorBoundary";
import "./styles.css";

window.addEventListener("error", (event) => {
  const root = document.getElementById("root");
  if (root && !root.textContent?.includes("前端启动失败")) {
    root.innerHTML = `<main class="bootError"><h1>前端启动失败</h1><pre>${event.message}</pre></main>`;
  }
});

window.addEventListener("unhandledrejection", (event) => {
  const root = document.getElementById("root");
  if (root && !root.textContent?.includes("前端启动失败")) {
    root.innerHTML = `<main class="bootError"><h1>前端启动失败</h1><pre>${String(event.reason)}</pre></main>`;
  }
});

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
);
