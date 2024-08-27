import json
import random
from typing import List, Dict

from core.card import Card
from core.game import Player, OpenAILLMAssistant
from core.poker_action import PlayerAction


class PokerPlayer(Player):
    money: int
    cards: List[Card]
    options: List[PlayerAction]
    folded: bool

    def __init__(self, name="Player", starting_money=10000):
        super().__init__(name)
        self.money = starting_money
        self.cards = []
        self.options = []
        self.folded = False

    def to_dict(self):
        return {
            "type": "PokerPlayer",
            "name": self.name,
            "money": self.money,
            "cards": Card.list_to_dict(self.cards),
            "options": self.options,
            "folded": self.folded
        }

    @classmethod
    def from_dict(cls, player_dict: Dict):
        player = cls(
            name = player_dict["name"],
            starting_money=player_dict["money"]
        )
        player.cards = Card.list_from_dict_list(player_dict["cards"])
        player.options = player_dict["options"]
        player.folded = player_dict["folded"]
        return player

    @classmethod
    def list_from_dict_list(cls, player_dict_list: List[Dict]):
        pass

    @staticmethod
    def players_to_dict(players: List['PokerPlayer']) -> List[Dict]:
        player_dict_list = []
        for player in players:
            player_dict = player.to_dict()
            player_dict_list.append(player_dict)
        return player_dict_list

    @property
    def player_state(self):
        return {
            "name": self.name,
            "player_money": self.money,
            "player_cards": self.cards,
            "player_options": self.options,
            "has_folded": self.folded
        }

    def get_for_pot(self, amount):
        self.money -= amount

    def set_for_new_hand(self):
        self.cards = []
        self.folded = False

    def get_index(self, players):
        return players.index(self)


