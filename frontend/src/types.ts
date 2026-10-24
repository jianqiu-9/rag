/** 与后端契约保持一致：处理成功与是否有效是两个独立维度。 */
export interface DocumentRecord {
  id: string;
  logical_document_id: string;
  filename: string;
  sha256: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  is_active: boolean;
  has_active_version?: boolean;
  error_message: string | null;
  error_code: string | null;
  created_at: string;
  attempt_count: number;
}

export interface Source {
  chunk_id: string;
  document_id: string;
  filename: string;
  section: string;
  line_start: number;
  line_end: number;
  excerpt: string;
}

export interface Question {
  question: string;
  answer: string | null;
  note: string | null;
  source_chunk_ids: string[];
}

export interface QueryResult {
  request_id: string;
  status: 'answered' | 'insufficient';
  answer: string;
  questions: Question[];
  sources: Source[];
  requested_count: number | null;
  actual_count: number;
  notice: string | null;
}

export interface UploadResult {
  document?: DocumentRecord;
  duplicate?: boolean;
  filename?: string;
  message?: string;
  error_code?: string;
  status_code: number;
}
