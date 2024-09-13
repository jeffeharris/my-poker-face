# Spades Game (Flask Web App)

## Overview

This project implements a web-based Spades game using Python's Flask framework. Players can play against three CPU opponents in a classic game of Spades, with support for features like **Nil**, **Blind Nil**, and the ability to **Shoot the Moon** (Boston). The game follows the traditional rules of Spades, with some additional logic for player and CPU decision-making.

## Game Rules and Logic

### 1. **Basic Gameplay**

- The game follows the standard rules of Spades:
  - Players are divided into two teams:
    - **Team 1:** Player and CPU2
    - **Team 2:** CPU1 and CPU3
  - Each player is dealt 13 cards, and each player must bid the number of tricks they expect to win.
  - The suit of **Spades** is the trump suit, and it cannot be played until "broken" (i.e., until a player cannot follow suit and plays a Spade).
  - The player with the **2 of Clubs** starts the game, and play progresses in a clockwise fashion.
  - The player who wins the trick leads the next trick.

### 2. **Bidding Phase**

- Players and CPUs bid the number of tricks they expect to take in the round. Players can also bid **Nil** (expecting to win zero tricks) or **Blind Nil** (expecting to win zero tricks without seeing their cards first).
- If a player bids **Blind Nil**, they do not see their cards until after the bidding phase, and they must avoid winning any tricks.
- CPU opponents use basic logic to decide their bids, with a chance to bid **Nil** or **Blind Nil** based on their current score (e.g., CPUs may attempt Blind Nil if their team is significantly behind).
- Each team's bid is the sum of the bids of its members.

### 3. **Playing Phase**

- The player with the **2 of Clubs** starts the first trick.
- Players must follow the suit of the leading card, if possible. If they cannot follow suit, they may play any card, including Spades (which may break the suit).
- The trick is won by the highest-ranking card in the leading suit, unless a Spade has been played, in which case the highest-ranking Spade wins.
- The player who wins the trick leads the next round.
- **Spades are broken** when a Spade is played in a trick, allowing Spades to be played as the leading suit in future tricks.

### 4. **Special Bids**

- **Nil Bid:** A player bids that they will not win any tricks. If successful, the player earns a bonus (e.g., 100 points). If they win any tricks, they incur a penalty (e.g., -100 points).
- **Blind Nil Bid:** A player declares that they will not win any tricks without seeing their hand. This provides a higher bonus (e.g., 200 points) but also a higher penalty for failure (e.g., -200 points).
- **Shooting the Moon (Boston):** If a player or team wins all 13 tricks, they receive a special bonus (e.g., 250 points). This is a rare and difficult achievement.

### 5. **End of Round**

- After 13 tricks are played, the round ends and scores are calculated:
  - Teams receive points based on whether they made or exceeded their bids.
  - Points are awarded for successful Nil and Blind Nil bids, and penalties are incurred for failed Nil/Blind Nil bids.
  - **Boston**: If a team wins all 13 tricks, they are awarded a special bonus, and the opposing team loses points.

### 6. **Scoring**

- For each round:
  - A team's score is based on whether they met their combined bid.
  - For each trick won above the bid, a small bonus (usually 1 point) is awarded (optional).
  - Failed Nil/Blind Nil bids result in penalties.
  - Shooting the Moon (winning all 13 tricks) results in a large bonus.
- The game continues for multiple rounds until one team reaches a certain score (usually 500 points).

## Key Decisions in Game Logic

### 1. **Bid Decision-Making**
- Players can bid their expected number of tricks, or choose Nil/Blind Nil.
- The CPU bidding is based on the number of high cards and Spades in their hand. There is a chance that CPUs will bid Nil or Blind Nil if conditions are met (e.g., being behind by more than 100 points).

### 2. **Handling of Spades Breaking**
- Players cannot lead with Spades until Spades are broken.
- If a player cannot follow suit, they may play a Spade, and this will break the suit, allowing Spades to be played in subsequent rounds.

### 3. **Blind Nil Logic**
- The player is prompted to decide whether they want to bid Blind Nil **before** seeing their cards. This is in line with traditional Spades rules.
- CPUs may also attempt Blind Nil if their team is significantly behind.

### 4. **Trick Evaluation**
- Tricks are evaluated based on the leading suit, with the highest card of the leading suit winning the trick unless a Spade is played. If a Spade is played, the highest Spade wins.

## Known Issues

1. **Basic CPU Strategy:**
   - The CPU logic for bidding and playing cards is relatively simple. It could be enhanced to account for more complex strategies, such as recognizing the importance of certain tricks, managing Nil bids better, and playing defensively when necessary.
   
2. **Limited Handling of Sandbags:**
   - Currently, sandbagging penalties (penalizing teams that win significantly more tricks than they bid) are not implemented. This is a common rule in Spades and could be added.

3. **No Game Over Logic:**
   - The game does not currently have a built-in "Game Over" state. While rounds can be played continuously, the game does not yet end at a specific score (e.g., 500 points). Adding this feature would enhance the realism of the game.

4. **Blind Nil Behavior:**
   - If a player successfully bids Blind Nil, they should not be shown their cards until after the round begins. However, the logic for "cheating" (e.g., players reviewing their hand) is not enforced.

5. **Scoring Display:**
   - The game could benefit from improved score tracking across multiple rounds, including displaying a detailed breakdown of points for each round.

6. **AI Improvements for Trick Decisions:**
   - The current AI does not make sophisticated decisions during the play phase. For example, it doesn’t always recognize the strategic importance of certain tricks, such as when to avoid winning a trick or when to force opponents to break suit.

## Recommended Improvements

1. **Enhance CPU Strategy:**
   - Implement more advanced CPU decision-making for both bidding and playing cards. For example, CPUs could track the remaining high cards and make better decisions about when to try to win a trick or avoid winning.

2. **Implement Sandbagging Penalties:**
   - Add penalties for teams that win significantly more tricks than they bid (often known as "bags"). This would add another layer of strategy, as players must carefully manage their bids and trick-taking.

3. **Add Game Over Condition:**
   - Implement a "Game Over" state, where the game ends once one team reaches a predefined score (e.g., 500 points). Display a "winner" message and provide an option to restart the game.

4. **Improve User Interface:**
   - Enhance the visual presentation of the game. Add more detailed round summaries, better tracking of tricks won, and clear displays of the current game state (e.g., whose turn it is, what the bids are).

5. **Expand Multiplayer Support:**
   - Consider adding real-time multiplayer support where multiple human players can play against each other over the web. Flask-SocketIO or a similar library could be used to handle real-time interactions.

6. **Add In-Game Chat (Optional):**
   - If multiplayer support is added, consider implementing an in-game chat system where players can send messages to each other.

7. **Add Custom Game Rules:**
   - Allow players to customize certain rules, such as the penalties for sandbags, the bonuses for Shooting the Moon, and the score needed to win.

---

## How to Run

### Prerequisites

- Python 3.x installed on your machine.
- Flask web framework (`pip install Flask`).

### Running the Game

1. Clone the repository to your local machine.
2. Install dependencies:
   ```bash
   pip install Flask
   ```
3. Run the game using: 
    ```bash
    python spades_game.py
    ```
4. Open your browser and go to `https://localhost:5000` to start the game.

## Conclusion
This Spades game offers a fun and interactive way to play the classic card game against AI opponents. While the game is fully playable, there are several areas for improvement, particularly in terms of CPU strategy, game state tracking, and UI enhancements. By addressing the known issues and implementing the recommended improvements, this game could become a more polished and engaging experience.