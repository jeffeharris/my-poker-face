import streamlit as st
import random

from cards import render_cards
from game import StreamlitInterface
from poker import (PokerGame,
                   get_players)

from dotenv import load_dotenv

load_dotenv()


def display_player(player):
    st.text(player.player_state)


def display_game(game: PokerGame):
    # display game situation
    pot_total_display = st.metric("Pot Total", game.pot)
    # cost_to_call_display = st.metric("Cost to Call", game.cost_to_call)
    dealer_display = st.text(f"Dealer: {game.dealer}")
    if game.current_round != "pre-flop":
        st.text(render_cards(game.community_cards))
    for player in game.players:
        display_player(player)


def simple_app():
    st.title("My Poker Face")
    if not st.button("Start Game"):
        st.stop()
    else:
        players = get_players(test=False, num_players=3)
        poker_game = PokerGame(players, StreamlitInterface())
        poker_game.set_dealer(players[random.randint(0, len(players) - 1)])
        display_game(poker_game)
        poker_game.play_hand()


def main():
    simple_app()


if __name__ == "__main__":
    main()
