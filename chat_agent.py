from langchain import OpenAI, ConversationChain
from dotenv import load_dotenv

from langchain.chat_models import ChatOpenAI
from langchain.schema import (
    AIMessage,
    HumanMessage,
    SystemMessage
)

from langchain.prompts import PromptTemplate

from langchain.prompts.chat import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
    MessagesPlaceholder
)

from langchain.chains import LLMChain
from langchain.agents import AgentType, initialize_agent, load_tools
from langchain.memory import ConversationBufferMemory

load_dotenv()

scenario_one = {"persona": "Kanye West",
                "confidence": "Unshakeable",
                "attitude": "Smitten",
                "number_of_opponents": 2,
                "opponent_positions": ["Jeff has $1000 to your left", "Halh has $900 to your right"],
                "position": "small blind",
                "player_money": 800,
                "current_situation": "The hole cards have just been dealt",
                "hole_cards": "A of Clubs and J of Spades",
                "current_pot": 30,
                "player_options": "call, raise, fold",
                }

persona = "Kanye West"
confidence = "Unshakeable"
attitude = "Smitten"
number_of_opponents = 2
position = "small blind"
player_money = 980
current_situation = "The hole cards were dealt. Hal bet 100, Alice bet 99."
hole_cards = "K of Diamonds and 5 of Clubs"
current_pot = 369
player_options = "call, raise, fold"

sample_string = (
    f"""
        Persona: {scenario_one["persona"]}
        Attitude: {scenario_one["attitude"]}
        Confidence: {scenario_one["confidence"]}
        Opponents: {scenario_one["opponent_positions"]}
                            
        You are {persona} playing a round of Texas Hold em with {number_of_opponents} other people.
        You are {position} and have ${player_money} in chips remaining. {current_situation},
        you have {hole_cards} in your hand. The current pot is ${current_pot}.
        Your options are: {player_options}
        Feel free to express yourself physically using *things i'm doing* to indicate actions like *looks around table*.
        Don't over do this though, you are playing poker and you don't want to give anything away that would hurt your
        chances of winning. You should respond with a JSON containing your action, your bet (if applicable), any comments
        or things you want to say to the table, any pysical movements you make at the table, and your inner monologue

        Sample response:
        {{
            action: <enter the action you're going to take here>
            amount: <enter the dollar amount to bet here>
            comment: <enter what you want to say here>
            inner_monologue: <enter your internal thoughts here, these won't be shared with the others at the table>
            persona_response: <based on your attitude, confidence, and who you are, provide a unique response given the situation>
            physical: <enter a list of strings with the physical actions you take in the order you take them>               
        }}
        
        Remeber {persona}, you're feeling {attitude} and {confidence}.
        
        """)
        # it's ${amount_to_call} to you to call and cover the blind and $20 to bet. Would you like to call or fold?

# print(sample_string)

poker_prompt = ChatPromptTemplate.from_messages([
    SystemMessagePromptTemplate.from_template(template=sample_string),
    MessagesPlaceholder(variable_name="history"),
    HumanMessagePromptTemplate.from_template("{input}")
])

player = ChatOpenAI(temperature=.5)

memory = ConversationBufferMemory(return_messages=True)

conversation = ConversationChain(memory=memory, prompt=poker_prompt, llm=player)


player_response = conversation.predict(input="What is your move?")
print(player_response)
