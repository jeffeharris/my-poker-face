import random

import streamlit as st
from typing import List
from openai import OpenAI


class Interface:
    def request_action(self, options: List, request: str):
        pass

    def display_text(self, text):
        pass


class ConsoleInterface(Interface):
    def request_action(self, options, request):
        print(options)
        return input(request)

    def display_text(self, text):
        print(text)


class StreamlitInterface(Interface):
    def request_action(self, options, request):
        placeholder = st.empty()
        selected_option = placeholder.selectbox(key=random.randint(0, 10000),
                                                label=request,
                                                options=options,
                                                index=None)
        # Check if user made the selection, then display confirm button
        if selected_option:
            confirm_button = st.button("Confirm")
            if confirm_button:
                return selected_option

        # return None by default if no selection is made or confirmed
        return None

    def display_text(self, text):
        st.text(body=text)


class Player:
    name: str

    def __init__(self, name: str):
        self.name = name


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
        self.temp = ai_temp
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

    def add_to_memory(self, message):
        self.memory.append(message)
        self.trim_memory()

    def get_response(self, prompt):
        response = "you said: " + prompt
        return response


class OpenAILLMAssistant(LLMAssistant):
    client: OpenAI
    functions: List[dict] or None

    def __init__(self,
                 ai_model="gpt-3.5-turbo-16k",
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
            temperature=self.temp,
            max_tokens=5000,
            top_p=1,
            frequency_penalty=0,
            presence_penalty=0
        )
        return response

    def chat(self, user_content):
        user_message = {"role": "user", "content": user_content}
        self.add_to_memory(user_message)
        response = self.get_response(self.messages)
        self.add_to_memory(response.choices[0].message)

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
