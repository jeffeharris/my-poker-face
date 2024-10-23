from old_files.poker_player import AIPokerPlayer
from ui_console import prepare_ui_data, human_player_action


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
        self.assistant = AIPokerPlayer(player['name'], starting_money=player['stack'], ai_temp=ai_temp).assistant

    def decide_action(self, game_state):
        return ai_player_action(game_state, self.assistant)
