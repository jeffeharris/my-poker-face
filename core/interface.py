from typing import List, Optional, Any
import random

import streamlit as st


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
        elif d["__name__"] == "StreamlitInterface":
            return StreamlitInterface()
        elif d["__name__"] == "Interface":
            return Interface()
        raise TypeError("Expected an Interface object, but got: " + d["__name__"])

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


