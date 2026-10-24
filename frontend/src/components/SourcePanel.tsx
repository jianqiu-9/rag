import { useEffect, useState } from 'react';
import { FileText, LoaderCircle, X } from 'lucide-react';
import { api, errorText } from '../api';
import type { Source } from '../types';

/**
 * 再读原文件时后端会重新确认版本有效性，不把先前 excerpt 当成永久可用的来源。
 * 切换文件或卸载面板时取消旧请求，防止较慢的响应覆盖用户刚选中的来源。
 */
export default function SourcePanel({ source, onClose }: { source: Source; onClose: () => void }) {
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setContent(null);
    setError('');
    api
      .source(source.document_id, controller.signal)
      .then((result) => setContent(result.content))
      .catch((err) => {
        if (!controller.signal.aborted) setError(errorText(err));
      });
    return () => controller.abort();
  }, [source.document_id]);

  return (
    <aside className="source-panel" aria-label="来源原文">
      <div className="section-heading">
        <h2>
          <FileText size={17} /> 来源原文
        </h2>
        <button className="icon-button" onClick={onClose} aria-label="关闭来源">
          <X size={18} />
        </button>
      </div>
      <strong>{source.filename}</strong>
      <p className="muted">
        {source.section || '正文'} · 第 {source.line_start}–{source.line_end} 行
      </p>
      {error && (
        <div role="alert" className="alert error">
          {error}
        </div>
      )}
      {content === null && !error && <LoaderCircle className="spin" size={20} />}
      {content !== null && (
        <div className="source-lines">
          {content.split(/\r?\n/).map((line, index) => (
            <div
              key={index}
              className={
                index + 1 >= source.line_start && index + 1 <= source.line_end ? 'highlighted' : ''
              }
            >
              <span className="line-number">{index + 1}</span>
              <span>{line || ' '}</span>
            </div>
          ))}
        </div>
      )}
    </aside>
  );
}
