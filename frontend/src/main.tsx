import React, { Component, type ErrorInfo, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type BoundaryState = { error: Error | null };

function StartupFailure({ error }: { error: unknown }) {
  const detail = error instanceof Error ? (error.stack || error.message) : String(error);
  return (
    <main className="startup-error" role="alert">
      <div className="startup-error-card">
        <div className="startup-error-mark">K</div>
        <p className="startup-error-eyebrow">KARDECAGENT STUDIO</p>
        <h1>Não foi possível iniciar a interface</h1>
        <p className="startup-error-description">
          O aplicativo abriu, mas ocorreu um erro ao carregar seus componentes.
          Os detalhes abaixo ajudam a diagnosticar o problema.
        </p>
        <pre>{detail}</pre>
        <button onClick={() => window.location.reload()}>Tentar novamente</button>
      </div>
    </main>
  );
}

class StartupErrorBoundary extends Component<{ children: ReactNode }, BoundaryState> {
  state: BoundaryState = { error: null };
  static getDerivedStateFromError(error: Error): BoundaryState { return { error }; }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("KardecAgent Studio failed to render", error, info.componentStack);
  }
  render() {
    return this.state.error ? <StartupFailure error={this.state.error} /> : this.props.children;
  }
}

const root = document.getElementById("root");
if (!root) {
  document.body.textContent = "KardecAgent Studio: elemento raiz da interface não encontrado.";
  throw new Error("Root element not found");
}

const reactRoot = createRoot(root);
import("./App")
  .then(({ default: App }) => {
    reactRoot.render(
      <React.StrictMode>
        <StartupErrorBoundary><App /></StartupErrorBoundary>
      </React.StrictMode>
    );
  })
  .catch((error: unknown) => {
    console.error("KardecAgent Studio failed to load its application module", error);
    reactRoot.render(<StartupFailure error={error} />);
  });
