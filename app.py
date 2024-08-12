import streamlit as st
import random
from game import StreamlitInterface
from poker import (PokerGame,
                   PokerAction,
                   PokerHand,
                   PokerPlayer,
                   get_players, AIPokerPlayer)

from dotenv import load_dotenv

load_dotenv()


def display_player(player: PokerPlayer, st_container=None, position=None):
    if st_container is None:
        st_container = st.container(border=True)

    player_lines = [f"Name: {player.name}",
                    f"Chips: {player.money}"]

    if isinstance(player, AIPokerPlayer):
        player_lines.extend([f"Attitude: {player.attitude}",
                             f"Confidence: {player.confidence}"])
    player_text = "\n".join(player_lines)
    st_container.text(player_text)
    if position is not None:
        st_container.subheader(f"{position}")
    return st_container


def display_game(game: PokerGame):
    # display game situation
    # pot_total_display = st.metric("Pot Total", game.pot)

    st.header("Players:")
    cols = st.columns(len(game.players))
    i = 0
    for player in game.players:
        with cols[i]:
            display_player(player=player)
        i += 1


def display_poker_action(poker_action: PokerAction):
    action_container = st.container(border=True)
    action_container.header("Last Action")
    poker_action_lines = [f"Player: {poker_action.player.name}",
                          f"Action: {poker_action.player_action.value}"]
    if poker_action.amount:
        poker_action_lines.append(f"Amount: {poker_action.amount}")

    # join the poker_action_lines into a single string
    action_text = "\n".join(poker_action_lines)
    action_container.text(action_text)

    hand_state_expander = action_container.expander(label="Hand State", expanded=False)
    with hand_state_expander:
        st.write(poker_action.hand_state)
    action_detail_expander = action_container.expander(label="Action Detail", expanded=False)
    with action_detail_expander:
        st.write(poker_action.action_detail)

    return action_container


def simple_app():
    st.title("My Poker Face")
    if "is_game_running" not in st.session_state:
        st.session_state["is_game_running"] = False

    if not st.session_state["is_game_running"]:
        if not st.button("Start Game"):
            st.stop()
        else:
            st.session_state["is_game_running"] = True
            players = get_players(test=False, num_players=3)
            poker_game = PokerGame(players, StreamlitInterface())
            if "poker_game" not in st.session_state:
                st.session_state["poker_game"] = poker_game
            if "dealer" not in st.session_state:
                st.session_state["dealer"] = poker_game.players[random.randint(0, len(poker_game.players) - 1)]
    else:
        poker_game = st.session_state["poker_game"]

    for poker_hand in poker_game.hands:
        replay_hand(poker_hand)

    poker_hand = PokerHand(interface=poker_game.interface,
                           players=poker_game.players,
                           dealer=poker_game.players[random.randint(0, len(poker_game.players) - 1)],
                           deck=poker_game.deck)
    poker_game.hands.append(poker_hand)
    display_game(poker_game)

    poker_game.remaining_players, st.session_state["dealer"] = poker_hand.play_hand()

    play_again = poker_game.interface.request_action(
        ["yes", "no"],
        "Would you like to play another hand? ",
        0)
    if play_again != "yes":
        poker_game.display_text("Game over!")


def replay_hand(poker_hand: PokerHand) -> None:
    for action in poker_hand.poker_actions:
        display_poker_action(action)


def main():
    simple_app()
    # cols = st.columns(3)
    # poker_action = PokerAction(player=PokerPlayer("Jeff"),
    #                            action="bet",
    #                            amount=500,
    #                            hand_state="State",
    #                            action_detail="Details")
    # with cols[2]:
    #     my_container = display_poker_action(poker_action)


if __name__ == "__main__":
    main()
