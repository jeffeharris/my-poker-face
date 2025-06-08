import { createDeck, shuffleDeck } from '../../utils/cards';
import { Card } from '../cards';
import './Card.css';

export function CardDemo() {
  const deck = createDeck();
  const shuffled = shuffleDeck(deck).slice(0, 13); // Show first 13 cards

  return (
    <div style={{ padding: '20px', backgroundColor: '#0d5016' }}>
      <h2 style={{ color: 'white', textAlign: 'center' }}>Complete Card Deck Demo</h2>
      
      <div style={{ marginBottom: '30px' }}>
        <h3 style={{ color: 'white' }}>Sample Cards (shuffled)</h3>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'center' }}>
          {shuffled.map((card, i) => (
            <Card key={i} card={card} size="medium" />
          ))}
        </div>
      </div>

      <div style={{ marginBottom: '30px' }}>
        <h3 style={{ color: 'white' }}>All Spades</h3>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'center' }}>
          {deck.filter(card => card.suit === 'spades').map((card, i) => (
            <Card key={i} card={card} size="medium" />
          ))}
        </div>
      </div>

      <div style={{ marginBottom: '30px' }}>
        <h3 style={{ color: 'white' }}>All Hearts</h3>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'center' }}>
          {deck.filter(card => card.suit === 'hearts').map((card, i) => (
            <Card key={i} card={card} size="medium" />
          ))}
        </div>
      </div>

      <div style={{ marginBottom: '30px' }}>
        <h3 style={{ color: 'white' }}>All Diamonds</h3>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'center' }}>
          {deck.filter(card => card.suit === 'diamonds').map((card, i) => (
            <Card key={i} card={card} size="medium" />
          ))}
        </div>
      </div>

      <div style={{ marginBottom: '30px' }}>
        <h3 style={{ color: 'white' }}>All Clubs</h3>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'center' }}>
          {deck.filter(card => card.suit === 'clubs').map((card, i) => (
            <Card key={i} card={card} size="medium" />
          ))}
        </div>
      </div>

      <div style={{ marginBottom: '30px' }}>
        <h3 style={{ color: 'white' }}>Card Sizes</h3>
        <div style={{ display: 'flex', gap: '20px', justifyContent: 'center', alignItems: 'end' }}>
          <div style={{ textAlign: 'center' }}>
            <Card card={deck[0]} size="small" />
            <p style={{ color: 'white', margin: '5px 0' }}>Small</p>
          </div>
          <div style={{ textAlign: 'center' }}>
            <Card card={deck[0]} size="medium" />
            <p style={{ color: 'white', margin: '5px 0' }}>Medium</p>
          </div>
          <div style={{ textAlign: 'center' }}>
            <Card card={deck[0]} size="large" />
            <p style={{ color: 'white', margin: '5px 0' }}>Large</p>
          </div>
        </div>
      </div>

      <div style={{ marginBottom: '30px' }}>
        <h3 style={{ color: 'white' }}>Face Down Cards</h3>
        <div style={{ display: 'flex', gap: '8px', justifyContent: 'center' }}>
          <Card faceDown={true} size="small" />
          <Card faceDown={true} size="medium" />
          <Card faceDown={true} size="large" />
        </div>
      </div>
    </div>
  );
}