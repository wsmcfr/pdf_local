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
  embedding_model: string;
  llm_model: string;
  context_chunks: number;
  offline_mode: boolean;
};

export type Health = {
  ok: boolean;
  ollama: boolean;
  version: string;
};
