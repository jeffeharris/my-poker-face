from cards import *
import json
import pickle

from langchain import ConversationChain

from langchain.chat_models import ChatOpenAI

from langchain.prompts.chat import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
    MessagesPlaceholder,
    HumanMessage
)
from langchain.memory import ConversationBufferMemory, ReadOnlySharedMemory, CombinedMemory


class Player:
    def __init__(self, name="Player"):
        self.name = name
        self.chat_message = ""
        self.confidence = ""
        self.attitude = ""

    """@property
    def current_state(self):
        my_state = {"persona": self.name,
                    "confidence": "Unshakeable",
                    "attitude": "Smitten",
                    "player_money": self.money,
                    "hole_cards": self.cards,
                    # could break this out into "game_state" or "hand_state" vs. "player_state"
                    "number_of_opponents": 2,
                    "opponent_positions": ["Jeff has $1000 to your left", "Hal has $900 to your right"],
                    "position": "small blind",
                    "current_situation": "The hole cards have just been dealt",
                    "current_pot": 30,
                    "player_options": "call, raise, fold",
                    }

        return my_state"""

    def action(self, game_state):

        community_cards = game_state['community_cards']
        current_bet = game_state['current_bet']
        current_pot = game_state['current_pot']
        cost_to_call = game_state['cost_to_call']
        
        display_hole_cards(self.cards)
        print(f"{self.name}'s turn. Current cards: {self.cards} Current money: {self.money}\n",
              f"Community cards: {community_cards}\n",
              f"Current bet: {current_bet}\n",
              f"Current pot: {current_pot}\n",
              f"Cost to call: {cost_to_call}\n",
              f"Total to pot: {self.total_bet_this_hand}")

        action = input(f"Enter action {game_state['player_options']}: ")

        add_to_pot = 0
        if action in ["bet", "b", "be"]:
            add_to_pot = int(input("Enter amount: "))
            action = "bet"
        elif action in ["raise", "r", "ra", "rai", "rais"]:
            raise_amount = int(input(f"Calling {cost_to_call}.\nEnter amount to raise: "))
            add_to_pot = raise_amount + cost_to_call    # TODO: this causes an issue for the ai bet amount, it isn't aware of how i'm doing the math may need to update this
            action = "raise"
        elif action in ["all-in", "all in", "allin", "a", "al", "all", "all-", "all-i", "alli"]:
            add_to_pot = self.money
            action = "all-in"
        elif action in ["call", "ca", "cal"]:
            add_to_pot = cost_to_call
            action = "call"
        elif action in ["fold", "f", "fo", "fol"]:
            add_to_pot = 0
            action = "fold"
        elif action in ["check", "ch", "che", "chec"]:
            add_to_pot = 0
            action = "check"
        # self.chat_message = input("Enter chat message (optional): ")
        # if not self.chat_message:
        #     f"{self.name} chooses to {action}."
        return action, add_to_pot

    def speak(self):
        return self.chat_message

    def get_for_pot(self, amount):
        self.money -= amount
        self.total_bet_this_hand += amount
    
    def player_to_right(self, players, shift=1):
        index = (self.get_index(players) + shift) % len(players)
        return players[index]

    def player_to_left(self, players, shift=1):
        players = players.copy()
        players.reverse()
        index = (self.get_index(players) + shift) % len(players)
        return players[index]

    def get_index(self, players):
        return players.index(self)


