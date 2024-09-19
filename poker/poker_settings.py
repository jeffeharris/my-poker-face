class PokerSettings:
    all_in_allowed: bool
    starting_small_blind: int
    player_starting_money: int
    ai_player_starting_money: int

    def __init__(self, small_blind=10, player_money=1000, ai_money=1000, all_in=True):
        self.starting_small_blind = small_blind
        self.player_starting_money = player_money
        self.ai_player_starting_money = ai_money
        self.all_in_allowed = all_in

    def to_dict(self):
        return {
            'starting_small_blind': self.starting_small_blind,
            'player_starting_money': self.player_starting_money,
            'ai_player_starting_money': self.ai_player_starting_money,
            'all_in_allowed': self.all_in_allowed
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(**data)
