import type { DocumentRecord, QueryResult, UploadResult } from './types';

/** 错误只展示后端安全消息及请求 ID；不直接渲染上游服务的原始响应。 */
export class ApiError extends Error {
  constructor(
    message: string,
    public requestId?: string,
    public retryable = false,
  ) {
    super(message);
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, options);
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError('无法连接本地服务，请确认后端已启动', undefined, true);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(body.message || '请求失败，请稍后重试', body.request_id, body.retryable);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  documents: () => request<{ documents: DocumentRecord[] }>('/documents'),
  remove: (id: string) => request<void>(`/documents/${id}`, { method: 'DELETE' }),
  retry: (id: string) => request<DocumentRecord>(`/documents/${id}/retry`, { method: 'POST' }),
  source: (id: string, signal?: AbortSignal) =>
    request<{ filename: string; content: string }>(`/documents/${id}/source`, { signal }),
  query: (query: string, limit?: number) =>
    request<QueryResult>('/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, ...(limit ? { limit } : {}) }),
    }),
  async upload(files: File[]): Promise<UploadResult[]> {
    const body = new FormData();
    files.forEach((file) => body.append('files', file));
    // 单文件校验失败也是逐文件结果；保留该契约，批量上传才能展示部分成功。
    const response = await fetch('/api/documents', { method: 'POST', body }).catch(() => {
      throw new ApiError('无法连接本地服务，请确认后端已启动', undefined, true);
    });
    const result = await response.json();
    if (Array.isArray(result.results)) return result.results;
    throw new ApiError(result.message || '文件上传失败', result.request_id, result.retryable);
  },
};

export function errorText(error: unknown): string {
  if (error instanceof ApiError)
    return error.message + (error.requestId ? `（请求 ID：${error.requestId}）` : '');
  return error instanceof Error ? error.message : '操作失败，请稍后重试';
}