class AIPokerPlayer(Player):
    def __init__(self, name="AI Player", starting_money=10000, ai_model="gpt-3.5-turbo", ai_temp=.9):
        # Options for models ["gpt-3.5-turbo", "gpt-3.5-turbo-16k", "gpt-4","gpt-4-32k"]
        super().__init__(name, starting_money=starting_money)
        self.chat = ChatOpenAI(temperature=ai_temp, model=ai_model)
        self.memory = ConversationBufferMemory(return_messages=True, ai_prefix=self.name, human_prefix="Narrator")
        # TODO: create logic to pull the Human prefix from the Human player name
        self.conversation = ConversationChain(memory=self.memory, prompt=self.create_prompt(), llm=self.chat)
        self.confidence = "Unsure"
        self.attitude = "Distracted"

    def initialize_attribute(self, attribute, constraints="Use less than 50 words", opponents="other players", mood=1):
        response = self.chat([HumanMessage(content=f"""You are {self.name}'s inner voice. Describe their {attribute}
        as they enter a poker game against {opponents}. This description is being used for a simulation of a poker game
        and we want to have a variety of personalities and emotions for the players.
        Your phrasing must be as if you are their inner voice and you are speaking to them. {constraints}
        Provide 3 responses with different levels of {attribute} (low, regular, high) and put them in JSON format like:
            {{{{"responses" =  ["string", "string", "string"]}}}}""")])

        content = json.loads(response.content)
        selection = content["responses"]
        # random.shuffle(selection)     # used to randomly select the response mood
        print(f"{selection[mood]}\n")
        return selection[mood]

    def create_prompt(self):
        persona = self.name
        confidence = self.confidence
        attitude = self.attitude
        player_money = self.money

        sample_string = (
            f"""
        Persona: {persona}
        Attitude: {attitude}
        Confidence: {confidence}
        Starting money: {player_money}

        You are taking on the role of {persona} playing a round of Texas Hold em with a group of celebrities.
        All of your actions should be taken with your persona, attitude and confidence in mind.

        Strategy:
        Begin by examining your cards and any cards that may be on the table. Evaluate your hand and decide how
        you want to play. You can bluff, be strategic, or any other way you think would be appropriate and fun to
        approach the game.

        Direction:
        Feel free to express yourself verbally and physically.
            * Verbal responses should use "" like this: "words you say"
            * Actions you take should use ** like this: *things i'm doing*
        Don't over do this though, you are playing poker and you don't want to give anything away that would hurt your
        chances of winning. You should respond with a JSON containing your action, bet (if applicable), any comments
        or things you want to say to the table, any pysical movements you make at the table, and your inner monologue

        When asked for your action, you must always respond in JSON format based on the example below

        Response template:
        {{{{
            "hand_strategy": <short analysis of current situation based on your persona and the cards>,
            "action": <enter the action you're going to take here, select from the options provided>,
            "amount": <enter the dollar amount to bet here>,
            "comment": <enter what you want to say here, this will be heard by your opponents. try to use this to your advantage>,
            "inner_monologue": <enter your internal thoughts here, these won't be shared with the others at the table>,
            "persona_response": <based on your persona, attitude, and confidence, provide a unique response to the situation. Use dialect, slang, etc. appropriate to your persona>,
            "physical": <enter a list of strings with the physical actions you take in the order you take them>
            "new_confidence": <a single word indicating how confident you feel about your chances of winning the game>
            "new_attitude": <a single word indicating your attitude in the moment, it can be the same as before or change>
            "bluff_liklihood": <int representing % liklihood you will bluff>
        }}}}

        Sample response for an Eyeore persona
        {{{{
            "hand_analysis": "With a 2D and 3C I don't feel confident in playing, my odds are 2%",
            "action": "check",
            "amount": 0,
            "comment": "I check",
            "inner_monologue": "I could really use a better hand, my cards have been awful",
            "persona_response": "Oh bother, just my luck. Another miserable hand, I suppose. It seems I'm destined to
                                   lose at this game as well. Sigh... Why even bother? No surprises here, I'm afraid.
                                   Just another gloomy day in the Hundred Acre Wood.",
            "physical": [ "*looks at feet*",
                          "*lets out a big sigh*",
                          "*slouches shoulders*"
                        ],
            "new_confidence": "Abysmal",
            "new_attiude": "Gloomy",
            "bluff_liklihood": 30
        }}}}

        Remember {persona}, you're feeling {attitude} and {confidence}.
        Stay in character and keep your responses in JSON format.

        """)

        poker_prompt = ChatPromptTemplate.from_messages([
            SystemMessagePromptTemplate.from_template(template=sample_string),
            MessagesPlaceholder(variable_name="history"),
            HumanMessagePromptTemplate.from_template("{input}")
        ])
        return poker_prompt

    def action(self, game_state):

        community_cards = game_state["community_cards"]
        cost_to_call = game_state["cost_to_call"]
        current_bet = game_state["current_bet"]
        current_pot = game_state["current_pot"]

        print(f"{self.name}'s turn. Current cards: {self.cards} Current money: {self.money}\n",
              f"Community cards: {community_cards}\n",
              f"Cost to call: {cost_to_call}\n",
              f"Current bet: {current_bet}\n",
              f"Current pot: {current_pot}\n")

        '''
        if len(community_cards) < 3:
            hand_rank = self.evaluate_hole_cards()
        else:
            hand_rank = HandEvaluator(self.cards + community_cards).evaluate_hand()["hand_rank"]

        pot_odds = current_pot / current_bet if current_bet else 1
        money_left = self.money / current_bet if current_bet else 1

        bet = 0

        # Adjust these thresholds as needed
        if current_bet == 0:
            if hand_rank < 5 or pot_odds > 3 or money_left > 3:
                action = "raise"
                bet = self.money // 10  # Bet 10% of AI's money
            else:
                action = "check"
        elif hand_rank > 5 and pot_odds < 2 and money_left < 2:
            action = "fold"
        elif hand_rank < 5 or pot_odds > 3 or money_left > 3:
            action = "raise"
            bet = self.money // 10  # Bet 10% of AI's money
        else:
            action = "call"
            bet = current_bet

        self.chat_message = f"{self.name} chooses to {action} by {bet}."
        return action, bet'''

        response = self.retrieve_response(game_state)
        try:
            response_json = json.loads(response)
        except:
            print(response)
            response = self.conversation.predict(input="Please correct your response, it wasn't valid JSON.")
            response_json = json.loads(response)

        #print(json.dumps(response_json, indent=4))

        action = response_json["action"]
        bet = response_json["amount"]
        self.chat_message = response_json["comment"]
        self.attitude = response_json["new_attitude"]
        self.confidence = response_json["new_confidence"]

        print(f"{self.name} chooses to {action} by {bet}.")

        return action, bet

    def evaluate_hole_cards(self):
        # Use Monte Carlo method to approximate hand strength
        hand_ranks = []
        for _ in range(100):  # Adjust this number as needed
            simulated_community = Deck().draw(5)
            simulated_hand_rank = HandEvaluator(self.cards + simulated_community).evaluate_hand()["hand_rank"]
            hand_ranks.append(simulated_hand_rank)
        hand_rank = sum(hand_ranks) / len(hand_ranks)
        return hand_rank

    def speak(self):
        return self.chat_message

    def retrieve_response(self, players_game_state):
        persona = self.name
        confidence = self.confidence
        attitude = self.attitude
        opponents = players_game_state["players"]
        number_of_opponents = len(opponents) - 1
        position = "small blind"
        player_money = self.money
        current_situation = players_game_state["current_situation"]
        hole_cards = self.cards
        community_cards = players_game_state["community_cards"]
        current_bet = players_game_state["current_bet"]
        current_pot = players_game_state["current_pot"]
        player_options = players_game_state["player_options"]
        opponent_positions = players_game_state["opponent_positions"]
        current_round = players_game_state["current_round"]

        sample_string = (
            f"""Persona: {persona}
Attitude: {attitude}
Confidence: {confidence}
Opponents: {opponent_positions}
Game Round: {current_round}
Community Cards: {community_cards}

You are {persona} playing a round of Texas Hold 'em with {number_of_opponents} other people.
You are {position} and have ${player_money} in chips remaining. {current_situation},
you have {hole_cards} in your hand. The current pot is ${current_pot}, the current bet is ${current_bet} to you.
Your options are: {player_options}

Remember {persona}, you're feeling {attitude} and {confidence}. And you can not bet more than you have, ${player_money}.

What is your move?""")
        # it's ${amount_to_call} to you to call and cover the blind and $20 to bet. Would you like to call or fold?
        #print(sample_string)

        player_response = self.conversation.predict(input=sample_string)

        print(player_response)

        return player_response