from fpdf import FPDF

class PDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 22)
        self.set_text_color(15, 15, 15)
        self.cell(0, 14, "BABU", align="L")
        self.set_font("Helvetica", "", 11)
        self.set_text_color(80, 80, 80)
        self.cell(0, 14, "Multi-Agent Telegram AI Assistant", align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-14)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, "BABU Project Schema  -  Page " + str(self.page_no()), align="C")

    def section_title(self, title):
        self.ln(4)
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(30, 30, 30)
        self.set_fill_color(240, 242, 255)
        self.cell(0, 9, "  " + title, fill=True, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def body(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(40, 40, 40)
        self.multi_cell(0, 6, text)
        self.ln(1)

    def mono(self, text):
        self.set_font("Courier", "", 9)
        self.set_text_color(30, 30, 30)
        self.set_fill_color(248, 248, 248)
        self.multi_cell(0, 5.5, text, fill=True)
        self.ln(1)

    def table(self, headers, rows):
        self.set_font("Helvetica", "B", 9)
        self.set_fill_color(70, 90, 200)
        self.set_text_color(255, 255, 255)
        col_w = 190 // len(headers)
        for h in headers:
            self.cell(col_w, 7, h, fill=True, border=1)
        self.ln()
        self.set_font("Helvetica", "", 9)
        self.set_text_color(30, 30, 30)
        for i, row in enumerate(rows):
            self.set_fill_color(245, 246, 255) if i % 2 == 0 else self.set_fill_color(255, 255, 255)
            for cell in row:
                self.cell(col_w, 6, cell, fill=True, border=1)
            self.ln()
        self.ln(2)


pdf = PDF()
pdf.add_page()
pdf.set_auto_page_break(auto=True, margin=15)

# --- Overview ---
pdf.section_title("Overview")
pdf.body(
    "BABU is a Telegram bot powered by a LangGraph multi-agent pipeline. It automatically "
    "routes incoming messages into two gears - WALK for casual chat and SPRINT for deep "
    "research - and uses a 3-agent swarm (Analyst, Skeptic, Strategist) to synthesize "
    "thorough, well-rounded responses via a Llama 3 Personal Assistant."
)

# --- Agent Flow ---
pdf.section_title("Agent Flow")
pdf.mono(
    "  User Message (Telegram)\n"
    "         |\n"
    "         v\n"
    "   [intent_router]   <-  decides WALK or SPRINT\n"
    "         |\n"
    "         +-- WALK ----------------------------------------+\n"
    "         |   (casual chat / /walk command)                |\n"
    "         |                                                 |\n"
    "         +-- SPRINT                                        |\n"
    "             (research / /sprint command)                  |\n"
    "             |                                             |\n"
    "             v                                             |\n"
    "        [research_dept]                                    |\n"
    "        +------------------+                               |\n"
    "        |  ANALYST         |                               |\n"
    "        |  SKEPTIC         |                               |\n"
    "        |  STRATEGIST      |                               |\n"
    "        +------------------+                               |\n"
    "             |                                             |\n"
    "             +---------------------------------------------+\n"
    "                                                           |\n"
    "                                                           v\n"
    "                                                      [pa_node]\n"
    "                                                 (llama-3.3-70b)\n"
    "                                                           |\n"
    "                                                           v\n"
    "                                                 Telegram Reply"
)

# --- Gear System ---
pdf.section_title("Gear System")
pdf.table(
    ["Gear", "Trigger", "Behaviour"],
    [
        ["WALK",   "Casual chat  /  /walk command",   "Single PA call - brief and direct"],
        ["SPRINT", "Research query  /  /sprint cmd",  "3-agent swarm -> PA synthesis"],
    ]
)

# --- Tech Stack ---
pdf.section_title("Tech Stack")
pdf.table(
    ["Layer", "Tool", "Free Tier"],
    [
        ["Bot interface",     "python-telegram-bot",           "Free"],
        ["Orchestration",     "LangGraph + LangChain",         "Free"],
        ["Router + Research", "llama-3.1-8b-instant (Groq)",   "6,000 req/day"],
        ["PA final reply",    "llama-3.3-70b-versatile (Groq)", "6,000 req/day"],
        ["Hosting",           "Replit Reserved VM",            "Always-on"],
    ]
)

# --- Secrets ---
pdf.section_title("Required Secrets")
pdf.table(
    ["Secret", "Purpose"],
    [
        ["GROQ_API_KEY",       "Groq LLM API key (free at console.groq.com)"],
        ["TELEGRAM_BOT_TOKEN", "Telegram bot token from @BotFather"],
    ]
)

# --- File Structure ---
pdf.section_title("File Structure")
pdf.mono(
    "  babu/\n"
    "    bot.py           <- All BABU logic: router, research dept, PA, Telegram handler\n"
    "    generate_pdf.py  <- This PDF generator\n"
    "  replit.md          <- Project documentation & architecture\n"
    "  artifacts/\n"
    "    api-server/      <- Deployment config (artifact.toml)\n"
)

# --- Commands ---
pdf.section_title("Telegram Commands")
pdf.table(
    ["Input", "Behaviour"],
    [
        ["Any casual message",    "Auto-routes to WALK (quick reply)"],
        ["Any research question", "Auto-routes to SPRINT (3-agent swarm)"],
        ["/sprint in message",    "Forces SPRINT mode regardless of content"],
        ["/walk in message",      "Forces WALK mode regardless of content"],
    ]
)

# --- Notes ---
pdf.section_title("Important Notes")
pdf.body(
    "1. Bot uses polling (not webhooks) - must be deployed as a Reserved VM, NOT autoscale.\n"
    "2. Groq free tier: 6,000 requests/day and 30 requests/minute.\n"
    "3. SPRINT mode makes 4 Groq calls per message (1 router + 3 agents + 1 PA).\n"
    "4. LangGraph retry logic may delay error responses by ~30s when rate-limited."
)

pdf.output("BABU_Project_Schema.pdf")
print("PDF saved: BABU_Project_Schema.pdf")
