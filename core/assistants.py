from typing import List, Dict, Optional
import os

from openai import OpenAI

# Import config for AI settings
try:
    from poker.config import AI_MAX_MEMORY_LENGTH
except ImportError:
    AI_MAX_MEMORY_LENGTH = 15  # Default fallback


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
        """
        A client for interacting with the OpenAI API directly. This client allows setting
        various configurations such as temperature, model, and system messages, as well
        as storing a memory of past interactions.

        :param ai_temp: Temperature setting for the AI model, controlling the randomness of responses.
        :type ai_temp: float
        :param ai_model: The AI model to use for generating responses.
        :type ai_model: str or None
        :param system_message: An optional system message that can guide the AI's behavior.
        :type system_message: str or None
        :param memory: Optional memory to retain across interactions.
        :type memory: list or None
        """
        # create a class that defines the client using OpenAI API directly
        self.max_memory_length = AI_MAX_MEMORY_LENGTH
        self.memory = memory
        self.ai_temp = ai_temp
        self.ai_model = ai_model
        self.system_message = system_message

    @property
    def memory_length(self):
        """
            Get the length of the memory.

            :return: The length of the memory.
            :rtype: int
        """
        return len(self.memory)

    def trim_memory(self):
        """
        Trim the memory to keep it within the allowed maximum length.

        If the memory length exceeds the maximum allowed length, this method
        will trim the memory list to ensure it conforms to the maximum limit.

        :raises AttributeError: If `self.memory_length` or `self.max_memory_length` does not exist.
        """
        if self.memory_length > self.max_memory_length:
            self.memory = self.memory[-self.max_memory_length:]

    @property
    def messages(self):
        """
        Retrieve the current state of messages, including system message and memory.

        The function initializes the messages with the system message, trims the memory to ensure it does not exceed predefined constraints, and then extends the messages list with the current memory content.

        :return: The current list of messages containing roles and contents.
        :rtype: list
        """
        # initialize memory
        messages = [{"role": "system", "content": self.system_message}]
        self.trim_memory()
        messages.extend(self.memory)
        return messages

    def add_to_memory(self, message: Dict[str, str]):
        """
        Add a message to the memory and trim memory if necessary.

        :param message: The message to add to the memory.
        :type message: Dict[str, str]
        """
        self.memory.append(message)
        self.trim_memory()

    def get_response(self, prompt):
        """
        Generate a response based on the given prompt.

        :param prompt: The input string to which the response will be generated.
        :type prompt: str
        :return: A response string that echoes the input prompt.
        :rtype: str
        """
        response = "you said: " + prompt
        return response


class OpenAILLMAssistant(LLMAssistant):
    client: OpenAI
    functions: List[dict] or None

    def __init__(self,
                 ai_model="gpt-5-mini",      # "gpt-3.5-turbo-0125"     # gpt-3.5-turbo-16k
                 ai_temp=1.0,
                 system_message="You are a helpful assistant.",
                 memory=None,
                 functions: list = None):
        """
        Initialize the OpenAILLMAssistant instance.

        :param ai_model: The model of the AI to use. Default is "gpt-5-mini".
        :type ai_model: str
        :param ai_temp: The temperature setting for the model. Default is 1.0.
        :type ai_temp: float
        :param system_message: The initial system message for the assistant. Default is "You are a helpful assistant.".
        :type system_message: str
        :param memory: Memory to initialize the assistant with. If None, initializes with an empty list.
        :type memory: list or None
        :param functions: List of additional functions for the assistant. Default is None.
        :type functions: list or None
        """
        super().__init__(ai_temp, ai_model, system_message, memory)
        if memory is None:
            self.memory = []
        # Initialize OpenAI client with just the API key from environment
        self.client = OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY")
        )
        self.functions = functions

    def get_response(self, messages: List[Dict[str, str]]):
        """
        Get a response from the AI model based on the input messages.

        :param messages: A list of message dictionaries, each containing the content and metadata of a message.
        :type messages: List[Dict[str, str]]
        :return: The response generated by the AI model.
        :rtype: dict
        """
        # Build kwargs - GPT-5 models don't support temperature parameter
        kwargs = {
            "model": self.ai_model,
            "messages": messages,
            "max_completion_tokens": 1500,
        }
        if not self.ai_model.startswith("gpt-5"):
            kwargs["temperature"] = self.ai_temp
            kwargs["top_p"] = 1
            kwargs["frequency_penalty"] = 0
            kwargs["presence_penalty"] = 0

        response = self.client.chat.completions.create(**kwargs)
        return response

    def get_json_response(self, messages: List[Dict[str, str]]):
        """
        Generate a JSON response based on provided messages by interacting with the AI model.

        :param messages: A list of dictionaries, each containing the input string for the AI model.
        :type messages: List[Dict[str, str]]
        :return: The JSON response generated by the AI model.
        :rtype: dict
        """
        # Build kwargs - GPT-5 models don't support temperature parameter
        kwargs = {
            "model": self.ai_model,
            "messages": messages,
            "max_completion_tokens": 1500,
            "response_format": {"type": "json_object"},
        }
        if not self.ai_model.startswith("gpt-5"):
            kwargs["temperature"] = self.ai_temp
            kwargs["top_p"] = 1
            kwargs["frequency_penalty"] = 0
            kwargs["presence_penalty"] = 0

        json_response = self.client.chat.completions.create(**kwargs)
        return json_response

    def chat(self, user_content, json_format: Optional[bool] = False):
        """
        Process user input and generate a response from an AI assistant.

        :param user_content: The content of the user's message.
        :type user_content: str
        :param json_format: Flag to determine if the response should be in JSON format. Defaults to False.
        :type json_format: Optional[bool]
        :return: The content of the AI assistant's response.
        :rtype: str
        """
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
        """
            Reset the memory of the instance.

            This method clears the "memory" attribute of the instance, resetting it to an empty list.
        """
        self.memory = []

    def to_dict(self):
        """
        Convert the object instance into a dictionary representation.

        :return: A dictionary representation of the object.
        :rtype: dict
        """
        return {
            "__name__": "OpenAILLMAssistant",   # TODO: <BUG> change __name__ to type if there isnt a magic property of name
            "ai_model": self.ai_model,
            "ai_temp": self.ai_temp,
            "system_message": self.system_message,
            "max_memory_length": self.max_memory_length,
            "memory": self.memory,
            "functions": self.functions
        }
