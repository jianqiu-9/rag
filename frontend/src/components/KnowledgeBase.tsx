import { useEffect, useRef, useState } from 'react';
import {
  ArrowUpRight,
  Check,
  FileText,
  LoaderCircle,
  RefreshCw,
  Trash2,
  UploadCloud,
} from 'lucide-react';
import { api, errorText } from '../api';
import type { DocumentRecord } from '../types';

const statusText = {
  pending: '待处理',
  processing: '处理中',
  completed: '已入库',
  failed: '处理失败',
};

/**
 * 文档列表展示“最新一次处理尝试”，不把替换失败误解成整份资料已丢失。
 * 旧版是否还能检索由后端 has_active_version 决定，前端不自行推测版本状态。
 */
export default function KnowledgeBase({ onAsk }: { onAsk: () => void }) {
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [error, setError] = useState('');
  const [messages, setMessages] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [revision, setRevision] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    // 请求完成后再安排下一次轮询，避免慢请求积压；组件卸载后不再写入状态。
    async function refresh() {
      try {
        const result = await api.documents();
        if (cancelled) return;
        setDocuments(result.documents);
        setError('');
        if (result.documents.some((d) => ['pending', 'processing'].includes(d.status)))
          timer = setTimeout(refresh, 2000);
      } catch (err) {
        if (!cancelled) setError(errorText(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void refresh();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [revision]);

  async function upload(files: File[]) {
    if (!files.length || busy) return;
    if (files.length > 20) {
      setError('一次最多上传 20 个文件');
      return;
    }
    setBusy(true);
    setError('');
    setMessages([]);
    try {
      const result = await api.upload(files);
      setMessages(
        result.map((r) =>
          r.document
            ? `${r.document.filename}：${r.duplicate ? '文件已存在，无需重复导入' : '已加入处理队列'}`
            : `${r.filename}：${r.message}`,
        ),
      );
      setRevision((value) => value + 1);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = '';
    }
  }

  async function operate(id: string, action: 'remove' | 'retry') {
    setBusy(true);
    setError('');
    try {
      await api[action](id);
      setRevision((v) => v + 1);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const active = documents.filter((d) => d.is_active || d.has_active_version).length;
  const processing = documents.filter((d) => ['pending', 'processing'].includes(d.status)).length;

  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">YOUR KNOWLEDGE, CONNECTED</p>
          <h1>
            把零散面经，变成你的知识库<span>。</span>
          </h1>
          <p>导入本地资料，用自己的知识准备下一场面试。</p>
        </div>
      </div>
      <div className="stat-grid">
        <div className="stat">
          <span>知识库文档</span>
          <strong>{String(documents.length).padStart(2, '0')}</strong>
          <FileText size={20} />
        </div>
        <div className="stat">
          <span>可检索资料</span>
          <strong>{String(active).padStart(2, '0')}</strong>
          <Check size={20} />
        </div>
        <div className="stat">
          <span>正在处理</span>
          <strong>{String(processing).padStart(2, '0')}</strong>
          <RefreshCw size={20} className={processing ? 'spin' : ''} />
        </div>
      </div>
      {error && (
        <div role="alert" className="alert error">
          {error}
          <button className="text-button" onClick={() => setRevision((v) => v + 1)}>
            重新加载
          </button>
        </div>
      )}
      <section
        className={`upload-zone ${dragging ? 'dragging' : ''}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void upload(Array.from(e.dataTransfer.files));
        }}
      >
        <div className="upload-icon">
          <UploadCloud size={25} />
        </div>
        <h2>让知识从这里开始</h2>
        <p>拖拽文件到这里，或选择你想导入的面经资料</p>
        <input
          ref={fileInput}
          type="file"
          multiple
          accept=".md,.txt"
          className="visually-hidden"
          aria-label="选择面经文件"
          onChange={(e) => void upload(Array.from(e.target.files || []))}
        />
        <button className="primary" disabled={busy} onClick={() => fileInput.current?.click()}>
          {busy ? <LoaderCircle size={16} className="spin" /> : <UploadCloud size={16} />}选择文件
        </button>
        <small>支持 Markdown / TXT · 每个文件最多 10 MB · 本地保存</small>
      </section>
      {messages.length > 0 && (
        <div className="alert" role="status">
          {messages.map((message, i) => (
            <div key={i}>{message}</div>
          ))}
        </div>
      )}
      <section className="document-section">
        <div className="section-heading">
          <h2>
            全部资料 <span>{documents.length}</span>
          </h2>
          <button className="text-button" onClick={() => setRevision((v) => v + 1)}>
            <RefreshCw size={14} />
            刷新
          </button>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>文件名称</th>
                <th>导入时间</th>
                <th>状态</th>
                <th className="align-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {documents.map((doc) => (
                <tr key={doc.id}>
                  <td>
                    <div className="file-name">
                      <span className="file-icon">
                        <FileText size={18} />
                      </span>
                      <div>
                        <strong>{doc.filename}</strong>
                        <small title={doc.sha256}>SHA256 · {doc.sha256.slice(0, 12)}</small>
                        {doc.error_message && (
                          <small className="danger">
                            {doc.error_message}
                            {doc.has_active_version && ' · 旧版本仍可检索'}
                          </small>
                        )}
                      </div>
                    </div>
                  </td>
                  <td className="muted">
                    {new Date(doc.created_at).toLocaleString('zh-CN', {
                      month: '2-digit',
                      day: '2-digit',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </td>
                  <td>
                    <span className={`status ${doc.status}`}>
                      {['pending', 'processing'].includes(doc.status) ? (
                        <LoaderCircle size={12} className="spin" />
                      ) : (
                        <span className="status-dot" />
                      )}
                      {statusText[doc.status]}
                    </span>
                  </td>
                  <td>
                    <div className="row-actions">
                      {doc.status === 'failed' && (
                        <button
                          className="text-button"
                          disabled={busy}
                          onClick={() => void operate(doc.id, 'retry')}
                        >
                          重试
                        </button>
                      )}
                      <button
                        className="icon-button delete"
                        aria-label={`删除 ${doc.filename}`}
                        disabled={busy}
                        onClick={() => void operate(doc.id, 'remove')}
                      >
                        <Trash2 size={16} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!documents.length && (
            <div className="empty-state">
              <FileText size={29} />
              <p>{loading ? '正在读取本地资料…' : '还没有资料，导入第一份面经吧'}</p>
              <small>你的每一次积累，都能成为下一次回答的依据。</small>
            </div>
          )}
        </div>
      </section>
      <div className="bottom-callout">
        <div>
          <span className="tiny-label">下一步</span>
          <h3>知识有了，开始提问。</h3>
          <p>按技术主题找题，或深入理解一道面试题。</p>
        </div>
        <button className="secondary" onClick={onAsk}>
          前往问答 <ArrowUpRight size={16} />
        </button>
      </div>
    </>
  );
}
