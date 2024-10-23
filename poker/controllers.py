import json

from old_files.poker_player import AIPokerPlayer
from ui_console import prepare_ui_data, human_player_action, ai_player_action


class PlayerController:
    def __init__(self, player):
        self.player = player

    def decide_action(self, game_state):
        raise NotImplementedError("Must implement decide_action method.")


class HumanPlayerController(PlayerController):
    def decide_action(self, game_state):
        ui_data, player_options = prepare_ui_data(game_state)
        return human_player_action(ui_data, player_options)


class AIPlayerController(PlayerController):
    def __init__(self, player, ai_temp=0.9):
        super().__init__(player)
        self.ai_temp = ai_temp
        self.assistant = AIPokerPlayer(player.name, starting_money=player.stack, ai_temp=ai_temp).assistant

    def decide_action(self, game_state):
        current_player = game_state.current_player
        message = json.dumps(prepare_ui_data(game_state))
        response_json = self.assistant.chat(
            message + "\nPlease only respond with the JSON, not the text with back quotes.")
        try:
            response_dict = json.loads(response_json)
            if not all(key in response_dict for key in ('action', 'adding_to_pot', 'persona_response', 'physical')):
                raise ValueError("AI response is missing required keys.")
        except json.JSONDecodeError as e:
            raise ValueError("What happened here")

        # print(response_json)
        player_choice = response_dict['action']
        amount = response_dict['adding_to_pot']
        player_message = response_dict['persona_response']
        player_physical_description = response_dict['physical']
        print(f"\n{'-' * 20}\n")
        print(f"{current_player.name} chose to {player_choice} by {amount}")
        print(f"\"{player_message}\"")
        print(f"{player_physical_description}")
        print(f"\n{'-' * 20}\n")

        return player_choice, amount
