import { Router, Switch, Route } from "wouter";

function Home() {
  return (
    <div className="min-h-screen bg-[#0f0f1a] text-white flex flex-col items-center justify-center px-4">
      <div className="max-w-xl w-full text-center">

        <div className="inline-flex items-center gap-2 bg-[#0d2b1f] border border-[#00e676] text-[#00e676] rounded-full px-5 py-2 text-sm font-semibold mb-8">
          <span className="w-2.5 h-2.5 rounded-full bg-[#00e676] animate-pulse" />
          LIVE
        </div>

        <h1 className="text-6xl font-bold tracking-widest text-[#a78bfa] mb-3">ARIA</h1>
        <p className="text-[#888] text-lg mb-12">Multi-Agent Telegram AI Assistant</p>

        <div className="space-y-3 mb-12">
          <div className="bg-[#1a1a2e] border border-[#2a2a4a] rounded-xl px-6 py-4 flex items-start gap-4 text-left">
            <span className="text-[#a78bfa] font-bold text-sm mt-0.5 w-14 shrink-0">WALK</span>
            <span className="text-[#aaa] text-sm">Casual chat — quick, direct replies via Llama 3.3 70B</span>
          </div>
          <div className="bg-[#1a1a2e] border border-[#2a2a4a] rounded-xl px-6 py-4 flex items-start gap-4 text-left">
            <span className="text-[#a78bfa] font-bold text-sm mt-0.5 w-14 shrink-0">SPRINT</span>
            <span className="text-[#aaa] text-sm">Research mode — 3-agent swarm (Analyst + Skeptic + Strategist) synthesised by Llama 3.3 70B</span>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-4 mb-12">
          {[
            { label: "Engine", value: "Groq" },
            { label: "Model", value: "Llama 3" },
            { label: "Orchestration", value: "LangGraph" },
          ].map(({ label, value }) => (
            <div key={label} className="bg-[#1a1a2e] border border-[#2a2a4a] rounded-xl py-4 px-3">
              <div className="text-[#555] text-xs mb-1">{label}</div>
              <div className="text-white font-semibold text-sm">{value}</div>
            </div>
          ))}
        </div>

        <p className="text-[#444] text-xs">
          Send <span className="text-[#a78bfa]">/sprint</span> or <span className="text-[#a78bfa]">/walk</span> in any message to force a gear
        </p>
      </div>
    </div>
  );
}

const basePath = import.meta.env.BASE_URL.replace(/\/$/, "");

export default function App() {
  return (
    <Router base={basePath}>
      <Switch>
        <Route path="/" component={Home} />
      </Switch>
    </Router>
  );
}
