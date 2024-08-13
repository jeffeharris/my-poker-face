# flask_app/app.py
from flask import Flask, render_template
import random
# from core.game import FlaskInterface
from core.poker import (PokerGame,
                        PokerAction,
                        PokerHand,
                        PokerPlayer,
                        AIPokerPlayer,
                        get_players,
                        shift_list_left)

from dotenv import load_dotenv

app = Flask(__name__)


@app.route('/')
def home():
    return render_template('index.html')


if __name__ == '__main__':
    app.run(debug=True)