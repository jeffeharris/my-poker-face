import unittest

from core.llm import Assistant
from poker.poker_player import PokerPlayer, AIPokerPlayer
from core.card import Card


class AIPokerPlayerTests(unittest.TestCase):

    def setUp(self):
        self.name = "AI Player"
        self.money = 20000
        self.obj = AIPokerPlayer(self.name, self.money)
        self.obj.cards = [Card(rank='A', suit='Hearts'), Card(rank='K', suit='Spades')]
        self.obj.options = ['fold', 'check', 'call', 'raise']
        self.obj.folded = False
        self.obj.confidence = "High"
        self.obj.attitude = "Friendly"
        self.obj.assistant = Assistant(system_prompt="You are a friendly assistant")

    def test_to_dict(self):
        result = self.obj.to_dict()
        self.assertEqual(dict, type(result))
        self.assertEqual(self.name, result['name'])
        self.assertEqual(self.money, result['money'])

    def test_from_dict(self):
        data = {
            'name': 'TestName',
            'money': 30000,
            'cards': [
                {'rank': 'A', 'suit': 'Hearts'},
                {'rank': 'K', 'suit': 'Spades'}
            ],
            'options': ['fold', 'check', 'call', 'raise'],
            'folded': False,
            'confidence': "High",
            'attitude': "Friendly",
            'assistant': {
                'system_prompt': "You are a friendly assistant",
                'memory': {'system_prompt': "You are a friendly assistant", 'messages': []}
            }
        }
        result = AIPokerPlayer.from_dict(data)
        self.assertEqual('TestName', result.name)
        self.assertEqual(30000, result.money)
        self.assertEqual([Card(rank='A', suit='Hearts'), Card(rank='K', suit='Spades')], result.cards)
        self.assertEqual(['fold', 'check', 'call', 'raise'], result.options)
        self.assertEqual(False, result.folded)
        self.assertEqual("High", result.confidence)
        self.assertEqual("Friendly", result.attitude)
        self.assertEqual("You are a friendly assistant", result.assistant.system_message)

    def test_player_state(self):
        result = self.obj.player_state
        self.assertEqual(dict, type(result))

    def test_set_for_new_hand(self):
        self.obj.set_for_new_hand()
        # Check that the function didn't raise any error

    def test_initialize_attribute(self):
        attribute = 'confidence'
        self.obj.initialize_attribute(attribute=attribute)
        # Check that the function didn't raise any error

    def test_persona_prompt(self):
        result = self.obj.persona_prompt()
        self.assertEqual(str, type(result))

    # TODO: test_get_player_action and test_get_player_response require
    # mocking the AI assistant. These tests are skipped for now.
    # def test_get_player_action(self):
    #     hand_state = {"state": "initial"}
    #     self.obj.get_player_action(hand_state)
    #     # Check that the function didn't raise any error
    #
    # def test_get_player_response(self):
    #     hand_state = {"state": "initial"}
    #     response = self.obj.get_player_response(hand_state)
    #     self.assertEqual(dict, type(response))


class TestPokerPlayer(unittest.TestCase):
    def setUp(self):
        self.player = PokerPlayer("Test", 5000)
        self.player.cards = [Card(rank='A', suit='Hearts'), Card(rank='K', suit='Spades')]
        self.player.options = ['fold','call','raise','all-in']

    def test_init(self):
        self.assertEqual(self.player.name, "Test")
        self.assertEqual(self.player.money, 5000)
        self.assertEqual(self.player.cards, [Card(rank='A', suit='Hearts'), Card(rank='K', suit='Spades')])
        self.assertEqual(self.player.options, ['fold','call','raise','all-in'])
        self.assertEqual(self.player.folded, False)

    def test_to_dict(self):
        result = self.player.to_dict()
        self.assertEqual(result['type'], "PokerPlayer")
        self.assertEqual(result["name"], self.player.name)
        self.assertEqual(result["money"], self.player.money)
        self.assertEqual(result["cards"], [card.to_dict() for card in self.player.cards])
        self.assertEqual(result["options"], self.player.options)
        self.assertEqual(result["folded"], self.player.folded)

    def test_from_dict(self):
        cards = [
            { "rank": "A", "suit": "Hearts" },
            { "rank": "K", "suit": "Spades" }
        ]
        player_dict = {
            "name": "Test",
            "money": 5000,
            "cards": cards,
            "options": ['fold','call','raise','all-in'],
            "folded": False
        }
        self.player.from_dict(player_dict)
        self.assertEqual(self.player.name, "Test")
        self.assertEqual(self.player.money, 5000)
        self.assertEqual(self.player.cards, [Card(rank='A', suit='Hearts'), Card(rank='K', suit='Spades')])
        self.assertEqual(self.player.options, ['fold','call','raise','all-in'])
        self.assertEqual(self.player.folded, False)

    def test_player_state(self):
        result = self.player.player_state
        self.assertEqual(result["name"], self.player.name)
        self.assertEqual(result["player_money"], self.player.money)
        self.assertEqual(result["player_cards"], self.player.cards)
        self.assertEqual(result["player_options"], self.player.options)
        self.assertEqual(result["has_folded"], self.player.folded)

    def test_get_for_pot(self):
        self.player.get_for_pot(1000)
        self.assertEqual(self.player.money, 4000)

    def test_set_for_new_hand(self):
        self.player.cards = [Card(rank='A', suit='Hearts'), Card(rank='K', suit='Spades')]
        self.player.folded = True
        self.player.set_for_new_hand()
        self.assertEqual(self.player.cards, [])
        self.assertEqual(self.player.folded, False)


if __name__ == '__main__':
    unittest.main()
