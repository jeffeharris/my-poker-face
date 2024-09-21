from typing import List, Dict, Optional

from openai import OpenAI


class LLMAssistant:
    ai_model: str
    ai_temp: float
    system_message: str
    max_memory_length: int
    memory: List[dict] or None

    def __init__(self,
                 ai_temp=1.0,
                 ai_model=None,
                 system_message=None,
                 memory=None):
        # create a class that defines the client using OpenAI API directly
        self.max_memory_length = 15
        self.memory = memory
        self.ai_temp = ai_temp
        self.ai_model = ai_model
        self.system_message = system_message

    @property
    def memory_length(self):
        return len(self.memory)

    # TODO: <FEATURE> abstract to a memory class
    def trim_memory(self):
        if self.memory_length > self.max_memory_length:
            self.memory = self.memory[-self.max_memory_length:]

    @property
    def messages(self):
        # initialize memory
        messages = [{"role": "system", "content": self.system_message}]
        self.trim_memory()
        messages.extend(self.memory)
        return messages

    def add_to_memory(self, message: Dict[str, str]):
        self.memory.append(message)
        self.trim_memory()

    def get_response(self, prompt):
        response = "you said: " + prompt
        return response


class OpenAILLMAssistant(LLMAssistant):
    client: OpenAI
    functions: List[dict] or None

    def __init__(self,
                 ai_model="gpt-4o-mini",      # "gpt-3.5-turbo-0125"     # gpt-3.5-turbo-16k
                 ai_temp=1.0,
                 system_message="You are a helpful assistant.",
                 memory=None,
                 functions: list = None):
        super().__init__(ai_temp, ai_model, system_message, memory)
        if memory is None:
            self.memory = []
        self.client = OpenAI()
        self.functions = functions

    def get_response(self, messages: List[Dict[str, str]]):
        response = self.client.chat.completions.create(
            model=self.ai_model,
            messages=messages,
            temperature=self.ai_temp,
            max_tokens=1500,
            top_p=1,
            frequency_penalty=0,
            presence_penalty=0
        )
        return response

    def get_json_response(self, messages: List[Dict[str, str]]):
        json_response = self.client.chat.completions.create(
            model=self.ai_model,
            messages=messages,
            temperature=self.ai_temp,
            max_tokens=1500,
            top_p=1,
            frequency_penalty=0,
            presence_penalty=0,
            response_format={"type": "json_object"}
        )
        return json_response

    def chat(self, user_content, json_format: Optional[bool] = False):
        user_message = {"role": "user", "content": user_content}
        self.add_to_memory(user_message)
        if json_format:
            response = self.get_json_response(self.messages)
        else:
            response = self.get_response(self.messages)

        content = response.choices[0].message.content
        ai_message = {"role": "assistant", "content": content}
        self.add_to_memory(ai_message)

        return response.choices[0].message.content

    def reset_memory(self):
        self.memory = []

    def to_dict(self):
        return {
            "__name__": "OpenAILLMAssistant",   # TODO: <BUG> change __name__ to type if there isnt a magic property of name
            "ai_model": self.ai_model,
            "ai_temp": self.ai_temp,
            "system_message": self.system_message,
            "max_memory_length": self.max_memory_length,
            "memory": self.memory,
            "functions": self.functions
        }
