## This is just a test python file, not using it for the actual website

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

'''
llm = OpenAI(temperature=0.9)
prompt = PromptTemplate.from_template("What is a good name for a company that makes {product}?")
chain = LLMChain(llm=llm, prompt=prompt)
print(chain.run("colorful socks"))

# print(llm.predict("What is a good name for a mountain biking apparel company?"))

chat = ChatOpenAI(temperature=0)

template = "You are a helpful assistant that translates {input_language} to {output_language}."
system_message_prompt = SystemMessagePromptTemplate.from_template(template)
human_template = "{text}"
human_message_prompt = HumanMessagePromptTemplate.from_template(human_template)
chat_prompt = ChatPromptTemplate.from_messages([system_message_prompt, human_message_prompt])

chain = LLMChain(llm=chat, prompt=chat_prompt)
print(chain.run(input_language="English", output_language="Spanish",
                text="I used to be a good sailor"))
# >> AIMessage(content="J'aime programmer.", additional_kwargs={})
'''

'''
llm = OpenAI(temperature=0.9)
products = ["Leather laptop bag", "Bluetooth noise-canceling headphones", "Eco-friendly yoga mat",
            "Stainless steel water bottle", "Organic cotton bed sheets", "Vegan protein powder",
            "Handcrafted wooden chess set", "Aromatherapy essential oil diffuser", "Smart home security system",
            "Luxury bath towel set"]
prompt = PromptTemplate.from_template("What is a good name for a company that makes {product}?")

for product in products:
    final_prompt = prompt.format(product=product)
    print(llm.predict(final_prompt))
'''

'''
template = "You are a helpful assistant that translates {input_language} to {output_language}."
system_message_prompt = SystemMessagePromptTemplate.from_template(template)
human_template = "{text}"
human_message_prompt = HumanMessagePromptTemplate.from_template(human_template)

chat = ChatOpenAI(temperature=0)
chat_prompt = ChatPromptTemplate.from_messages([system_message_prompt, human_message_prompt])
languages = ["English", "Spanish", "French", "German", "Mandarin"]

for language in languages:
    final_prompt = chat_prompt.format_messages(input_language="English", output_language=language, text="I love programming.")
    print(chat.predict_messages(final_prompt).content)
'''
'''
# The language model we're going to use to control the agent.
llm = OpenAI(temperature=0)
conversation = ConversationChain(llm=llm, verbose=True)
print(conversation.run("Hi there!"))
print(conversation.run("I'm doing well! Just having a conversation with an AI."))

# The tools we'll give the Agent access to. Note that the 'llm-math' tool uses an LLM, so we need to pass that in.
tools = load_tools(["serpapi", "llm-math"], llm=llm)

# Finally, let's initialize an agent with the tools, the language model, and the type of agent we want to use.
agent = initialize_agent(tools, llm, agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION, verbose=True)

# Let's test it out!
print(agent.run("What was the high temperature in Atlanta over the last month? How does it compare to the average high temperature for the same time period over the last 20 years? What are your interpretations of this data?"))
'''

serious_prompt = ChatPromptTemplate.from_messages([
    SystemMessagePromptTemplate.from_template(
        "You are collaborative and communicate efficiently. You are a human working with a creative AI to answer the question they have received from another human. The creative AI is going to present some ideas to you and you should evaluate them and work with the creative AI to find a solution that they can present back to the other human. Ask for clarification and don't make any assumptions without confirming them. Challenge your colleague's ideas until you're satisfied. You should continue to work together until either of you decide that it is time to stop."
    ),
    MessagesPlaceholder(variable_name="history"),
    HumanMessagePromptTemplate.from_template("{input}")
])

curious_prompt = ChatPromptTemplate.from_messages([
    SystemMessagePromptTemplate.from_template(
        "You are collaborative and communicate efficiently. You will get the question from the human 1 and begin by coming up with some quick divergent ideas. This first pass should be about casting a wide net. After sharing them with your colleague, another human, you will debate the ideas with your colleague until you come to an agreement on the best one to present back to the user. You should continue to work together until either of you decide that it is time to stop."
    ),
    MessagesPlaceholder(variable_name="history"),
    HumanMessagePromptTemplate.from_template("{input}")
])

serious = ChatOpenAI(temperature=.1)
curious = ChatOpenAI(temperature=.9)

serious_memory = ConversationBufferMemory(return_messages=True)
curious_memory = ConversationBufferMemory(return_messages=True)

serious_conversation = ConversationChain(memory=serious_memory, prompt=serious_prompt, llm=serious)
curious_conversation = ConversationChain(memory=curious_memory, prompt=curious_prompt, llm=curious)


curious_response = curious_conversation.predict(input="The human has asked you for suggestions on how to stack the following items: a book, a nail, 9 eggs, and a highlighter. They should be stacked in a way that they would remain as stable as possible. Visual appeal does not matter")
print(curious_response)
serious_response = serious_conversation.predict(input="The human has asked you for suggestions on how to stack the following items: a book, a nail, 9 eggs, and a highlighter. They should be stacked in a way that they would remain as stable as possible. Visual appeal does not matter")
print(serious_response)

i = 3
while i > 0:
    serious_response = serious_conversation.predict(input=curious_response)
    curious_response = curious_conversation.predict(input=serious_response)
    i = i - 1

print(serious_response)
print(curious_response)
