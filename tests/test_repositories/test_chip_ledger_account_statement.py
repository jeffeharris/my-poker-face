"""`entries_for_account` — the player ledger statement backing GET /api/cash/ledger
(#4). Verifies signed-amount direction (receive +, pay −), newest-first order, the
running-total == balance_of invariant, and cross-sandbox spanning."""


def test_entries_for_account_signs_order_and_running_balance(repos):
    r = repos['chip_ledger_repo']
    acct = 'player:tester'
    # A cash session: seed in, buy-in out, cash-out in (the player-side flows).
    r.record('central_bank', acct, 1000, 'player_seed', sandbox_id='s1')
    r.record(acct, 'seat:g1', 400, 'player_buy_in', sandbox_id='s1')
    r.record('seat:g1', acct, 650, 'player_cash_out', sandbox_id='s1')

    entries = r.entries_for_account(acct, sandbox_id=None)

    # Newest first.
    assert [e['reason'] for e in entries] == [
        'player_cash_out',
        'player_buy_in',
        'player_seed',
    ]
    # Signed: +amount when the account receives (sink), −amount when it pays (source).
    signed = {e['reason']: e['signed_amount'] for e in entries}
    assert signed == {'player_seed': 1000, 'player_buy_in': -400, 'player_cash_out': 650}
    # The running total of signed amounts equals the derived balance.
    assert sum(e['signed_amount'] for e in entries) == r.balance_of(acct, sandbox_id=None)
    assert r.balance_of(acct, sandbox_id=None) == 1250


def test_entries_for_account_spans_sandboxes_but_can_scope(repos):
    r = repos['chip_ledger_repo']
    acct = 'player:tester'
    r.record('central_bank', acct, 100, 'player_seed', sandbox_id='s1')
    r.record('central_bank', acct, 200, 'player_seed', sandbox_id='s2')

    # Default (None) spans every sandbox — the human player account is global.
    assert len(r.entries_for_account(acct, sandbox_id=None)) == 2
    # An explicit sandbox scopes to that save file only.
    scoped = r.entries_for_account(acct, sandbox_id='s1')
    assert len(scoped) == 1 and scoped[0]['signed_amount'] == 100
