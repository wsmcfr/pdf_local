export type DocumentRow = {
  id: string;
  root_path: string;
  path: string;
  file_name: string;
  mime: string;
  size_bytes: number;
  page_count: number;
  status: string;
  ocr_confidence: number | null;
  error: string | null;
  indexed_at: string | null;
};

export type JobRow = {
  id: string;
  type: string;
  status: string;
  progress: number;
  total: number;
  processed: number;
  current_file: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type PageContent = {
  document_id: string;
  page_no: number;
  text: string;
  is_scanned: number;
  ocr_confidence: number | null;
  low_confidence: number;
};

export type Citation = {
  file_name: string;
  page_no: number;
  kind: "pdf" | "markdown";
};

export type SourceChunk = {
  chunk_id: string;
  document_id: string;
  page_no: number;
  text: string;
  file_name: string;
  mime: string;
  score?: number;
};

export type Settings = {
  data_dir: string;
  ocr_dpi: number;
  context_chunks: number;
  offline_mode: boolean;
  embedding_mode?: string;
  embedding_provider_id?: string;
  embedding_model?: string;
  llm_provider_id?: string;
};

export type Health = {
  ok: boolean;
  version: string;
};

export type ProviderKind =
  | "openai_chat"
  | "openai_responses"
  | "anthropic"
  | "gemini"
  | "custom";

export type ProviderModel = {
  id: string;
  name: string;
};

export type Provider = {
  id: string;
  name: string;
  kind: ProviderKind;
  base_url: string;
  model: string;
  enabled: boolean;
  has_key: boolean;
  extra: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type Preset = {
  id: string;
  name: string;
  kind: ProviderKind;
  base_url: string;
  default_model: string;
};

export type EmbeddingMode = "local" | "external" | "auto" | "fts";

export type EmbeddingConfig = {
  mode: EmbeddingMode;
  provider_id: string;
  model: string;
  local_model: string;
  ok: boolean;
  status: string;
  providers: { id: string; name: string }[];
};

export type GatewayConfig = {
  enabled: boolean;
  rag_mode: "auto" | "off" | "tools";
  provider_id: string;
  mcp_enabled: boolean;
  token: string;
};

export type OcrStatus = {
  ready: boolean;
  engine?: string;
  models?: string[];
  error?: string | null;
};

export type EmbeddingStatus = {
  ready: boolean;
  model: string;
  path: string;
  source: string;
};

export type ProviderStatusItem = {
  id: string;
  name: string;
  kind: ProviderKind;
  model: string;
  has_key: boolean;
};

export type ProvidersStatus = {
  configured: boolean;
  enabled: boolean;
  items: ProviderStatusItem[];
};

export type ModelsStatus = {
  ocr: OcrStatus;
  embedding: EmbeddingStatus;
  providers: ProvidersStatus;
};
