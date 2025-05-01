## RAI – Next-Level Automation Tool & Framework for Building LLM Agents and Teams in Cybersecurity

<h1 align="center">
  <img src="static/rai-demo.jpg" alt="RAI" width="450px">
  <br>
</h1>

  <div> 
 <div>

 <div align="center">

**lightweight, faster LLM Agents,Team building with YAML Configuration**

</div>


<p align="center">
    <a href="https://github.com/RevoltSecurities/RAI?tab=readme-ov-file#features">Features</a> |
    <a href="https://github.com/RevoltSecurities/RAI?tab=readme-ov-file#installation">Installation</a> |
    <a href="https://github.com/RevoltSecurities/RAI?tab=readme-ov-file#usage">Usage</a> |
    <a href="https://github.com/RevoltSecurities/RAI?tab=readme-ov-file#-yaml-configuration">Building Agents & Teams with YAML Configuration</a>
</p>

 <div align="center">

![GitHub last commit](https://img.shields.io/github/last-commit/RevoltSecurities/Subdominator) ![GitHub release (latest by date)](https://img.shields.io/github/v/release/RevoltSecurities/Subdominator) [![GitHub license](https://img.shields.io/github/license/RevoltSecurities/Subdominator)](https://github.com/RevoltSecurities/Subdominator/blob/main/LICENSE)

</div>



**RAI (Revolt AI Agent)** is a modern, YAML-driven CLI tool and framework for building intelligent agents and agent teams tailored for cybersecurity automation, offensive security, and penetration testing operations.
Built on top of the powerful [Agno framework](https://docs.agno.com), RAI enables security professionals, red teamers, and AI hackers to design, orchestrate, and deploy advanced LLM-powered agents without writing traditional code. Its no-code architecture leverages structured YAML configurations to define agent behavior, tools, and team collaboration logic.



### Features🔧:
---

<h1 align="center">

<img src="https://github.com/RevoltSecurities/RAI">
<br>
</h1>

- 🧠 **Interactive Shell Mode** – Engage in real-time conversations with LLM agents and teams via a powerful interactive CLI. Seamlessly switch between agents or teams with intuitive commands.
  
- 📝 **YAML-Based Agent & Team Building** – Define agents and teams using easy-to-edit YAML templates. Accelerate development with low-code configurations and smart defaults.
  
- 🤖 **Multi-Agent & Team Support** – Build, run, and manage multiple agents or teams in parallel with full operational isolation and coordination.
  
- 🔌 **Tool Integration (SSE & stdio)** – Integrate custom tools via Server-Sent Events or standard I/O for dynamic agent-tool communication.
  
- 🔄 **Dynamic Team Allocation** – Flexibly assign, reassign, or reconfigure agents across different teams at runtime to optimize task workflows.
  
- 🧩 **MCP-Compatible Infrastructure** – Built with modularity in mind, RAI is ready for integration with Model Context Protocol (MCP) tooling and future agent standards.
  
- ⚙️ **Fast & Flexible Configuration** – Lightweight setup with extensible configuration options. Designed for developers who want control without the clutter.
  
- 🧠 **Built-In Reasoning Engine** – Agents can *think*, *reason*, and *decide* intelligently before taking action, enabling smarter task execution.
  
- 🛡️ **Cybersecurity-First Design** – Purpose-built for red teaming, bug bounty automation, recon, exploit development, and offensive security workflows.
  
- 🧬 **Agent-to-Agent Communication** – Enable inter-agent messaging within teams, allowing agents to delegate tasks, collaborate, and share results autonomously.


### Supported LLM Providers:
- **Anthropic**
- **AWS**
- **Azure**
- **Cohere**
- **DeepInfra**
- **DeepSeek**
- **Fireworks**
- **Google (Gemini)**
- **Groq**
- **Hugging Face**
- **IBM**
- **InternLM**
- **LiteLLM**
- **LMStudio**
- **Meta (LLaMA)**
- **Mistral**
- **NVIDIA**
- **Ollama**
- **OpenAI**
- **OpenRouter**
- **Perplexity**
- **SambaNova**
- **Together.ai**
- **xAI**


### Installation🚀

RAI can be easily installed using [**uv**](https://github.com/astral-sh/uv) — a fast Python package manager designed for modern workflows.

### 📦 Install with `uv`

```bash
uv tool install rai
```

> ✅ Make sure you have Python 3.13 or newer installed.  
> ✅ `uv` automatically handles virtual environments, speed, and dependency resolution.

  
### Usage:
---
```code
rai -h
```

```yaml
    ____     ___     ____
   / __ \   /   |   /  _/
  / /_/ /  / /| |   / /  
 / _, _/  / ___ | _/ /   
/_/ |_|  /_/  |_|/___/   
                         

                     - RevoltSecurities


[DESCRIPTION]: 

    RAI is a next-gen CLI tool and framework to automate the creation of intelligent agents and teams for cybersecurity and offensive security operations

[USAGE]: 

    rai [flags]

[FLAGS]:

    -h,    --help                 :  Show this help message and exit.
    -v,    --version              :  Show current version of RAI.
    -cp,   --config-path          :  Path to YAML config file (default: $HOME/.config/RAI/raiagent.yaml).
    -sup,  --show-updates         :  Show latest update details.
    -up,   --update               :  Update RAI to the latest version (manual YAML update).

```


# 🛠 YAML Configuration

RAI allows you to define **AI Agents** and **Agent Teams** using a simple YAML configuration. This configuration determines how agents behave, what models they use, what tools are attached, and how they collaborate as teams.

---

## ✳️ Agent Configuration (`agents`)

Each agent must define the following **required fields**:

| Field         | Type     | Description |
|--------------|----------|-------------|
| `name`        | string   | Unique agent name, must use `-` or `_` only (e.g., `web_pentest_agent`) |
| `model`       | string   | The provider name (e.g., `openai`, `gemini`, `groq`, `xai`) |
| `model-id`    | string   | The specific model ID to use (e.g., `gpt-4`, `gemini-2.0-pro`) |
| `apikey`      | string   | API key for the chosen provider  |
| `role`        | string   | A short sentence describing the agent’s purpose |
| `description` | string   | Multi-line detailed description of the agent’s capabilities |
| `instructions`| string   | A clear set of multi-line operational guidelines for the agent |
| `tools`       | toolconfig     | Tools assigned to the agent (`sse` or `stdio` types with required params) |


> ✅ The only **optional field** is `think: true`, which enables your agent to think and analyze before its response

---

## 🧠 Team Configuration (`teams`)

Teams are collaborative groups of agents that share analysis tasks.

Each team must define the following **required fields**:

| Field         | Type     | Description |
|--------------|----------|-------------|
| `name`        | string   | Team name, must use `-` or `_` only (e.g., `pentest_team`) |
| `mode`        | string   | Team mode (e.g., `coordinate`, `route`, `collaborate`) |
| `model`       | string   | Provider name for internal logic (same as in agents) |
| `model-id`    | string   | Model ID used for internal processing |
| `apikey`      | string   | API key for the team’s model |
| `instructions`| string   | Multi-line instructions on how the team should collaborate |
| `members`     | list     | List of agent names (must match agent `name` fields) |
| `tools`       | toolconfig     | Tools assigned to the agent (`sse` or `stdio` types with required params) |
| `success_criteria` | string | Configure your teams collaboration success criteria to achieve your goal for the RAI Team task

> ✅ `think: true` is an **optional field** to allow team-wide reasoning before responding.

---

## 🧩 Agent Naming Convention

To ensure consistency and compatibility:

- Agent and team `name` fields must:
  - Be unique across agents and teams
  - Use only lowercase characters, numbers, `-`, or `_`
  - Not contain spaces or special characters

✅ **Valid:** `api_pentest_agent`, `web-agent-1`  
❌ **Invalid:** `Agent 01`, `Web*Pentest`

---

## 🔗 Team Member Allocation Rules

- `members` must list agent names **already defined** under the `agents:` section.
- All member agents **must be valid and fully configured** before referencing them in a team.
- Duplicate agent names or undeclared agents will raise errors.

---

### 🔧 Tools Configuration

Agents or Teams can integrate external or internal MCP tools via yaml configuration:

#### SSE Tool (Server-Sent Events)
```yaml
- type: "sse"
  name: "tool_name"
  params:
    url: "http://host:port/endpoint"
    headers:
      Authorization: "Bearer your_token_here"
```

#### Stdio Tool (Local MCP server execution)
```yaml
- type: "stdio"
  name: "tool_name"
  params:
    command: "command_to_run (ex:uv)"
    args: ["arg1", "arg2", "argN"]
```
---


## 📦 Sample Full YAML Configuration

```yaml
agents:
  - name: "web_pentest_agent"
    model: "gemini"
    model-id: "gemini-2.0-flash-exp"
    apikey: "AIzaSyDnd-REDACTED-1234567890"
    role: "An expert web application penetration tester."
    description: |
      This agent performs in-depth analysis of web applications, including:
      - XSS, SQLi, CSRF, SSRF detection
      - Payload recommendations
      - Mitigation advice using OWASP guidelines
    instructions: |
      - Analyze HTML, JS, HTTP requests
      - Follow ethical boundaries
      - Provide clear markdown-formatted results
    tools:
      - type: "sse"
        name: "web_tool"
        params:
          url: "http://localhost:8000/sse"
          headers:
            Authorization: "Bearer sample-token-123"
    markdown: true
    enable_history: true

  - name: "api_pentest_agent"
    model: "gemini"
    model-id: "gemini-2.0-flash-exp"
    apikey: "AIzaSyDnd-REDACTED-0987654321"
    role: "API security expert."
    description: |
      Focused on discovering API vulnerabilities:
      - BOLA, Mass Assignment, Broken Auth
      - CORS and Rate Limiting checks
    instructions: |
      Use OWASP API Top 10 as the baseline. Respond only with ethical suggestions.
    tools:
      - type: "stdio"
        name: "api_tool"
        params:
          command: "python3"
          args: ["api_runner.py"]
    markdown: true
    enable_history: true

  - name: "ctf_agent"
    model: "gemini"
    model-id: "gemini-2.0-flash-exp"
    apikey: "AIzaSyDnd-REDACTED-CTFKEY"
    role: "CTF solving agent"
    description: |
      Solves CTF challenges in web, pwn, reverse engineering, crypto, and forensics.
    instructions: |
      - Give step-by-step answers
      - Output payloads and flag extraction logic
    markdown: true
    enable_history: true

teams:
  - name: "pentest_team_alpha"
    mode: "coordinate"
    model: "gemini"
    model-id: "gemini-2.0-flash-exp"
    apikey: "AIzaSyDnd-REDACTED-TEAMKEY"
    instructions: |
      Collaborate across agents to detect and explain vulnerabilities in web or API apps.
    tools:
      - type: "stdio"
        name: "api_tool"
        params:
          command: "python3"
          args: ["api_runner.py"]
    members:
      - "web_pentest_agent"
      - "api_pentest_agent"
      - "ctf_agent"
    success_criteria: "All vulnerabilities are clearly identified, explained, and mitigated."
    think: true
```

---

### 🚀 Future Enhancement Plan:

RAI (Revolt AI Agent) is under **continuous development** 🛠️ — evolving rapidly to empower cybersecurity automation with intelligent, collaborative agents. Upcoming features include:

- 🖥 **Agent UI with Agno UI Integration**  
  A powerful web-based UI to manage, monitor, and interact with agents, tools, and teams visually — built on Agno's robust interface layer.

- 🧠 **Memory & Session Storage Management**  
  Agents will support persistent session memory:
  - Maintain conversation continuity  
  - Recall user preferences, task history, and learned context

- 📚 **Agent & Team Knowledge Injection**  
  Allow agents and teams to use **custom knowledge bases**, enabling them to:
  - Ingest structured/unstructured data sources (e.g., markdown, PDFs, JSON, code)  
  - Improve reasoning and task performance through embedded knowledge  
  - Learn iteratively and adapt during operations

- 🧩 **Agent Knowledge Learning Loop**  
  Equip agents with mechanisms to analyze outcomes, refine their behavior, and build contextual awareness from completed tasks.


> ⚠️ **RAI is Under Continuous Development**
> 
> ───────────────────────────────────────────────
> 
> 🛠️ RAI (Revolt AI Agent) is an actively evolving project built on top of the powerful **Agno** framework.
> This means you can expect:
>
> 🔄 Regular updates & new feature drops  
> 🧪 Experimental support for cutting-edge agent workflows  
> 🔧 Frequent performance and usability improvements  
> 🧰 Expanding tool integrations and LLM backend compatibility  
> 📦 Community-driven contributions & enhancements welcome!
> 
> While RAI is already production-capable, it’s designed to grow fast—
> so expect changes, iteration, and rapid innovation.
> 
> ➕ Stay updated. Join the journey. Contribute. Hack with AI.
> ───────────────────────────────────────────────

---

### ❤️ Acknowledgements & Community Contribution:

A special thanks to the [**Agno Framework**](https://github.com/agno-agi/agno) for providing a powerful foundation for RAI. Their contributions have made it possible to build a sophisticated, flexible, and scalable platform that empowers cybersecurity professionals worldwide. 🙏

RAI (Revolt AI Agent) is developed with ❤️ by [**RevoltSecurities**](https://github.com/RevoltSecurities), driven by a passion for open-source and cybersecurity innovation. We are excited to share this tool with the community and empower the next generation of red teamers, security researchers, and AI hackers. 🚀
We **welcome** contributions, ideas, and feedback from the open-source community. Together, we can make RAI even more powerful and continue to drive innovation in the cybersecurity field. 
Your contributions, whether in the form of code, documentation, bug reports, or ideas, are highly appreciated. Let's build, learn, and grow together! 🤝


