import streamlit as st
import random

from game import StreamlitInterface
from poker import (PokerGame,
                   get_players)

from dotenv import load_dotenv

load_dotenv()


def display_player(player):
    pass


def display_game(game: PokerGame):
    for player in game.players:
        display_player(player)


def main():
    st.title("My Poker Face")
    if st.button("Start Game"):
        players = get_players(test=False, num_players=3)
        poker_game = PokerGame(players, StreamlitInterface())
        poker_game.set_dealer(players[random.randint(0, len(players) - 1)])

        poker_game.play_hand()


if __name__ == "__main__":
    main()
