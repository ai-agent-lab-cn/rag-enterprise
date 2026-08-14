/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DEPLOYMENT_MODE?: "demo" | "local";
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
