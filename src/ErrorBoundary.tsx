import React from "react";

interface ErrorBoundaryState {
  error: string | null;
}

export class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: unknown): ErrorBoundaryState {
    return { error: error instanceof Error ? error.stack || error.message : String(error) };
  }

  componentDidCatch(error: unknown) {
    console.error(error);
  }

  render() {
    if (this.state.error) {
      return (
        <main className="bootError">
          <h1>前端启动失败</h1>
          <pre>{this.state.error}</pre>
        </main>
      );
    }
    return this.props.children;
  }
}
