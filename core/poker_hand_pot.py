from typing import Dict, List, Optional

# TODO: Remove bug where two players with the same name would have a clash. May require player UUID?
class PokerHandPot:
    player_pot_amounts: Dict[str, int]  # Use player names instead of PokerPlayer objects
    pot_winner: Optional[str]

    def __init__(self, player_pot_amounts: Optional[Dict[str, int]] = None,
                 pot_winner: Optional[str] = None):
        self.player_pot_amounts = player_pot_amounts or {}
        self.pot_winner = pot_winner

    @staticmethod
    def _initialize_pot_amounts(player_names: List[str]) -> Dict[str, int]:
        return {name: 0 for name in player_names}

    def initialize_pot(self, player_names: List[str]):
        self.player_pot_amounts = self._initialize_pot_amounts(player_names)

    def to_dict(self) -> Dict:
        return {
            'player_pot_amounts': self.player_pot_amounts,
            'pot_winner': self.pot_winner
        }

    @classmethod
    def from_dict(cls, d: Dict):
        return cls(
            player_names=list(d['player_pot_amounts'].keys()),
            player_pot_amounts=d['player_pot_amounts'],
            pot_winner=d['pot_winner']
        )

    @property
    def total(self) -> int:
        return sum(self.player_pot_amounts.values())

    @property
    def current_bet(self) -> int:
        return max(self.player_pot_amounts.values())

    def get_player_pot_amount(self, player_name: str) -> int:
        return self.player_pot_amounts[player_name]

    def get_player_cost_to_call(self, player_name: str) -> int:
        player_contributed = self.get_player_pot_amount(player_name)
        return self.current_bet - player_contributed

    def add_to_pot(self, player_name: str, player_money_func, amount: int) -> None:
        player_money_func(amount)
        self.player_pot_amounts[player_name] += amount

    def resolve_pot(self, pot_winner_name: str, update_winner_money_func) -> None:
        update_winner_money_func(self.total)
        self.pot_winner = pot_winner_name
