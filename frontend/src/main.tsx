import React, { Component, type ErrorInfo, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

type BoundaryState = { error: Error | null };

class StartupErrorBoundary extends Component<{ children: ReactNode }, BoundaryState> {
  state: BoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): BoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("KardecAgent Studio failed to render", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
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
            <pre>{this.state.error.stack || this.state.error.message}</pre>
            <button onClick={() => window.location.reload()}>Tentar novamente</button>
          </div>
        </main>
      );
    }
    return this.props.children;
  }
}

const root = document.getElementById("root");
if (!root) {
  document.body.innerHTML = "<main style='padding:32px;color:#fff;font:14px Segoe UI'>KardecAgent Studio: elemento raiz da interface não encontrado.</main>";
  throw new Error("Root element not found");
}

createRoot(root).render(
  <React.StrictMode>
    <StartupErrorBoundary>
      <App />
    </StartupErrorBoundary>
  </React.StrictMode>
);
