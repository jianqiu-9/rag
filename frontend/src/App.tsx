import { useState } from 'react';
import { ArrowUpRight, BookOpen, Layers, MessageSquare, ShieldCheck } from 'lucide-react';
import KnowledgeBase from './components/KnowledgeBase';
import QuestionAnswer from './components/QuestionAnswer';

export default function App() {
  const [page, setPage] = useState<'knowledge' | 'ask'>('knowledge');
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setPage('knowledge');
          }}
        >
          <span className="brand-icon">
            <Layers size={24} />
          </span>
          <div>
            Agent Notes<small>面经知识库</small>
          </div>
        </a>
        <div className="workspace-label">
          个人工作空间 <span>LOCAL</span>
        </div>
        <nav aria-label="主导航">
          <button
            className={page === 'knowledge' ? 'active' : ''}
            onClick={() => setPage('knowledge')}
          >
            <BookOpen size={18} />
            知识库<span>01</span>
          </button>
          <button className={page === 'ask' ? 'active' : ''} onClick={() => setPage('ask')}>
            <MessageSquare size={18} />
            检索问答<span>02</span>
          </button>
        </nav>
        <div className="sidebar-note">
          <ShieldCheck size={21} />
          <h3>只回答，有依据的问题。</h3>
          <p>资料来自你的本地知识库。找不到答案时，我们会如实告知。</p>
          <span>
            为下一场面试，多一份准备 <ArrowUpRight size={12} />
          </span>
        </div>
        <div className="sidebar-footer">
          <span className="avatar">我</span>
          <div>
            我的知识空间<small>本地 · 单用户</small>
          </div>
          <span className="online-dot" />
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div>
            工作空间 <span>/</span> <strong>{page === 'knowledge' ? '知识库' : '检索问答'}</strong>
          </div>
          <span className="local-badge">
            <span className="status-dot" />
            本地资料库
          </span>
        </header>
        <main>
          {page === 'knowledge' ? (
            <KnowledgeBase onAsk={() => setPage('ask')} />
          ) : (
            <QuestionAnswer />
          )}
        </main>
        <footer className="page-footer">
          少一点翻找，多一点思考。<span>AGENT NOTES / MVP</span>
        </footer>
      </div>
    </div>
  );
}
