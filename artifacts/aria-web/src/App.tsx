import { useState, useRef, useEffect } from "react";
import { Router, Switch, Route, Link, useLocation } from "wouter";

// ── Types ──────────────────────────────────────────────────────────────────

type Gear = "WALK" | "SPRINT" | "LAUNCH";

interface Message {
  id: string;
  role: "user" | "aria";
  content: string;
  gear?: Gear;
  ts: number;
}

// ── Constants ──────────────────────────────────────────────────────────────

const GEAR_STYLE: Record<Gear, { color: string; bg: string; border: string; label: string }> = {
  WALK:   { color: "#a78bfa", bg: "#a78bfa18", border: "#a78bfa30", label: "WALK" },
  SPRINT: { color: "#60a5fa", bg: "#60a5fa18", border: "#60a5fa30", label: "SPRINT" },
  LAUNCH: { color: "#c084fc", bg: "#c084fc18", border: "#7c3aed50", label: "LAUNCH" },
};

const SESSION_KEY = "aria_session_id";

function getSessionId(): string {
  let id = localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = `web_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    localStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

// ── Chat page ──────────────────────────────────────────────────────────────

function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "welcome",
      role: "aria",
      content: "Hello. I'm ARIA — your multi-agent AI assistant. Ask me anything. I'll route your query to the right gear automatically, or you can prefix with /walk, /sprint, or /launch to choose.",
      gear: "WALK",
      ts: Date.now(),
    },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [dots, setDots] = useState(".");
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const sessionId = useRef(getSessionId());

  // Animate dots while loading
  useEffect(() => {
    if (!loading) return;
    const t = setInterval(() => setDots(d => d.length >= 3 ? "." : d + "."), 500);
    return () => clearInterval(t);
  }, [loading]);

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function send() {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");

    const userMsg: Message = { id: Date.now().toString(), role: "user", content: text, ts: Date.now() };
    setMessages(m => [...m, userMsg]);
    setLoading(true);
    setDots(".");

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, session_id: sessionId.current }),
      });
      const data = await res.json();
      const ariaMsg: Message = {
        id: (Date.now() + 1).toString(),
        role: "aria",
        content: data.reply ?? data.error ?? "No response.",
        gear: (data.gear as Gear) ?? "WALK",
        ts: Date.now(),
      };
      setMessages(m => [...m, ariaMsg]);
    } catch {
      setMessages(m => [...m, {
        id: (Date.now() + 1).toString(),
        role: "aria",
        content: "⚠️ Could not reach ARIA. Please try again.",
        gear: "WALK",
        ts: Date.now(),
      }]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="flex flex-col h-screen bg-[#0f0f1a] text-white">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-[#1e1e3a]">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-[#444] hover:text-[#a78bfa] text-sm transition-colors">← About</Link>
          <span className="text-[#2a2a4a]">|</span>
          <span className="font-bold tracking-widest text-[#a78bfa]">ARIA</span>
        </div>
        <div className="flex items-center gap-2 text-[#00e676] text-xs font-semibold">
          <span className="w-2 h-2 rounded-full bg-[#00e676] animate-pulse" />
          LIVE
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-6 space-y-4">
        {messages.map(msg => (
          <div key={msg.id} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            {msg.role === "aria" ? (
              <div className="max-w-[80%]">
                <div className="bg-[#1a1a2e] border border-[#2a2a4a] rounded-2xl rounded-tl-sm px-4 py-3">
                  <p className="text-sm text-[#ddd] whitespace-pre-wrap leading-relaxed">{msg.content}</p>
                </div>
                {msg.gear && (
                  <div className="mt-1.5 flex items-center gap-1.5">
                    <span
                      className="text-[10px] font-semibold px-2 py-0.5 rounded-full"
                      style={{
                        color: GEAR_STYLE[msg.gear].color,
                        background: GEAR_STYLE[msg.gear].bg,
                        border: `1px solid ${GEAR_STYLE[msg.gear].border}`,
                      }}
                    >
                      {GEAR_STYLE[msg.gear].label}
                    </span>
                  </div>
                )}
              </div>
            ) : (
              <div className="max-w-[80%] bg-[#3b1fa8] rounded-2xl rounded-tr-sm px-4 py-3">
                <p className="text-sm text-white whitespace-pre-wrap leading-relaxed">{msg.content}</p>
              </div>
            )}
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="bg-[#1a1a2e] border border-[#2a2a4a] rounded-2xl rounded-tl-sm px-4 py-3">
              <span className="text-[#666] text-sm">Thinking{dots}</span>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="px-4 py-4 border-t border-[#1e1e3a]">
        <div className="flex gap-3 items-end max-w-3xl mx-auto">
          <textarea
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={onKey}
            placeholder="Ask ARIA anything… or /sprint, /launch for deeper research"
            rows={1}
            disabled={loading}
            className="flex-1 bg-[#1a1a2e] border border-[#2a2a4a] rounded-xl px-4 py-3 text-sm text-white placeholder-[#444] resize-none focus:outline-none focus:border-[#a78bfa] transition-colors disabled:opacity-50"
            style={{ minHeight: "44px", maxHeight: "120px" }}
            onInput={e => {
              const t = e.currentTarget;
              t.style.height = "auto";
              t.style.height = Math.min(t.scrollHeight, 120) + "px";
            }}
          />
          <button
            onClick={send}
            disabled={loading || !input.trim()}
            className="bg-[#a78bfa] hover:bg-[#9061f9] disabled:opacity-30 disabled:cursor-not-allowed text-white font-semibold rounded-xl px-5 py-3 text-sm transition-colors shrink-0"
          >
            Send
          </button>
        </div>
        <p className="text-center text-[#333] text-[10px] mt-2">
          Shift+Enter for new line · /walk /sprint /launch to force a gear
        </p>
      </div>
    </div>
  );
}

// ── About / home page ──────────────────────────────────────────────────────

const gears = [
  {
    name: "WALK", cmd: "/walk",
    desc: "Casual chat — quick, direct reply.",
    agents: null,
    accent: "#a78bfa", bg: "#1a1a2e", border: "#2a2a4a",
  },
  {
    name: "SPRINT", cmd: "/sprint",
    desc: "Research mode — 3-agent swarm: Analyst, Skeptic, Strategist.",
    agents: ["Analyst", "Skeptic", "Strategist"],
    accent: "#60a5fa", bg: "#1a1a2e", border: "#2a2a4a",
  },
  {
    name: "LAUNCH", cmd: "/launch",
    desc: "Deep dive — 6-agent, 2-round swarm with cross-agent synthesis.",
    agents: ["Analyst", "Skeptic", "Strategist", "Historian", "Futurist", "Synthesizer"],
    accent: "#c084fc", bg: "#1e1028", border: "#7c3aed",
  },
];

const tools = [
  { icon: "🔍", name: "Web Search", desc: "Live DuckDuckGo search available to all agents" },
  { icon: "🧠", name: "Memory", desc: "Remembers your conversation across messages" },
  { icon: "📚", name: "Knowledge Base", desc: "Built-in knowledge available to every agent" },
  { icon: "⚡", name: "Make.com Automations", desc: "Gmail · Google Calendar · Sheets · Slack · Docs — just ask naturally" },
];

function AboutPage() {
  return (
    <div className="min-h-screen bg-[#0f0f1a] text-white flex flex-col items-center justify-center px-4 py-12">
      <div className="max-w-lg w-full">
        <div className="text-center mb-8">
          <div className="inline-flex items-center gap-2 bg-[#0d2b1f] border border-[#00e676] text-[#00e676] rounded-full px-5 py-2 text-sm font-semibold mb-6">
            <span className="w-2.5 h-2.5 rounded-full bg-[#00e676] animate-pulse" />
            LIVE
          </div>
          <h1 className="text-6xl font-bold tracking-widest text-[#a78bfa] mb-2">ARIA</h1>
          <p className="text-[#555] text-base mb-6">Multi-Agent AI Assistant</p>
          <Link
            href="/chat"
            className="inline-flex items-center gap-2 bg-[#a78bfa] hover:bg-[#9061f9] text-white font-semibold rounded-xl px-6 py-3 text-sm transition-colors"
          >
            Start chatting →
          </Link>
        </div>

        <div className="space-y-2 mb-6">
          {gears.map(g => (
            <div key={g.name} className="rounded-xl px-4 py-3 border" style={{ background: g.bg, borderColor: g.border }}>
              <div className="flex items-baseline gap-3 mb-1">
                <span className="font-bold text-sm" style={{ color: g.accent }}>{g.name}</span>
                <code className="text-[#555] text-xs">{g.cmd}</code>
              </div>
              <p className="text-[#888] text-xs mb-1.5">{g.desc}</p>
              {g.agents && (
                <div className="flex flex-wrap gap-1">
                  {g.agents.map(a => (
                    <span key={a} className="text-[9px] font-medium px-1.5 py-0.5 rounded-full"
                      style={{ background: `${g.accent}15`, color: g.accent, border: `1px solid ${g.accent}25` }}>
                      {a}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="space-y-2 mb-6">
          <p className="text-[#444] text-xs uppercase tracking-wider mb-2">Agent Tools</p>
          {tools.map(t => (
            <div key={t.name} className="flex items-start gap-3 bg-[#141422] border border-[#1e1e3a] rounded-xl px-4 py-3">
              <span className="text-base">{t.icon}</span>
              <div>
                <div className="text-[#a78bfa] text-xs font-semibold mb-0.5">{t.name}</div>
                <div className="text-[#555] text-xs">{t.desc}</div>
              </div>
            </div>
          ))}
        </div>

        <div className="grid grid-cols-3 gap-2">
          {[{ label: "Engine", value: "Groq" }, { label: "Model", value: "Llama 3" }, { label: "Framework", value: "LangGraph" }].map(({ label, value }) => (
            <div key={label} className="bg-[#141422] border border-[#1e1e3a] rounded-xl py-3 px-2 text-center">
              <div className="text-[#333] text-[9px] uppercase tracking-wider mb-1">{label}</div>
              <div className="text-white font-semibold text-xs">{value}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Router ─────────────────────────────────────────────────────────────────

const basePath = import.meta.env.BASE_URL.replace(/\/$/, "");

export default function App() {
  return (
    <Router base={basePath}>
      <Switch>
        <Route path="/" component={AboutPage} />
        <Route path="/chat" component={ChatPage} />
      </Switch>
    </Router>
  );
}
