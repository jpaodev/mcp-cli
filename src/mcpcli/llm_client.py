import logging
import os
import uuid
from typing import Any, Dict, List
import json

import ollama
from dotenv import load_dotenv
from openai import OpenAI, AzureOpenAI
from anthropic import Anthropic
from litellm import completion
# Load environment variables
load_dotenv()


class LLMClient:
    def __init__(self, provider="openai", model="gpt-4o-mini", api_key=None):
        # set the provider, model and api key
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = None
        
        # ensure we have the api key for openai if set
        if provider == "openai":
            self.api_key = self.api_key or os.getenv("OPENAI_API_KEY")
            if not self.api_key:
                raise ValueError("The OPENAI_API_KEY environment variable is not set.")
        # check anthropic api key
        elif provider == "anthropic":
            self.api_key = self.api_key or os.getenv("ANTHROPIC_API_KEY")
            if not self.api_key:
                raise ValueError(
                    "The ANTHROPIC_API_KEY environment variable is not set."
                )
        # check ollama is good
        elif provider == "ollama" and not hasattr(ollama, "chat"):
            raise ValueError("Ollama is not properly configured in this environment.")

        elif provider == "deepseek":
            self.base_url = "https://api.deepseek.com/v1"
            self.api_key = self.api_key or os.getenv("DEEPSEEK_API_KEY")
            if not self.api_key:
                raise ValueError(
                    "The DEEPSEEK_API_KEY environment variable is not set."
                )
        elif provider == "azure":
            self.base_url = os.getenv("AZURE_OPENAI_ENDPOINT")
            self.api_key = self.api_key or os.getenv("AZURE_OPENAI_API_KEY")
            self.model = self.model or os.getenv("AZURE_DEPLOYMENT_NAME")
            if not all([self.api_key, self.base_url, self.model]):
                raise ValueError(
                    "Please set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY environment variables."
                )
        elif provider == "litellm":
            pass
        
    def create_completion(
        self, messages: List[Dict], tools: List = None
    ) -> Dict[str, Any]:
        """Create a chat completion using the specified LLM provider."""
        if self.provider in ["openai", "deepseek", "azure"]:
            # perform an openai completion
            return self._openai_completion(messages, tools)
        elif self.provider == "litellm":
            return self._litellm_completion(messages, tools)
        elif self.provider == "anthropic":
            # perform an anthropic completion
            return self._anthropic_completion(messages, tools)
        elif self.provider == "ollama":
            # perform an ollama completion
            return self._ollama_completion(messages, tools)
        else:
            # unsupported providers
            raise ValueError(f"Unsupported provider: {self.provider}")

    def _litellm_completion(self, messages: List[Dict], tools: List) -> Dict[str, Any]:
        """Handle LiteLLM chat completions."""
        # litellm completion request
        response = completion(
            model=self.model,
            messages=messages,
            tools=tools or []
        )
        # return the response
        return {
            "response": response.choices[0].message.content,
            "tool_calls": getattr(response.choices[0].message, "tool_calls", []),
        }
            
    def _openai_completion(self, messages: List[Dict], tools: List) -> Dict[str, Any]:
        """Handle OpenAI chat completions."""
        # get the openai client
        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        if self.provider == "azure":
            client = AzureOpenAI(
                azure_endpoint=self.base_url,
                api_key=self.api_key,
                api_version=os.getenv("OPENAI_API_VERSION"),
                azure_deployment=self.model,
                
            )
        try:
            # make a request, passing in tools
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools or [],
            )

            # return the response
            return {
                "response": response.choices[0].message.content,
                "tool_calls": getattr(response.choices[0].message, "tool_calls", []),
            }
        except Exception as e:
            # error
            logging.error(f"OpenAI API Error: {str(e)}")
            raise ValueError(f"OpenAI API Error: {str(e)}")

    def _anthropic_completion(self, messages: List[Dict], tools: List) -> Dict[str, Any]:
        """Handle Anthropic chat completions."""
        # get the anthropic client
        client = Anthropic(api_key=self.api_key)

        try:
            # format messages for anthropic api
            anthropic_messages = []
            system_messages = []
            for msg in messages:
                if msg["role"] == "system":
                    system_messages.append({
                        "type": "text",
                        "text": msg["content"]
                    })
                elif msg["role"] == "tool":
                    anthropic_messages.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": msg["tool_call_id"],
                            "content": msg["content"]
                        }]
                    })
                elif msg["role"] == "assistant" and "tool_calls" in msg:
                    content = []
                    if msg["content"]:
                        content.append({
                            "type": "text",
                            "content": msg["content"]
                        })

                    for tool_call in msg["tool_calls"]:
                        content.append({
                            "type": "tool_use",
                            "id": tool_call["id"],
                            "name": tool_call["function"]["name"],
                            "input":(
                                json.loads(tool_call["function"]["arguments"])
                                if isinstance(tool_call["function"]["arguments"], str)
                                else tool_call["function"]["arguments"]
                            )
                        })

                    anthropic_messages.append({
                        "role": msg["role"],
                        "content": content
                    })
                else:
                    anthropic_messages.append({
                        "role": msg["role"],
                        "content": [{
                            "type": "text",
                            "text": msg["content"]
                        }]
                    })

            # add prompt caching markers
            if len(system_messages) > 0:
                system_messages[-1]["cache_control"] = {"type": "ephemeral"}
            if len(anthropic_messages) > 0:
                anthropic_messages[-1]["content"][-1]["cache_control"] = {"type": "ephemeral"}
            if len(anthropic_messages) > 2:
                anthropic_messages[-3]["content"][-1]["cache_control"] = {"type": "ephemeral"}

            # format tools for anthropic api
            if tools:
                anthropic_tools = []
                for tool in tools:
                    anthropic_tools.append({
                        "name": tool["function"]["name"],
                        "description": tool["function"]["description"],
                        "input_schema": tool["function"]["parameters"]
                    })
                # add prompt caching marker
                if len(anthropic_tools) > 0:
                    anthropic_tools[-1]["cache_control"] = {"type": "ephemeral"}
            else:
                anthropic_tools = None

            # make a reuest, passing in tools
            response = client.messages.create(
                model=self.model,
                system=system_messages,
                tools=anthropic_tools,
                messages=anthropic_messages,
                max_tokens=8192
            )

            # format tool calls
            tool_calls = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_calls.append({
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": block.input
                        }
                    })

            # return the response
            return {
                "response": response.content[0].text if response.content[0].type == "text" else "",
                "tool_calls": tool_calls
            }
        except Exception as e:
            # error
            raise ValueError(f"Anthropic API Error: {repr(e)}")

    def _ollama_completion(self, messages: List[Dict], tools: List) -> Dict[str, Any]:
        """Handle Ollama chat completions."""
        # Format messages for Ollama
        ollama_messages = [
            {"role": msg["role"], "content": msg["content"]} for msg in messages
        ]

        try:
            # Make API call with tools
            response = ollama.chat(
                model=self.model,
                messages=ollama_messages,
                stream=False,
                tools=tools or [],
            )

            logging.info(f"Ollama raw response: {response}")

            # Extract the message and tool calls
            message = response.message
            tool_calls = []

            # Convert Ollama tool calls to OpenAI format
            if hasattr(message, "tool_calls") and message.tool_calls:
                for tool in message.tool_calls:
                    tool_calls.append(
                        {
                            "id": str(uuid.uuid4()),  # Generate unique ID
                            "type": "function",
                            "function": {
                                "name": tool.function.name,
                                "arguments": tool.function.arguments,
                            },
                        }
                    )

            return {
                "response": message.content if message else "No response",
                "tool_calls": tool_calls,
            }

        except Exception as e:
            # error
            logging.error(f"Ollama API Error: {str(e)}")
            raise ValueError(f"Ollama API Error: {str(e)}")
