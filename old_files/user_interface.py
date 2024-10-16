from abc import ABC, abstractmethod
from typing import List, Optional


class UserInterface(ABC):
    def request_action(self, options: List, request: str, default_option: Optional[int] = None) -> Optional[str]:
        pass

    def display_text(self, text):
        pass

    def display_expander(self, label, body):
        pass

    def get_user_input(self):
        pass

    def to_dict(self):
        return type(self).__name__

    @classmethod
    def from_dict(cls, name: str):
        # name = d.get("__name__")
        if not name:
            raise TypeError("Dictionary does not contain the required '__name__' key.")

        # Retrieve the class from globals()
        subclass = globals().get(name)

        if subclass and issubclass(subclass, cls):
            return subclass()

        raise TypeError(f"Expected a valid Interface subclass, but got: {name}")