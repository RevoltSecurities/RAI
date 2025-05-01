from dataclasses import dataclass
from typing import Optional, Any

from agno.models.anthropic import Claude
from agno.models.aws import AwsBedrock
from agno.models.azure import AzureOpenAI
from agno.models.cohere import Cohere
from agno.models.deepinfra import DeepInfra
from agno.models.deepseek import DeepSeek
from agno.models.fireworks import Fireworks
from agno.models.google import Gemini
from agno.models.groq import Groq
from agno.models.huggingface import HuggingFace
from agno.models.ibm import WatsonX
from agno.models.internlm import InternLM
from agno.models.litellm import LiteLLMOpenAI
from agno.models.lmstudio import LMStudio
from agno.models.mistral import MistralChat
from agno.models.nvidia import Nvidia
from agno.models.ollama import Ollama
from agno.models.openai import OpenAIChat
from agno.models.openrouter import OpenRouter
from agno.models.perplexity import Perplexity
from agno.models.sambanova import Sambanova
from agno.models.together import Together
from agno.models.xai import xAI



@dataclass
class ModelConfig:
    provider: str
    modelid: str
    apikey: Optional[str] = "sk-ant-api03-1234567890"


class ModelBuilder:

    Providers = {
        "anthropic": Claude,
        "aws": AwsBedrock,
        "azure": AzureOpenAI,
        "cohere": Cohere,
        "deepinfra": DeepInfra,
        "deepseek": DeepSeek,
        "fireworks": Fireworks,
        "gemini": Gemini,
        "groq": Groq,
        "huggingface": HuggingFace,
        "ibm": WatsonX,
        "internlm": InternLM,
        "litllm": LiteLLMOpenAI,
        "lmstudio": LMStudio,
        "mistral": MistralChat,
        "nvidia": Nvidia,
        "ollama": Ollama,
        "openai": OpenAIChat,
        "openrouter": OpenRouter,
        "perplexity": Perplexity,
        "sambanova": Sambanova,
        "together": Together,
        "xai": xAI
    }

    @classmethod
    def _get_provider(cls, provider: str) -> Any:
        provider = cls.Providers.get(provider.lower())
        if not provider:
            raise ValueError(f"LLM Provider {provider} not found")
        return provider

    @classmethod
    def build(cls, config: ModelConfig) -> Any:
        LllModel = cls._get_provider(config.provider)
        return LllModel(id=config.modelid,api_key=config.apikey)
