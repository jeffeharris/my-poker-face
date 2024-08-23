from typing import Dict, List

from core.poker_player import PokerPlayer


class PokerHandPot:
    player_pot_amounts: Dict[PokerPlayer, int]
    pot_winner: PokerPlayer or None

    def __init__(self, poker_players: List[PokerPlayer], player_pot_amounts = None, pot_winner = None):
        self.player_pot_amounts = player_pot_amounts or self._initialize_pot_amounts(poker_players)
        self.pot_winner = pot_winner

    @staticmethod
    def _initialize_pot_amounts(poker_players: List[PokerPlayer]) -> Dict[PokerPlayer, int]:
        return {player: 0 for player in poker_players}

    def to_dict(self) -> Dict:
        return {
            'player_pot_amounts': self._player_pot_amounts_dict(),
            'pot_winner': self.pot_winner.name if self.pot_winner else None
        }

    def _player_pot_amounts_dict(self) -> Dict:
        return {player.to_dict(): amount for player, amount in self.player_pot_amounts.items()}

    @classmethod
    def from_dict(cls, d: Dict):
        # TODO: determine if from_dict is needed, build out if so. may need to change the key in the dict
        pass

    @property
    def total(self) -> int:
        return sum(self.player_pot_amounts.values())

    @property
    def current_bet(self) -> int:
        return max(self.player_pot_amounts.values())

    def get_player_pot_amount(self, player: PokerPlayer) -> int:
        return self.player_pot_amounts[player]

    def get_player_cost_to_call(self, player: PokerPlayer) -> int:
        player_contributed = self.get_player_pot_amount(player)
        return self.current_bet - player_contributed

    def add_to_pot(self, player: PokerPlayer, amount: int) -> None:
        player.get_for_pot(amount)
        self.player_pot_amounts[player] += amount

    def resolve_pot(self, pot_winner: PokerPlayer) -> None:
        pot_winner.money += self.total
        self.pot_winner = pot_winner