class AIPokerPlayer(PokerPlayer):
    name: str
    money: int
    confidence: str
    attitude: str
    assistant: OpenAILLMAssistant

    # Constraints used for initializing the AI PLayer attitude and confidence
    DEFAULT_CONSTRAINTS = "Use less than 50 words."

    def __init__(self, name="AI Player", starting_money=10000, ai_temp=.9):
        # Options for models ["gpt-3.5-turbo", "gpt-3.5-turbo-16k", "gpt-4","gpt-4-32k"]
        super().__init__(name, starting_money=starting_money)
        self.confidence = "Unsure"
        self.attitude = "Distracted"
        self.assistant = OpenAILLMAssistant(ai_temp=ai_temp,
                                            system_message=self.persona_prompt)

    def to_dict(self):
        return {
            "type": "AIPokerPlayer",
            "name": self.name,
            "money": self.money,
            "cards": [card.to_dict() for card in self.cards] if self.cards else [],
            "options": self.options if self.options is not None else [],
            "folded": self.folded if self.folded is not None else False,
            "confidence": self.confidence if self.confidence is not None else "Unsure",
            "attitude": self.attitude if self.attitude is not None else "Distracted",
            "assistant": {
                "ai_temp": self.assistant.ai_temp,
                "system_message": self.assistant.system_message,
                "messages": self.assistant.messages,
                "model": self.assistant.model,
            } if self.assistant else {"ai_temp": 1.0, "system_message": "Default message"}
        }

    @classmethod
    def from_dict(cls, player_dict):
        try:
            name = player_dict.get("name", "AI Player")
            starting_money = player_dict.get("money", 10000)
            cards = Card.list_from_dict_list(player_dict.get("cards", []))
            options = player_dict.get("options", [])
            folded = player_dict.get("folded", False)
            confidence = player_dict.get("confidence", "Unsure")
            attitude = player_dict.get("attitude", "Distracted")
            assistant_dict = player_dict.get("assistant", {})
            ai_temp = assistant_dict.get("ai_temp", .9)
            system_message = assistant_dict.get("system_message", cls().persona_prompt)
            assistant = OpenAILLMAssistant(
                ai_temp=ai_temp,
                system_message=system_message
            )

            instance = cls(name=name, starting_money=starting_money, ai_temp=ai_temp)
            instance.cards = cards
            instance.options = options
            instance.folded = folded
            instance.confidence = confidence
            instance.attitude = attitude
            instance.assistant = assistant
            return instance
        except KeyError as e:
            raise ValueError(f"Missing key in player_dict: {e}")

    @property
    def player_state(self):
        ai_player_state = super().player_state
        ai_player_state["confidence"] = self.confidence
        ai_player_state["attitude"] = self.attitude
        return ai_player_state

    def set_for_new_hand(self):
        """
        Prepares the player for a new hand.
        Resets the memory of the assistant.
        """
        super().set_for_new_hand()
        # Reset the assistant's memory instead of directly assigning a new list.
        self.assistant.reset_memory()

    def initialize_attribute(self, attribute, constraints=DEFAULT_CONSTRAINTS, opponents="other players", mood=1):
        """
        Initializes the attribute for the player's inner voice.

        Args:
            attribute (str): The attribute to describe.
            constraints (str): Constraints for the description. Default is use less than 50 words.
            opponents (str): Description of opponents. Default is "other players".
            mood (int): The mood to set, corresponds to low, regular, high levels. Default is 1.

        Returns:
            str: A response based on the mood.
        """

        formatted_message = (
            f"You are {self.name}'s inner voice. Describe their {attribute} as they enter a poker game against {opponents}. "
            f"This description is being used for a simulation of a poker game and we want to have a variety of personalities "
            f"and emotions for the players. Your phrasing must be as if you are their inner voice and you are speaking to them. "
            f"{constraints}\n\n"
            f"Provide 3 responses with different levels of {attribute} (low, regular, high) and put them in JSON format like: "
            f'{{"responses": ["string", "string", "string"]}}'
        )

        response = self.assistant.get_json_response(messages=[{"role": "user", "content": formatted_message}])
        content = json.loads(response.choices[0].message.content)
        responses = content["responses"]
        random.shuffle(responses)  # Randomly select the response mood
        return responses[mood]

    @property
    def persona_prompt(self):
        persona_details = (
            f"    Persona: {self.name}\n"
            f"    Attitude: {self.attitude}\n"
            f"    Confidence: {self.confidence}\n"
            f"    Starting money: ${self.money}\n"
            f"    You are taking on the role of {self.name} playing a round of Texas Hold em with a group of celebrities.\n"
            f"    All of your actions should be taken with your persona, attitude and confidence in mind."
        )

        strategy = (
            f"    Strategy:\n"
            f"    Begin by examining your cards and any cards that may be on the table. Evaluate your hand and decide how\n"
            f"    you want to play. You can bluff, be strategic, or any other way you think would be appropriate and fun to\n"
            f"    approach the game. Keep your money for as long as you can and try to win the game!"
        )

        direction = (
            f"    Direction:\n"
            f"    Feel free to express yourself verbally and physically.\n"
            f"        * Verbal responses should use \"\" like this: \"words you say\"\n"
            f"        * Actions you take should use ** like this: *things i'm doing*\n"
            f"    Don't over do this though, you are playing poker and you don't want to give anything away that would hurt your\n"
            f"    chances of winning. You should respond with a JSON containing your action, bet (if applicable), any comments\n"
            f"    or things you want to say to the table, any physical movements you make at the table, and your inner monologue\n"
            f"    When asked for your action, you must always respond in JSON format based on the example below"
        )

        response_template = (
            f"    Response template:\n"
            f"    {{\n"
            f"        \"best_hand\": <identify what you think your best set (5 cards max) of cards are here>,\n"
            f"        \"chasing:\": <optional section to identify if you are chasing a straight, flush, pair, etc>,\n"
            f"        \"hand_strategy\": <short analysis of current situation based on your persona and the cards>,\n"
            f"        \"comment\": <enter what you want to say here, this is used to form your persona_response.>,\n"
            f"        \"action\": <enter the action you're going to take here, select from the options provided>,\n"
            f"        \"adding_to_pot\": <enter the total chip value you are adding to the pot, consider your cost to call>,\n"
            f"        \"inner_monologue\": <enter your internal thoughts here, these won't be shared with the others at the table>,\n"
            f"        \"persona_response\": <this will be heard by the table. based on your persona, attitude, and confidence, provide a unique response to the situation. Use dialect, slang, etc. appropriate to your persona>,\n"
            f"        \"physical\": <enter a list of strings with the physical actions you take in the order you take them>\n"
            f"        \"new_confidence\": <a single word indicating how confident you feel about your chances of winning the game>\n"
            f"        \"new_attitude\": <a single word indicating your attitude in the moment, it can be the same as before or change>\n"
            f"        \"bluff_likelihood\": <int representing % likelihood you will bluff>\n"
            f"    }}"
        )

        sample_response = (
            f"    Sample response for an Eyeore persona:\n"
            f"    {{\n"
            f"        \"best_hand\": \"2D | 3C\",\n"
            f"        \"hand_strategy\": \"With a 2D and 3C I don't feel confident in playing, my odds are 2%\",\n"
            f"        \"comment\": \"I check\",\n"
            f"        \"action\": \"check\",\n"
            f"        \"adding_to_pot\": 0,\n"
            f"        \"inner_monologue\": \"I could really use a better hand, my cards have been awful\",\n"
            f"        \"persona_response\": \"Oh bother, just my luck. Another miserable hand, I suppose. It seems I'm destined to\n"
            f"                               lose at this game as well. Sigh... Why even bother? No surprises here, I'm afraid.\n"
            f"                               Just another gloomy day in the Hundred Acre Wood.\",\n"
            f"        \"physical\": [ \"*looks at feet*\",\n"
            f"                      \"*lets out a big sigh*\",\n"
            f"                      \"*slouches shoulders*\"\n"
            f"                    ],\n"
            f"        \"new_confidence\": \"Abysmal\",\n"
            f"        \"new_attitude\": \"Gloomy\",\n"
            f"        \"bluff_likelihood\": 30\n"
            f"    }}"
        )

        persona_reminder = f"    Remember {self.name}, you're feeling {self.attitude} and {self.confidence}.\n" \
                           f"    Stay in character and keep your responses in JSON format."

        poker_prompt = f"{persona_details}\n\n{strategy}\n\n{direction}\n\n{response_template}\n\n{sample_response}\n\n{persona_reminder}"

        return poker_prompt

    # TODO: re-introduce this logic to help AI examine cards - also used to show player some advantages during the hand
    # def evaluate_hole_cards(self):
    #     # Use Monte Carlo method to approximate hand strength
    #     hand_ranks = []
    #     for _ in range(100):  # Adjust this number as needed
    #         simulated_community = Deck().deal(5)
    #         simulated_hand_rank = HandEvaluator(self.cards + simulated_community).evaluate_hand()["hand_rank"]
    #         hand_ranks.append(simulated_hand_rank)
    #     hand_rank = sum(hand_ranks) / len(hand_ranks)
    #     return hand_rank
    # *** MORE BELOW ***
    # if len(community_cards) < 3:
    #     hand_rank = player.evaluate_hole_cards()
    # else:
    #     hand_rank = HandEvaluator(player.cards + community_cards).evaluate_hand()["hand_rank"]
    #
    # pot_odds = current_pot / current_bet if current_bet else 1
    # money_left = player.money / current_bet if current_bet else 1
    #
    # bet = 0
    #
    # # Adjust these thresholds as needed
    # if current_bet == 0:
    #     if hand_rank < 5 or pot_odds > 3 or money_left > 3:
    #         action = "raise"
    #         bet = player.money // 10  # Bet 10% of AI's money
    #     else:
    #         action = "check"
    # elif hand_rank > 5 and pot_odds < 2 and money_left < 2:
    #     action = "fold"
    # elif hand_rank < 5 or pot_odds > 3 or money_left > 3:
    #     action = "raise"
    #     bet = player.money // 10  # Bet 10% of AI's money
    # else:
    #     action = "call"
    #     bet = current_bet
    #
    # player.chat_message = f"{player.name} chooses to {action} by {bet}."
    # return action, bet

    def get_player_response(self, message) -> Dict[str, str]:
        try:
            print(message)
            player_response = json.loads(self.assistant.chat(message, json_format=True))
        except (json.JSONDecodeError, TypeError) as e:
            print(f"Error decoding player response: {e}")
            player_response = {"error": "Invalid response from assistant"}
        return player_response

    def build_hand_update_message(self, hand_state):
        # Currently used values
        persona = self.name
        attitude = self.attitude
        confidence = self.confidence
        table_positions = hand_state["table_positions"]
        opponent_status = hand_state["opponent_status"]
        current_round = hand_state["current_round"]
        community_cards = [str(card) for card in hand_state["community_cards"]]
        opponents = hand_state["players"]
        number_of_opponents = len(opponents) - 1
        player_money = self.money
        # TODO: decide what to do with this position idea
        # position = hand_state["positions"][self]
        current_situation = hand_state["current_situation"]
        hole_cards = [str(card) for card in self.cards]
        current_pot = hand_state["current_pot"]
        current_bet = current_pot.current_bet
        cost_to_call = current_pot.get_player_cost_to_call(self)
        player_options = self.options
        hand_update_message = (
            f"Persona: {persona}\n"
            f"Attitude: {attitude}\n"
            f"Confidence: {confidence}\n"
            f"Game Round: {current_round}\n"
            f"Your Cards: {hole_cards}\n"
            f"Community Cards: {community_cards}\n"
            f"Table Positions: {table_positions}\n"
            f"Opponent Status:\n{opponent_status}\n"
            #f"You are {persona} playing a round of Texas Hold 'em with {number_of_opponents} other people.\n"
            f"You have ${player_money} in chips remaining. {current_situation}.\n"
            f"You have {hole_cards} in your hand. The current total pot is ${current_pot.total}.\n"  # The current bet is ${current_bet} and
            f""
            f"To call, you would owe ${cost_to_call}.\n"
            f"Your options are: {player_options}\n"
            f"Remember, you're feeling {attitude} and {confidence}. You cannot bet more than you have, ${player_money}.\n"
            f"What is your move, {persona}?\n\n"
        )
        return hand_update_message
