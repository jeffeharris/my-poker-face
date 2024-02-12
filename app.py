import streamlit as st
import random

from game import StreamlitInterface
from poker import (PokerGame,
                   get_players,
                   shift_list_left,
                   render_cards)

from dotenv import load_dotenv

load_dotenv()


def display_player(player):
    pass


def display_game(game: PokerGame):
    for player in game.players:
        display_player(player)


def play_poker_hand(game: PokerGame):
    game.deck.shuffle()
    game.set_remaining_players()
    game.set_current_round("pre-flop")
    game.post_blinds()

    st.write(f"{game.dealer.name}'s deal.\n")
    st.write(f"Small blind: {game.small_blind_player.name}")
    st.write(f"Big blind: {game.big_blind_player.name}\n")
    # st.write(game.game_state)

    game.deal_hole_cards()

    start_player = game.determine_start_player()

    index = game.players.index(start_player)  # Set index at the start_player
    round_queue = game.players.copy()   # Copy list of all players that started the hand, could include folded
    shift_list_left(round_queue, index)     # Move to the start_player
    game.betting_round(round_queue)

    output_text, new_cards = game.reveal_cards(3, "flop")
    game.display_text(output_text)
    game.display_text(render_cards(new_cards))
    start_player = game.determine_start_player()
    index = game.players.index(start_player)
    round_queue = game.players.copy()   # Copy list of all players that started the hand, could include folded
    shift_list_left(round_queue, index)     # Move to the start_player
    game.betting_round(round_queue)

    output_text, new_cards = game.reveal_cards(1, "turn")
    game.display_text(output_text)
    game.betting_round(round_queue)

    output_text, new_cards = game.reveal_cards(1, "river")
    game.display_text(output_text)
    game.betting_round(round_queue)

    game.end_hand()
    # TODO: add return winner, game.pot


def main():
    st.title("My Poker Face")
    if st.button("Start Game"):
        players = get_players(test=False, num_players=2)
        game = PokerGame(players, StreamlitInterface())
        game.set_dealer(players[random.randint(0, len(players) - 1)])

        play_poker_hand(game)


if __name__ == "__main__":
    main()
