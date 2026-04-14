# ✈️ DailyDebrief — Flight Data Recorder for Your Dev Day

> *Turn your raw activity into structured insight.*
> DailyDebrief collects signals from your development workflow, compresses them intelligently, and generates a clear, structured report of what actually happened during your day.

---

## 🚀 Overview

**DailyDebrief** is a developer productivity intelligence tool inspired by **Flight Data Recorders (FDR)** — systems that continuously capture signals without deciding what's important upfront.

Instead of manually reflecting on your work, this tool:

* Observes your activity (git, shell, files)
* Extracts meaningful signals
* Sends compressed context to a local LLM
* Produces a clean, structured debrief

The result is a **daily technical reflection** that is:

* Specific
* Data-driven
* Actionable

---

## 🧠 Core Concept

```
SENSORS → COMPRESSION → ANALYSIS → REPORT
```

### Why this matters:

Most tools fail because they:

* Collect too little data → miss context
* Or collect too much → overwhelm the model

DailyDebrief solves this by:

* Capturing **broad signals**
* Compressing them into **high-value summaries**
* Feeding only the **most relevant context** to the LLM

---

## 📡 Sensor Streams

DailyDebrief collects multiple independent signals from your system:

### ✈️ Git Activity

* Recent commits
* Insertions / deletions
* Changed files
* Branch state
* Uncommitted changes

### ✈️ Shell History

* Commands executed
* Command frequency
* Error keyword detection
* Repeated command patterns
* Pip installs

### ✈️ File Modifications

* Recently edited files
* File type distribution
* “Hot files” (iterated multiple times)

### ✈️ System Snapshot (optional)

* Memory usage
* Disk usage
* Uptime
* Process count

---

## 🧩 Unique Features

### 🔥 Frustration Score

Detects developer friction based on:

* Error keywords (`error`, `failed`, `not found`, etc.)
* Repeated commands
* Behavioral patterns

Outputs:

```
LOW / MEDIUM / HIGH / CRITICAL
```

---

### 🧠 Smart Compression

Before sending data to the LLM:

* Raw logs are reduced
* Redundant data is removed
* Key signals are prioritized

➡️ Prevents context overflow
➡️ Improves output quality

---

### 📈 Streak Tracking

Tracks consecutive days of usage:

* Stored in `~/.debriefs/`
* Builds consistency awareness

---

### 🧪 Behavioral Insights

Not just *what you did*, but:

* Where you struggled
* What you repeated
* What patterns emerge

---

### 🎨 Rich Terminal UI

Built with `rich` for:

* Panels
* Color-coded insights
* Clean layout
* Readable summaries

---

## 🧾 Output Format

The LLM generates **exactly 5 structured insights**:

* 🔨 **BUILT** — what you worked on
* 💥 **BROKE** — what caused friction
* 💡 **LEARNED** — key insight
* 🚀 **NEXT** — next priority
* ✨ **ONELINER** — summary of the day

---

## ⚙️ Installation

### 1. Install dependencies

```bash
pip install ollama gitpython rich psutil
```

---

### 2. Install Ollama

Download and install from:

```
https://ollama.com/download
```

---

### 3. Start Ollama server

```bash
ollama serve
```

---

### 4. Pull a model

```bash
ollama pull qwen2.5:3b
```

---

## ▶️ Usage

### Default (last 24 hours)

```bash
python daily_debrief.py
```

---

### Custom time window

```bash
python daily_debrief.py --since 8
```

---

### Use different model

```bash
python daily_debrief.py --model llama3
```

---

### No LLM (debug mode)

```bash
python daily_debrief.py --no-llm
```

---

## 📁 Output Storage

Debriefs are automatically saved to:

```
~/.debriefs/YYYY-MM-DD.json
```

Includes:

* Full LLM output
* Key stats (commits, files, commands)
* Frustration score

---

## 🏗 Architecture

### Modular Design

Each sensor is isolated:

```
collect_git()
collect_shell()
collect_files()
collect_system()
```

➡️ Easy to extend
➡️ Fault-tolerant

---

### LLM Pipeline

```
Raw Data → Compression → Prompt → JSON Output
```

* Strict schema enforcement
* JSON parsing validation
* Fallback handling

---

## ⚡ Performance Considerations

* File scanning is capped
* Shell history is limited
* LLM input is compressed
* Optional system metrics

---

## ⚠️ Limitations

* Shell timestamps may be unreliable (depending on shell)
* File scanning can be slow on large directories
* Requires local LLM setup (Ollama)
* Not fully real-time (batch-based)

---

## 🔮 Future Improvements

* Real timeline reconstruction
* Weekly / monthly reports
* Web dashboard (Streamlit)
* Pattern detection (“you get stuck on X”)
* Async sensor execution
* Cross-device sync

---

## 🆚 Comparison to Typical Tools

| Feature               | DailyDebrief |
| --------------------- | ------------ |
| Multi-sensor analysis | ✅            |
| Behavioral insights   | ✅            |
| Frustration detection | ✅            |
| LLM compression       | ✅            |
| Structured output     | ✅            |
| Visual terminal UI    | ✅            |

---

## 💡 Use Cases

* Daily reflection for developers
* Debugging workflow inefficiencies
* Tracking productivity trends
* Building engineering habits
* Portfolio / self-analysis tool

---

## 🧠 Philosophy

> The system doesn’t decide what matters.
> It records everything — and lets analysis reveal meaning.

---

## 📌 Final Note

This is not just a script.

It’s a **developer observability system** in miniature —
designed to help you understand not just your code, but your **process**.

---

## ⭐ If you like this project

* Star the repo
* Fork it
* Build your own sensors
* Turn it into something bigger

---

**DailyDebrief — because your work deserves to be understood.**
