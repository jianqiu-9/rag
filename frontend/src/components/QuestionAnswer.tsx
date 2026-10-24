import { useState } from 'react';
import {
  ArrowUpRight,
  BookOpen,
  CornerDownLeft,
  FileText,
  LoaderCircle,
  Search,
  Sparkles,
} from 'lucide-react';
import { api, errorText } from '../api';
import type { QueryResult, Source } from '../types';
import SourcePanel from './SourcePanel';

const examples = [
  'LangGraph 常见面试问题有哪些？列出 5 道。',
  'MCP 与 Tool Calling 有什么区别？',
  '如何设计 RAG 的检索流程？',
];

/**
 * 一次提问对应一次完整响应，不隐式携带历史问题，避免超出 MVP 的单轮语义。
 * 来源按钮仅使用服务端返回的真实 ID；模型生成文字按纯文本渲染，禁止插入 HTML。
 */
export default function QuestionAnswer() {
  const [query, setQuery] = useState('');
  const [limit, setLimit] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<QueryResult | null>(null);
  const [source, setSource] = useState<Source | null>(null);

  async function submit() {
    if (!query.trim() || loading) return;
    setLoading(true);
    setError('');
    setResult(null);
    setSource(null);
    try {
      setResult(await api.query(query.trim(), limit ? Number(limit) : undefined));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }

  function sourceButtons(ids: string[]) {
    return ids.map((id) => {
      const item = result?.sources.find((s) => s.chunk_id === id);
      return (
        item && (
          <button
            key={id}
            className={`source-chip ${source?.chunk_id === id ? 'selected' : ''}`}
            onClick={() => setSource(item)}
          >
            <FileText size={13} />
            {item.filename} · L{item.line_start}–{item.line_end}
            <ArrowUpRight size={12} />
          </button>
        )
      );
    });
  }

  return (
    <>
      <div className="page-heading">
        <p className="eyebrow">ASK WITH EVIDENCE</p>
        <h1>
          每个好回答，都有据可循<span>。</span>
        </h1>
        <p>从你导入的资料出发，找到面试题、参考答案和原始出处。</p>
      </div>
      <div className={`qa-layout ${source ? 'with-source' : ''}`}>
        <div className="qa-main">
          <form
            className="question-box"
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
          >
            <label htmlFor="question">
              <Search size={17} /> 你想了解什么？
            </label>
            <textarea
              id="question"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              maxLength={4000}
              placeholder="例如：RAG 中如何提升召回质量？"
              rows={4}
              disabled={loading}
            />
            <div className="question-toolbar">
              <label className="count-label">
                列题数量（可选）
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={limit}
                  onChange={(e) => setLimit(e.target.value)}
                  placeholder="自动"
                  disabled={loading}
                />
              </label>
              <button type="submit" className="primary" disabled={loading || !query.trim()}>
                {loading ? (
                  <LoaderCircle size={16} className="spin" />
                ) : (
                  <CornerDownLeft size={16} />
                )}
                {loading ? '正在查找依据…' : '查询知识库'}
              </button>
            </div>
          </form>
          <p className="data-notice">
            <span className="status-dot" />
            向量化在本机完成；问题和召回片段将发送至 DeepSeek 生成回答。
            问答和模型调用记录在本机保存，默认保留 30 天。
          </p>
          {error && (
            <div className="alert error" role="alert">
              {error}
            </div>
          )}
          {!result && !loading && !error && (
            <div className="question-empty">
              <div className="upload-icon">
                <BookOpen size={25} />
              </div>
              <h2>带着问题，走近你的知识</h2>
              <p>试试按主题查题，或换一种问法理解已有资料。</p>
              <div className="example-list">
                {examples.map((text) => (
                  <button key={text} onClick={() => setQuery(text)}>
                    {text}
                    <ArrowUpRight size={15} />
                  </button>
                ))}
              </div>
            </div>
          )}
          {loading && (
            <div className="answer-card loading-answer" role="status">
              <Sparkles size={20} />
              <h2>正在检索你的资料</h2>
              <p>找到证据后，将为你整理有来源的回答。</p>
            </div>
          )}
          {result && (
            <section
              className={`answer-card ${result.status === 'insufficient' ? 'insufficient' : ''}`}
            >
              <div className="answer-label">
                <Sparkles size={16} />
                {result.status === 'answered' ? '根据本地资料整理' : '资料不足'}
              </div>
              <p className="answer-text">{result.answer}</p>
              {result.questions.map((question, i) => (
                <article className="question-result" key={`${i}-${question.question}`}>
                  <h3>
                    <span>{String(i + 1).padStart(2, '0')}</span>
                    {question.question}
                  </h3>
                  <p className="answer-text">{question.answer || '原文未提供答案'}</p>
                  {question.note && question.answer && <p>{question.note}</p>}
                  <div className="source-chips">{sourceButtons(question.source_chunk_ids)}</div>
                </article>
              ))}
              {!result.questions.length && (
                <div className="source-chips">
                  {sourceButtons(result.sources.map((s) => s.chunk_id))}
                </div>
              )}
              {result.notice && <p className="result-notice">{result.notice}</p>}
              <div className="answer-footer">
                {result.sources.length} 个引用片段 <span>请求 ID · {result.request_id}</span>
              </div>
            </section>
          )}
        </div>
        {source && <SourcePanel source={source} onClose={() => setSource(null)} />}
      </div>
    </>
  );
}
