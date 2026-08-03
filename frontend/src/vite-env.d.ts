/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Базовый путь API. По умолчанию /api/v1 — запросы идут через nginx. */
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
