## RAI – Next-Level Automation Tool & Framework for Building LLM Agents and Teams in Cybersecurity

<h1 align="center">
  <img src="rai-demo.jpg" alt="RAI" width="450px">
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

### Features🔧:
---

<h1 align="center">

<img src="https://github.com/RevoltSecurities/RAI">
<br>
</h1>

- **Interactive Shell Mode** - RAI developed with advance shell interface that allows user to switch between built LLM Teams and LLM Agents and start conversation with selected agents/teams
- **Low-Code YAML-Based Agent & Team Building** – Automate complex LLM agent/team setups with easy-to-edit YAML templates.  
- **Multiple Agent & Team Support** – Build, manage, and run multiple agents or teams in parallel with full isolation.  
- **Tool Integration (SSE & stdio)** – Seamlessly plug in tools using Server-Sent Events or standard I/O for real-time interaction.  
- **Dynamic Team Allocation** – Assign and reconfigure agents across teams dynamically.  
- **MCP-Compatible Infrastructure** – Built for modularity and future compatibility with Model Context Protocols Tools support.  
- **Fast & Flexible Configuration** – Lightweight setup with smart defaults and extensible configuration options.  
- **Built-In Reasoning Engine** – Agents can *think*, *analyze*, and *respond* more intelligently before executing tasks.  
- **Designed for Cybersecurity Automation** – Tailored for offensive security tasks like recon, exploitation, and team coordination.
- **Agent2Agent Communication** - LLM Team use your specialized agent for specific tasks and communicate with each other which make different agent to communicate

- **Multiple LLM Model Providers** - RAI supports wide range of LLM Provider like OpenAI, Gemini, Grok, Groq, xAI, Ollama and more


### Supported LLM Providers
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
