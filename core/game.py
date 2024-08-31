import random
import time

import streamlit as st
from typing import List, Optional, Dict, Any
from openai import OpenAI


class Interface:
    def request_action(self, options: List, request: str, default_option: Optional[int] = None) -> Optional[str]:
        pass

    def display_text(self, text):
        pass

    def display_expander(self, label, body):
        pass

    def to_dict(self):
        return type(self).__name__

    @classmethod
    def from_dict(cls, d):
        if d["__name__"] == "ConsoleInterface":
            return ConsoleInterface()
        # elif d["__name__"] == "StreamlitInterface":
        #     return StreamlitInterface()
        elif d["__name__"] == "Interface":
            return Interface()
        # elif d["__name__"] == "FlaskInterface":
        #     return FlaskInterface()

    @staticmethod
    def display_game(g):
        pass


class ConsoleInterface(Interface):
    def request_action(self, options: List, request: str, default_option: Optional[int] = None) -> Optional[str]:
        print(options)
        return input(request)

    def display_text(self, text):
        print(text)

    def display_expander(self, label: str, body: Any):
        self.display_text(body)


class StreamlitInterface(Interface):
    def request_action(self, options: List[str], request: str, default_option: Optional[int] = None) -> Optional[str]:
        placeholder = st.empty()
        random_key = random.randint(0, 10000)
        if "selected_option" not in st.session_state:
            st.session_state.selected_option = options[0]
        if st.session_state.selected_option in options:
            default_option = options.index(st.session_state.selected_option)
        else:
            default_option = None
        selected_option = placeholder.selectbox(key=f"selectbox_{random_key}",
                                                label=request,
                                                options=options,
                                                index=default_option)

        if st.button(label="Confirm", key=f"button_{random_key}"):
            player_action = st.session_state.selected_option
            del st.session_state["selected_option"]
            return player_action
        else:
            st.session_state.selected_option = selected_option
            st.stop()

        # if st.session_state.confirmed:
        #     player_action = st.session_state.selected_option
        #     del st.session_state["selected_option"]
        #     return player_action

    def display_text(self, text):
        st.text(body=text)

    def display_expander(self, label: str, body: Any):
        with st.expander(label=label):
            st.write(body)


class Player:
    name: str

    def __init__(self, name: str):
        self.name = name

    def __str__(self):
        return self.name


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
        self.max_memory_length = 10
        self.memory = memory
        self.ai_temp = ai_temp
        self.model = ai_model
        self.system_message = system_message

    @property
    def memory_length(self):
        return len(self.memory)

    # TODO: abstract to a memory class
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

    def get_response(self, messages):
        # print(messages)
        response = self.client.chat.completions.create(
            model=self.model,
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
            model=self.model,
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


class Game:
    players: List['Player']
    interface: Interface

    def __init__(self, players: List['Player'], interface: Interface = None):
        if interface is None:
            self.interface = ConsoleInterface()

        self.players = players
        self.interface = interface

    def request_action(self, options, request=None):
        return self.interface.request_action(options, request)

    def display_text(self, text):
        self.interface.display_text(text)
