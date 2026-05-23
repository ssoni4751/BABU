import { Router, Switch, Route } from "wouter";

const gears = [
  {
    name: "WALK",
    cmd: "/walk",
    desc: "Casual chat — quick, direct reply. Single PA call.",
    agents: null,
    accent: "#a78bfa",
    bg: "#1a1a2e",
    border: "#2a2a4a",
  },
  {
    name: "SPRINT",
    cmd: "/sprint",
    desc: "Research mode — 3-agent swarm synthesised into a sharp brief.",
    agents: ["Analyst", "Skeptic", "Strategist"],
    accent: "#a78bfa",
    bg: "#1a1a2e",
    border: "#2a2a4a",
  },
  {
    name: "LAUNCH",
    cmd: "/launch",
    desc: "Deep dive — 6-agent, 2-round swarm with cross-agent synthesis.",
    agents: ["Analyst", "Skeptic", "Strategist", "Historian", "Futurist", "Synthesizer"],
    accent: "#c084fc",
    bg: "#1e1028",
    border: "#7c3aed",
  },
];

function Home() {
  return (
    <div className="min-h-screen bg-[#0f0f1a] text-white flex flex-col items-center justify-center px-4 py-12">
      <div className="max-w-lg w-full">

        <div className="text-center mb-10">
          <div className="inline-flex items-center gap-2 bg-[#0d2b1f] border border-[#00e676] text-[#00e676] rounded-full px-5 py-2 text-sm font-semibold mb-7">
            <span className="w-2.5 h-2.5 rounded-full bg-[#00e676] animate-pulse" />
            LIVE
          </div>
          <h1 className="text-6xl font-bold tracking-widest text-[#a78bfa] mb-2">ARIA</h1>
          <p className="text-[#666] text-base">Multi-Agent Telegram AI Assistant</p>
        </div>

        <div className="space-y-3 mb-10">
          {gears.map((g) => (
            <div
              key={g.name}
              className="rounded-xl px-5 py-4 border"
              style={{ background: g.bg, borderColor: g.border }}
            >
              <div className="flex items-baseline gap-3 mb-1">
                <span className="font-bold text-sm" style={{ color: g.accent }}>{g.name}</span>
                <code className="text-[#555] text-xs">{g.cmd}</code>
              </div>
              <p className="text-[#aaa] text-sm mb-2">{g.desc}</p>
              {g.agents && (
                <div className="flex flex-wrap gap-1.5">
                  {g.agents.map((a) => (
                    <span
                      key={a}
                      className="text-[10px] font-medium px-2 py-0.5 rounded-full"
                      style={{
                        background: `${g.accent}18`,
                        color: g.accent,
                        border: `1px solid ${g.accent}30`,
                      }}
                    >
                      {a}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="grid grid-cols-3 gap-3 mb-8">
          {[
            { label: "Engine", value: "Groq" },
            { label: "Model", value: "Llama 3" },
            { label: "Orchestration", value: "LangGraph" },
          ].map(({ label, value }) => (
            <div key={label} className="bg-[#1a1a2e] border border-[#2a2a4a] rounded-xl py-3 px-3 text-center">
              <div className="text-[#444] text-[10px] uppercase tracking-wider mb-1">{label}</div>
              <div className="text-white font-semibold text-sm">{value}</div>
            </div>
          ))}
        </div>

        <p className="text-center text-[#333] text-xs">
          ARIA auto-routes every message — or force a gear with <span className="text-[#a78bfa]">/walk</span>, <span className="text-[#a78bfa]">/sprint</span>, <span className="text-[#a78bfa]">/launch</span>
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
