from typing import List, Dict, Any, Optional
import yaml
from dataclasses import dataclass
import aiofiles
from agno.agent import Agent
from agno.team.team import Team
from agno.tools.mcp import MCPTools, SSEClientParams, StreamableHTTPClientParams
from mcp import StdioServerParameters
from textwrap import dedent
import nest_asyncio
from agno.tools.reasoning import ReasoningTools

from rai.modules.toolconfig.toolconfig import ToolConfig
from rai.modules.teamconfig.teamconfig import TeamConfig
from rai.modules.modelconfig.modelconfig import ModelConfig, ModelBuilder
from rai.modules.logger.logger import Logger
from agno.memory.v2.db.sqlite import SqliteMemoryDb
from agno.memory.v2.memory import Memory
from agno.storage.agent.sqlite import SqliteAgentStorage
from rai.modules.config.config import Config
nest_asyncio.apply()

@dataclass
class AgentConfig:
    name: str
    model: str
    model_id: str
    instructions: str
    apikey: Optional[str] = None
    role: Optional[str] = None
    description: Optional[str] = None
    tools: List[ToolConfig] = None
    think: bool = False
    num_history_runs: int = 15


class AgentBuilder():
    def __init__(self, configfile: str, logging: bool = True):
        self.logging: bool = logging
        self.configfile = configfile
        self.agents_configured: Dict[str, AgentConfig] = {}
        self.teams_configured: Dict[str, TeamConfig] = {}
        self._builded_agents: Dict[str, Agent] = {}
        self._builded_teams: Dict[str, Team] = {}
        self.active_tools: List[Any] = []
        self._config_data = None
        self.logger = Logger()
        self._agent_storage: Optional[SqliteAgentStorage] = None
        self._team_storage: Optional[SqliteAgentStorage] = None
        self._user_memories: Optional[Memory] = None
        self.user_id: Optional[str] = None
        self.session_id: Optional[str] = None
        self._configs = Config("RAI")
        self._shared_db = self._configs.shared_session_db()
        self._memory_db = self._configs.user_db()
        self._memory_enabled: bool = False


    async def Load_Config(self):
        try:
            async with aiofiles.open(self.configfile, mode="r") as streamr:
                data = yaml.safe_load(await streamr.read())
                self._config_data = data
            if self.logging:
                self.logger.info(f"Configuration loaded successfully from '{self.configfile}'.")
        except FileNotFoundError:
            self.logger.error(f"Configuration file not found at '{self.configfile}'. Please ensure the path exists.")
            raise 
        except yaml.YAMLError as e:
            self.logger.error(f"Error parsing YAML configuration file '{self.configfile}': {e}")
            raise 
        except Exception as e:
            self.logger.error(f"An unexpected error occurred while loading configuration file '{self.configfile}': {e}")
            raise 

        await self._load_memories()
        await self._load_agents()
        await self._load_teams()

    async def _create_tool(self, tool_config: ToolConfig) -> Any:
        try:
            if tool_config.type == "sse":
                sse_params = tool_config.params
                url = sse_params.get("url")
                timeout = sse_params.get("timeout")
                
                if not url:
                    raise ValueError(f"Missing 'url' for SSE tool '{tool_config.name}'.")
                if timeout is None:
                    raise ValueError(f"Missing 'timeout' for SSE tool '{tool_config.name}'.")

                mcp_tool = MCPTools(
                    transport="sse",
                    server_params=SSEClientParams(
                        url=url,
                        headers=sse_params.get("headers", {}),
                        timeout=timeout,
                        sse_read_timeout=timeout
                    ),
                    timeout_seconds=timeout
                )
                await mcp_tool.__aenter__()
                self.active_tools.append(mcp_tool)

                return mcp_tool

            elif tool_config.type == "stdio":
                stdio_params = tool_config.params
                command = stdio_params.get("command")
                
                if not command:
                    raise ValueError(f"Missing 'command' for STDIO tool '{tool_config.name}'.")

                mcp_tool = MCPTools(
                    server_params=StdioServerParameters(
                        command=command,
                        args=stdio_params.get("args", []),
                        env=stdio_params.get("env", {})
                    ),
                    timeout_seconds=1800 
                )
                await mcp_tool.__aenter__()
                self.active_tools.append(mcp_tool)
                
                return mcp_tool
            
            elif tool_config.type == "streamable-http":
                stream_params = tool_config.params
                url = stream_params.get("url")
                timeout = stream_params.get("timeout")

                if not url:
                    raise ValueError(f"Missing 'url' for streamable-http tool '{tool_config.name}'.")
    
                headers = stream_params.get("headers")
                if not headers:
                    headers = {"Accept": "text/event-stream"}
                else:
                    if 'Accept' not in headers and 'accept' not in headers:
                         headers['Accept'] = 'text/event-stream'

                mcp_tool =  MCPTools(
                    transport="streamable-http",
                    url=url,
                    
                    timeout_seconds=timeout, 
                )
                await mcp_tool.__aenter__()
                self.active_tools.append(mcp_tool)
                return mcp_tool
            else:
                raise ValueError(f"Unknown tool type: {tool_config.type}")
        except RuntimeError:
            pass
        except Exception as e:
            self.logger.error(f"Error creating tool '{tool_config.name}' (type: {tool_config.type}): {e}")
            raise 
        
    async def disconnect_tools(self):
        try:
            for tool in self.active_tools:
                if hasattr(tool, '__aexit__'):
                    await tool.__aexit__(None, None, None)
        except RuntimeError:
            if self.logging:
                self.logger.warn("RuntimeError during tool disconnection, possibly event loop already closed.")
        except Exception as e:
            if self.logging:
                self.logger.error(f"Error disconnecting tools due to: {e}")
        finally:
            self.active_tools = []
            if self.logging:
                self.logger.info("All active tools list cleared.")


    async def _load_agents(self):
        if self.logging:
            self.logger.info("Loading agent configurations...")
        try:
            for agent_config_data in self._config_data.get("agents", []):
                try:
                    agent_name = agent_config_data.get("name")
                    if not agent_name:
                        self.logger.error(f"Agent configuration missing 'name': {agent_config_data}. Skipping this agent.")
                        continue 

                    tools = [ToolConfig(**tool) for tool in agent_config_data.get("tools", [])]
                    
                    self.agents_configured[agent_name] = AgentConfig(
                        name=agent_name,
                        model=agent_config_data["model"],
                        model_id=agent_config_data["model-id"],
                        instructions=agent_config_data["instructions"],
                        role=agent_config_data.get("role"),
                        description=agent_config_data.get("description"),
                        apikey=agent_config_data.get("apikey"),
                        tools=tools,
                        think=agent_config_data.get("think", False),
                        num_history_runs=agent_config_data.get("num_of_interactions_from_history", 15)
                    )
                    
                except KeyError as e:
                    self.logger.error(f"Missing required key '{e}' in agent configuration for '{agent_config_data.get('name', 'unknown')}': {agent_config_data}. Skipping.")
                    continue
                except Exception as e:
                    self.logger.error(f"Error processing agent configuration for '{agent_config_data.get('name', 'unknown')}': {e}. Skipping.")
                    continue
            if self.logging:
                self.logger.info(f"Finished loading {len(self.agents_configured)} agent configurations.")
        except Exception as e:
            self.logger.error(f"An error occurred during overall agent loading: {e}")
            raise 

    async def _load_memories(self):
        memory_config = self._config_data.get("memory", {})
        if not isinstance(memory_config, dict) or not memory_config:
            if self.logging:
                self.logger.warn("Memory configuration is missing or not properly defined as a dictionary, skipping from configuring memory for agent and teams!")
            self._memory_enabled = False
            return

        self.user_id = memory_config.get("user-id")
        self.session_id = memory_config.get("session-id")

        if not self.user_id:
            if self.logging:
                self.logger.error("Configuration error: 'user-id' is missing in the 'memory' section of the config. Skipping memory configuration.")
            self._memory_enabled = False
            return
        if not self.session_id:
            if self.logging:
                self.logger.error("Configuration error: 'session-id' is missing in the 'memory' section of the config. Skipping memory configuration.")
            self._memory_enabled = False
            return
        if not memory_config.get("model") or not memory_config.get("model-id"):
            if self.logging:
                self.logger.error("Configuration error: 'model' or 'model-id' is missing in the 'memory' section of the config. Skipping memory configuration.")
            self._memory_enabled = False
            return
        if not memory_config.get("memory-context"):
            if self.logging:
                self.logger.error("Configuration error: 'memory-context' is missing in the 'memory' section of the config. Skipping memory configuration.")
            self._memory_enabled = False
            return

        try:
            memory_db = SqliteMemoryDb(table_name="memory", db_file=self._memory_db)
            llmmodel = await self._get_model(memory_config["model"], memory_config["model-id"], memory_config.get("apikey"))
            self._user_memories = Memory(model=llmmodel, db=memory_db)
            self._user_memories.create_user_memories(message=memory_config["memory-context"], user_id=self.user_id)
            self._agent_storage = SqliteAgentStorage(table_name="agent_chat_sessions", db_file=self._shared_db)
            self._team_storage = SqliteAgentStorage(table_name="team_chat_sessions", db_file=self._shared_db)
            self._memory_enabled = True
            if self.logging:
                self.logger.info("Memory configuration loaded successfully.")
        except Exception as e:
            if self.logging:
                self.logger.error(f"Error loading memory configuration: {e}. Skipping memory configuration.")
            self._memory_enabled = False


    async def _load_teams(self):
        try:
            for team_config_data in self._config_data.get("teams", []):
                try:
                    team_name = team_config_data.get("name")
                    if not team_name:
                        self.logger.error(f"Team configuration missing 'name': {team_config_data}. Skipping this team.")
                        continue

                    tools = [ToolConfig(**tool) for tool in team_config_data.get("tools", [])]
                    self.teams_configured[team_name] = TeamConfig(
                        name=team_name,
                        model=team_config_data["model"],
                        model_id=team_config_data["model-id"],
                        instructions=team_config_data["instructions"],
                        mode=team_config_data["mode"],
                        members=team_config_data["members"],
                        apikey=team_config_data.get("apikey"),
                        tools=tools,
                        success_criteria=team_config_data["success_criteria"],
                        think=team_config_data.get("think", False),
                        num_history_runs=team_config_data.get("num_of_interactions_from_history", 15)
                    )
                    
                except KeyError as e:
                    self.logger.error(f"Missing required key '{e}' in team configuration for '{team_config_data.get('name', 'unknown')}': {team_config_data}. Skipping.")
                    continue
                except Exception as e:
                    self.logger.error(f"Error processing team configuration for '{team_config_data.get('name', 'unknown')}': {e}. Skipping.")
                    continue
            if self.logging:
                self.logger.info(f"Finished loading {len(self.teams_configured)} team configurations.")
        except Exception as e:
            self.logger.error(f"An error occurred during overall team loading: {e}")
            raise

    async def _get_model(self, provider: str, model_id: str, apikey: str = None):
        try:
            model_config = ModelConfig(
                provider=provider,
                modelid=model_id,
                apikey=apikey
            )
            llmmodel = ModelBuilder.build(model_config)
            return llmmodel
        except Exception as e:
            self.logger.error(f"Error building model for provider '{provider}' and model_id '{model_id}': {e}")
            raise

    async def Build_agent(self, agent_name: str):
        if agent_name in self._builded_agents:
            
            return self._builded_agents[agent_name]

        try:
            if agent_name not in self.agents_configured:
                raise ValueError(f"Agent '{agent_name}' not found in configuration.")

            agent_configured: AgentConfig = self.agents_configured[agent_name]

            tools = []
            for tool_config in agent_configured.tools:
                try:
                    tool = await self._create_tool(tool_config)
                    tools.append(tool)
                except Exception as e:
                    self.logger.error(f"Failed to create tool '{tool_config.name}' for agent '{agent_name}': {e}. Skipping this tool.")

            if agent_configured.think:
                tools.append(ReasoningTools(add_instructions=True))
            
            llmmodel = await self._get_model(agent_configured.model, agent_configured.model_id, agent_configured.apikey)

            agent_kwargs = {
                "name": agent_name,
                "model": llmmodel,
                "tools": tools,
                "role": agent_configured.role,
                "description": dedent(agent_configured.description),
                "instructions": dedent(agent_configured.instructions),
                "add_datetime_to_instructions": True,
                "num_history_runs": agent_configured.num_history_runs,
                "add_history_to_messages": True,
                "markdown": True,
                "enable_agentic_memory": False,
                "enable_user_memories": False,
                "read_chat_history": False
            }

            if self._memory_enabled:
                agent_kwargs["session_id"] = self.session_id
                agent_kwargs["user_id"] = self.user_id
                agent_kwargs["memory"] = self._user_memories
                agent_kwargs["storage"] = self._agent_storage
                agent_kwargs["enable_agentic_memory"] = True
                agent_kwargs["enable_user_memories"] = True
                agent_kwargs["read_chat_history"] = True

            agent = Agent(**agent_kwargs)

            self._builded_agents[agent_name] = agent
            return agent
        except Exception as e:
            self.logger.error(f"Error building agent '{agent_name}': {e}")
            raise 

    async def Build_Team(self, team_name: str) -> Team:
        
        if team_name in self._builded_teams:  
            return self._builded_teams[team_name]

        try:
            if team_name not in self.teams_configured:
                raise ValueError(f"Team '{team_name}' not found in configuration.")

            team_configured: TeamConfig = self.teams_configured[team_name]

            members = []
            for agent_name in team_configured.members:
                try:
                    agent = await self.Build_agent(agent_name)
                    members.append(agent)
                except Exception as e:
                    self.logger.error(f"Failed to build member agent '{agent_name}' for team '{team_name}': {e}. Skipping this member.")
                    continue

            if not members:
                raise ValueError(f"Team '{team_name}' has no valid members. Cannot build team.")

            team_tools = []
            for tool_config in team_configured.tools:
                try:
                    tool = await self._create_tool(tool_config)
                    team_tools.append(tool)
                except Exception as e:
                    self.logger.error(f"Failed to create tool '{tool_config.name}' for team '{team_name}': {e}. Skipping this tool.")
                    continue

            if team_configured.think:
                team_tools.append(ReasoningTools(add_instructions=True))

            team_model = await self._get_model(team_configured.model, team_configured.model_id, team_configured.apikey)

            team_kwargs = {
                "name": team_name,
                "model": team_model,
                "members": members,
                "tools": team_tools,
                "instructions": dedent(team_configured.instructions),
                "enable_team_history": True,
                "show_tool_calls": True,
                "markdown": True,
                "show_members_responses": True,
                "add_datetime_to_instructions": True,
                "num_history_runs": team_configured.num_history_runs,
                "add_history_to_messages": True,
                "success_criteria": team_configured.success_criteria,
                "enable_agentic_memory": False,
                "enable_user_memories": False,
                "read_team_history": False
            }

            if self._memory_enabled:
                team_kwargs["session_id"] = self.session_id
                team_kwargs["user_id"] = self.user_id
                team_kwargs["memory"] = self._user_memories
                team_kwargs["storage"] = self._team_storage
                team_kwargs["enable_agentic_memory"] = True
                team_kwargs["enable_user_memories"] = True
                team_kwargs["read_team_history"] = True

            team = Team(**team_kwargs)
            self._builded_teams[team_name] = team
            if self.logging:
                self.logger.info(f"Team '{team_name}' built successfully.")
            return team
        except Exception as e:
            self.logger.error(f"Error building team '{team_name}': {e}")
            raise 

    async def Build_All_Agents(self):
        for agent_name in list(self.agents_configured.keys()):
            try:
                await self.Build_agent(agent_name)
            except Exception as e:
                self.logger.error(f"Failed to build agent '{agent_name}': {e}. Continuing with other agents.")

    async def Build_All_Teams(self):
        for team_name in list(self.teams_configured.keys()): 
            try:
                await self.Build_Team(team_name)
            except Exception as e:
                self.logger.error(f"Failed to build team '{team_name}': {e}. Continuing with other teams.")
